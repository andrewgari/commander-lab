"""
AN-7: card2vec "played-with" similarity (analytics/card2vec.py).

word2vec over real Commander decklists. The app only loads the artifact
(analytics/data/card2vec.npz); training is offline (scripts/train_card2vec.py).

    deck card score  = scale(cos(card, centroid of the OTHER deck cards))
    candidate score  = scale(cos(card, centroid of the deck))
    scale            = (sim - sim_floor) / (sim_ceil - sim_floor), clamped 0..1
Unknown cards (not in the model) get no score, never 0: the weak-performer
score and the tuner score just skip the part for them.

Run: python -m pytest tests/test_card2vec.py
"""
import json
import os
import sys
import tempfile
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics import card2vec
from analytics import tuner
from analytics import weak_performers as weak

ARTIFACT = card2vec.DEFAULT_PATH


def _toy(path):
    """3 clusters in 4-d: aggro (x), control (y), lands-ish staples (z)."""
    rows = {
        "Sol Ring": [0.2, 0.2, 1.0, 0.0], "Arcane Signet": [0.2, 0.2, 0.98, 0.05],
        "Goblin Guide": [1.0, 0.0, 0.1, 0.0], "Monastery Swiftspear": [0.97, 0.05, 0.1, 0.0],
        "Lightning Bolt": [0.95, 0.1, 0.15, 0.0], "Fury of the Horde": [0.9, 0.0, 0.1, 0.2],
        "Counterspell": [0.0, 1.0, 0.1, 0.0], "Rhystic Study": [0.05, 0.97, 0.15, 0.0],
        "Brazen Borrower // Petty Theft": [0.1, 0.9, 0.1, 0.1],
    }
    keys = [card2vec.name_key(n) for n in rows]
    v = np.array(list(rows.values()), dtype=np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    meta = {"model_id": "toy", "decks": 3, "vocab": len(keys), "dim": 4,
            "calibration": {"sim_floor": 0.3, "sim_ceil": 0.95}, "sources": []}
    np.savez_compressed(path, keys=np.array(keys), names=np.array(list(rows)), vectors=v.astype(np.float16),
                        counts=np.arange(len(keys), dtype=np.int32) + 1, meta=np.array(json.dumps(meta)))


class TestToyModel(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "toy.npz")
        _toy(cls.path)
        cls.m = card2vec.Card2Vec(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_loads_and_reports(self):
        self.assertTrue(self.m.available())
        info = self.m.info()
        self.assertEqual((info["status"], info["vocab"], info["dim"]), ("ok", 9, 4))

    def test_name_key_matches_front_face(self):
        self.assertTrue(self.m.has("Brazen Borrower"))
        self.assertTrue(self.m.has("brazen borrower // petty theft"))
        self.assertTrue(self.m.has("Brazen Borrower / Petty Theft"))   # single-slash spelling
        self.assertFalse(self.m.has("Not A Card"))

    def test_neighbours_are_the_same_cluster(self):
        nb = [x["name"] for x in self.m.neighbours("Goblin Guide", 3)]
        self.assertEqual(set(nb), {"Monastery Swiftspear", "Lightning Bolt", "Fury of the Horde"})
        self.assertNotIn("Goblin Guide", nb)

    def test_scale_clamps_with_calibration(self):
        self.assertEqual(self.m.scale(0.3), 0.0)
        self.assertEqual(self.m.scale(0.95), 1.0)
        self.assertEqual(self.m.scale(2.0), 1.0)
        self.assertAlmostEqual(self.m.scale(0.625), 0.5, places=3)
        self.assertIsNone(self.m.scale(None))

    def test_deck_scores_leave_one_out_and_candidates(self):
        deck = ["Goblin Guide", "Monastery Swiftspear", "Lightning Bolt", "Fury of the Horde", "Sol Ring",
                "Counterspell", "Mountain", "Unknown Jank"]
        out = self.m.deck_scores(deck, ["Arcane Signet", "Rhystic Study", "Nope"])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(out["known"], 6)  # basics and unknown cards are left out
        self.assertNotIn("Mountain", out["deck"])
        self.assertNotIn("Unknown Jank", out["deck"])
        # The off-plan blue card is the least played-with in a red aggro deck.
        least = min(out["deck"], key=lambda n: out["deck"][n]["sim"])
        self.assertEqual(least, "Counterspell")
        self.assertGreater(out["candidates"]["Arcane Signet"]["sim"], out["candidates"]["Rhystic Study"]["sim"])
        self.assertNotIn("Nope", out["candidates"])

    def test_too_few_known_cards(self):
        self.assertEqual(self.m.deck_scores(["Sol Ring", "Nope"])["status"], "too_few")

    def test_deck_view(self):
        deck = ["Goblin Guide", "Monastery Swiftspear", "Lightning Bolt", "Fury of the Horde", "Counterspell",
                "Sol Ring", "Mystery Card"]
        v = self.m.deck_view(deck, exclude_lands=["Sol Ring"], top=2)
        self.assertEqual(v["status"], "ok")
        self.assertEqual((v["known"], v["total"]), (5, 6))
        self.assertEqual(v["unknown"], ["Mystery Card"])
        self.assertEqual(v["least"][0]["name"], "Counterspell")
        self.assertEqual(len(v["most"]), 2)

    def test_missing_artifact_is_unavailable_not_an_error(self):
        m = card2vec.Card2Vec(os.path.join(self.tmp.name, "nope.npz"))
        self.assertFalse(m.available())
        self.assertIn("no card2vec artifact", m.error)
        self.assertEqual(m.deck_scores(["Sol Ring"])["status"], "unavailable")
        self.assertEqual(m.deck_view(["Sol Ring"])["status"], "unavailable")
        self.assertEqual(m.neighbours("Sol Ring"), [])


class TestScoresUseThePlayedPart(unittest.TestCase):
    def test_weak_parts_adds_played_only_when_known(self):
        base = weak.weak_parts(0, 4.0, 0.5, 0.25, True, True)
        self.assertNotIn("played", base)
        self.assertAlmostEqual(base["score"], (1.0 + 0.5 + 0.5) / 3, places=3)
        known = weak.weak_parts(0, 4.0, 0.5, 0.25, True, True, played=0.2)
        self.assertEqual(known["played"], 0.8)
        self.assertAlmostEqual(known["score"], (1.0 + 0.5 + 0.5 + 0.8) / 4, places=3)

    def test_compute_weak_performers_passes_played_through(self):
        deck = {"name": "D", "commanders": ["Cmd"], "cards": [{"name": n, "quantity": 1} for n in ("A", "B", "Cmd")]}
        graph = {"nodes": [{"name": "A"}, {"name": "B"}, {"name": "Cmd"}], "edges": []}
        fit = {"status": "ok", "per_card": [{"name": "A", "score": 0.5, "bucket": "generic"},
                                            {"name": "B", "score": 0.5, "bucket": "generic"}]}
        inc = {"status": "ok", "per_card": [{"name": "A", "inclusion": 0.2, "listed": True},
                                            {"name": "B", "inclusion": 0.2, "listed": True}]}
        played = {"status": "ok", "deck": {"A": {"sim": 0.4, "score": 0.1}, "B": {"sim": 0.8, "score": 0.9},
                                           "C": {"sim": 0.6, "score": 0.5}}}
        deck["cards"].append({"name": "C", "quantity": 1})
        deck["cards"].append({"name": "D", "quantity": 1})
        graph["nodes"] += [{"name": "C"}, {"name": "D"}]
        for n in ("C", "D"):
            fit["per_card"].append({"name": n, "score": 0.5, "bucket": "generic"})
            inc["per_card"].append({"name": n, "inclusion": 0.2, "listed": True})
        out = weak.compute_weak_performers(deck, graph, fit, inc, played=played)
        rows = {r["name"]: r for r in out["cards"]}
        self.assertEqual(rows["A"]["played"], 0.1)
        self.assertIsNone(rows["D"]["played"])          # the model doesn't know D...
        # ...but D is still scored over the same 4 parts, with the deck's median (neutral) played score.
        self.assertEqual(out["weights"]["played_neutral"], 0.5)
        self.assertEqual(rows["D"]["parts"]["played"], 0.5)
        self.assertEqual(rows["D"]["weak_score"], rows["C"]["weak_score"])
        self.assertIn("rarely played with the rest", rows["A"]["reasons"])
        self.assertNotIn("rarely played with the rest", rows["D"]["reasons"])
        self.assertGreater(rows["A"]["weak_score"], rows["D"]["weak_score"])
        self.assertGreater(rows["D"]["weak_score"], rows["B"]["weak_score"])
        self.assertTrue(out["inputs"]["played"])
        self.assertEqual(out["weights"]["parts"], ["edges", "pillar", "edhrec", "played"])

    def test_without_the_model_nothing_changes(self):
        deck = {"name": "D", "commanders": [], "cards": [{"name": "A", "quantity": 1}]}
        graph = {"nodes": [{"name": "A"}], "edges": []}
        a = weak.compute_weak_performers(deck, graph, {"status": "none"}, {"status": "unavailable"})
        b = weak.compute_weak_performers(deck, graph, {"status": "none"}, {"status": "unavailable"},
                                         played={"status": "unavailable"})
        self.assertEqual(a["cards"], b["cards"])
        self.assertIsNone(b["cards"][0]["played"])
        self.assertNotIn("played", b["cards"][0]["parts"])
        self.assertFalse(b["inputs"]["played"])

    def test_tuner_score_mean_includes_played(self):
        self.assertEqual(tuner.score(4, 4.0, 0.5, 0.25, True, True), round((1 + 0.5 + 0.5) / 3, 4))
        self.assertEqual(tuner.score(4, 4.0, 0.5, 0.25, True, True, played=0.9), round((1 + 0.5 + 0.5 + 0.9) / 4, 4))

    def test_tuner_compares_cut_and_candidate_over_the_same_parts(self):
        """A known cut with a low played score must not make an UNKNOWN candidate look
        like an upgrade by denominator alone: the unknown candidate gets the neutral."""
        from tests.test_tuner import CAPS, COMP, DECK, cand, weak_row

        cut = weak_row("Weak Plan", 0.6, 2, 0.5, 0.25)
        cut["played"] = 0.1
        wk = {"cards": [cut], "pet_notice": None, "weights": {"p75_degree": 4.0, "played_neutral": 0.5},
              "inputs": {"pillars": True, "edhrec": True, "played": True}}
        same_but_unknown = cand("Unknown Twin", 2, 0.5, 0.25, owned=True, available=1)
        out = tuner.build_tune(DECK, wk, COMP, [same_but_unknown], CAPS, p75=4.0, has_pillars=True,
                               has_edhrec=True)
        # cut: (0.5+0.5+0.5+0.1)/4 = 0.40; twin: (0.5+0.5+0.5+0.5)/4 = 0.50 -> a real (played) upgrade only.
        self.assertEqual(out["swaps"][0]["cut"]["score"], 0.4)
        self.assertEqual(out["swaps"][0]["own"]["score"], 0.5)
        self.assertTrue(out["inputs"]["played"])
        # With an identical played score the twin is NOT an upgrade (same parts, same mean).
        cut["played"] = 0.5
        out = tuner.build_tune(DECK, wk, COMP, [same_but_unknown], CAPS, p75=4.0, has_pillars=True,
                               has_edhrec=True)
        self.assertEqual(out["swaps"], [])


@unittest.skipUnless(os.path.exists(ARTIFACT), "shipped artifact not present")
class TestShippedArtifact(unittest.TestCase):
    """Sanity checks on the real model: well-known cards' neighbours make sense."""

    @classmethod
    def setUpClass(cls):
        cls.m = card2vec.Card2Vec(ARTIFACT)

    def test_meta(self):
        info = self.m.info()
        self.assertEqual(info["status"], "ok")
        self.assertGreater(info["decks"], 5000)
        self.assertGreater(info["vocab"], 3000)
        cal = info["calibration"]
        self.assertLess(cal["sim_floor"], cal["sim_ceil"])

    def _near(self, card, n=15):
        return {x["name"] for x in self.m.neighbours(card, n)}

    def test_fast_mana_clusters(self):
        self.assertTrue({"Mana Vault", "Chrome Mox", "Mox Diamond"} & self._near("Sol Ring"))

    def test_blue_interaction_clusters(self):
        self.assertTrue({"Mystic Remora", "Force of Will", "Fierce Guardianship"} & self._near("Rhystic Study"))

    def test_aristocrats_cluster(self):
        self.assertTrue({"Zulaport Cutthroat", "Viscera Seer"} & self._near("Blood Artist"))

    def test_green_ramp_clusters(self):
        self.assertTrue({"Kodama's Reach", "Rampant Growth", "Farseek"} & self._near("Cultivate"))

    def test_holy_hellfire_commander_known(self):
        self.assertTrue(self.m.has("Kaalia of the Vast"))


if __name__ == "__main__":
    unittest.main()

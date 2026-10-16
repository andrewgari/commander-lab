"""AN-8: "mechanically similar" cards (analytics/mech_similar.py, GET /api/similar, oracle feed).

Offline: a tiny hand-made artifact (tests/fixtures/mech_similar_fixture.npz is the committed copy;
the same data is rebuilt into a temp dir here so the tests never depend on the binary).
No network, no HF token.

Run: python -m pytest tests/test_mech_similar.py
"""
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_mod  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from analytics import card2vec, mech_similar, theory  # noqa: E402
from tests.test_theory import CAPS, DECK, FakeRedis  # noqa: E402

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "mech_similar_fixture.npz"

# display name -> hand-made 3-d vector (normalised on write). Mana rocks cluster on x.
CARDS = {
    "Sol Ring": [1.0, 0.0, 0.0],
    "Mind Stone": [0.9, 0.1, 0.0],
    "Arcane Signet": [0.8, 0.3, 0.0],
    "Lightning Bolt": [0.0, 1.0, 0.0],
    "Counterspell": [0.0, 0.0, 1.0],
    "Fire // Ice": [0.0, 0.9, 0.1],
}


def write_artifact(path, cards=CARDS):
    names = list(cards)
    vecs = np.array([cards[n] for n in names], dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    np.savez_compressed(path, keys=np.array([card2vec.name_key(n) for n in names]), names=np.array(names),
                        vectors=vecs.astype(np.float16), meta=np.array(json.dumps({"dim": 3, "model_id": "fixture"})))


class ModuleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "mech.npz")
        write_artifact(cls.path)
        cls.m = mech_similar.MechSimilar(cls.path)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_committed_fixture_matches_generator(self):
        if not FIXTURE.exists():
            self.skipTest("fixture not generated")
        m = mech_similar.MechSimilar(str(FIXTURE))
        self.assertEqual([x["name"] for x in m.neighbours("Sol Ring", 2)], ["Mind Stone", "Arcane Signet"])

    def test_top_n_order_and_scores(self):
        out = self.m.neighbours("Sol Ring", 3)
        self.assertEqual([x["name"] for x in out][:2], ["Mind Stone", "Arcane Signet"])
        sims = [x["sim"] for x in out]
        self.assertEqual(sims, sorted(sims, reverse=True))
        self.assertAlmostEqual(out[0]["sim"], 0.9939, places=2)
        self.assertEqual(len(self.m.neighbours("Sol Ring", 1)), 1)
        # Test similar alias method and function
        self.assertEqual(self.m.similar("Sol Ring", 3), out)
        old_def = mech_similar._default
        try:
            mech_similar._default = self.m
            self.assertEqual(mech_similar.similar("Sol Ring", 3), out)
        finally:
            mech_similar._default = old_def

    def test_name_normalisation_matches_card2vec(self):
        self.assertIs(mech_similar.name_key, card2vec.name_key)
        a = self.m.neighbours("Sol Ring", 2)
        self.assertEqual(self.m.neighbours("  sol RING ", 2), a)
        # split card: front face and both slash spellings resolve; display name is canonical
        self.assertTrue(self.m.has("Fire"))
        self.assertTrue(self.m.has("Fire / Ice"))
        self.assertIn("Fire // Ice", [x["name"] for x in self.m.neighbours("Lightning Bolt", 2)])

    def test_self_exclusion(self):
        for n in CARDS:
            self.assertNotIn(card2vec.name_key(n), [card2vec.name_key(x["name"]) for x in self.m.neighbours(n, 10)])
        self.assertEqual(len(self.m.neighbours("Sol Ring", 50)), len(CARDS) - 1)

    def test_unknown_card_returns_empty(self):
        self.assertEqual(self.m.neighbours("Card Newer Than Aetherdrift", 5), [])
        self.assertFalse(self.m.has("Card Newer Than Aetherdrift"))
        self.assertEqual(self.m.neighbours("", 5), [])

    def test_missing_artifact_warns_once_and_returns_empty(self):
        m = mech_similar.MechSimilar(os.path.join(self.tmp.name, "nope.npz"))
        with self.assertLogs("commander_lab.mech_similar", level="WARNING") as cm:
            self.assertEqual(m.neighbours("Sol Ring", 5), [])
        self.assertEqual(len(cm.output), 1)
        # second call: still [] and no second warning
        with self.assertNoLogs("commander_lab.mech_similar", level="WARNING"):
            self.assertEqual(m.neighbours("Sol Ring", 5), [])
            self.assertFalse(m.available())
        self.assertEqual(m.info()["status"], "unavailable")

    def test_corrupt_artifact_does_not_raise(self):
        bad = os.path.join(self.tmp.name, "bad.npz")
        Path(bad).write_bytes(b"not an npz")
        m = mech_similar.MechSimilar(bad)
        with self.assertLogs("commander_lab.mech_similar", level="WARNING"):
            self.assertEqual(m.neighbours("Sol Ring", 5), [])

    def _write(self, name, **over):
        d = dict(keys=np.array(["a", "b"]), names=np.array(["A", "B"]),
                 vectors=np.array([[1, 0], [0, 1]], dtype=np.float16),
                 meta=np.array(json.dumps({"dim": 2})))
        d.update(over)
        p = os.path.join(self.tmp.name, name)
        np.savez(p, **d)
        return p if p.endswith(".npz") else p + ".npz"

    def test_malformed_artifacts_degrade(self):
        cases = {
            "mismatch": dict(names=np.array(["A"])),
            "oned": dict(vectors=np.array([1, 0], dtype=np.float16)),
            "empty": dict(keys=np.array([]), names=np.array([]), vectors=np.zeros((0, 2), dtype=np.float16)),
            "nan": dict(vectors=np.array([[1, np.nan], [0, 1]], dtype=np.float16)),
            "meta": dict(meta=np.array(json.dumps([1]))),
        }
        for name, over in cases.items():
            m = mech_similar.MechSimilar(self._write(name, **over))
            with self.assertLogs("commander_lab.mech_similar", level="WARNING"):
                self.assertEqual(m.neighbours("a", 5), [], name)
            self.assertIsNone(m.vectors, name)

    def test_rows_are_renormalized(self):
        m = mech_similar.MechSimilar(self._write("scaled", vectors=np.array([[3, 0], [0, 0.5]], dtype=np.float16)))
        self.assertTrue(m.available())
        self.assertTrue(np.allclose(np.linalg.norm(m.vectors, axis=1), 1.0, atol=1e-5))

    def test_env_var_path(self):
        old = os.environ.get("MECH_SIMILAR_PATH")
        os.environ["MECH_SIMILAR_PATH"] = self.path
        try:
            self.assertEqual(mech_similar.resolve_path(), self.path)
        finally:
            if old is None:
                os.environ.pop("MECH_SIMILAR_PATH")
            else:
                os.environ["MECH_SIMILAR_PATH"] = old

    def test_vectors_are_upcast_to_float32(self):
        self.m.available()
        self.assertEqual(self.m.vectors.dtype, np.float32)


class EndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        path = os.path.join(cls.tmp.name, "mech.npz")
        write_artifact(path)
        cls._old = mech_similar._default
        mech_similar._default = mech_similar.MechSimilar(path)
        cls.client = TestClient(app_mod.app)

    @classmethod
    def tearDownClass(cls):
        mech_similar._default = cls._old
        cls.tmp.cleanup()

    def test_shape_matches_card2vec(self):
        sim = self.client.get("/api/similar", params={"card": "Sol Ring", "n": 2})
        self.assertEqual(sim.status_code, 200)
        body = sim.json()
        c2v = self.client.get("/api/card2vec", params={"card": "Sol Ring", "n": 2}).json()
        self.assertEqual(set(body), set(c2v))
        self.assertTrue(body["success"])
        self.assertEqual(body["card"], "Sol Ring")
        self.assertIs(body["known"], True)
        self.assertEqual([x["name"] for x in body["neighbours"]], ["Mind Stone", "Arcane Signet"])
        for x in body["neighbours"]:
            self.assertEqual(set(x), {"name", "sim"})
        self.assertEqual(body["model"]["status"], "ok")

    def test_unknown_card_is_200_empty(self):
        r = self.client.get("/api/similar", params={"card": "Card Newer Than Aetherdrift"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["neighbours"], [])
        self.assertIs(r.json()["known"], False)

    def test_no_card_returns_model_info_only(self):
        body = self.client.get("/api/similar").json()
        self.assertEqual(set(body), {"success", "model"})

    def test_n_is_clamped(self):
        body = self.client.get("/api/similar", params={"card": "Sol Ring", "n": 9999}).json()
        self.assertEqual(len(body["neighbours"]), len(CARDS) - 1)
        body = self.client.get("/api/similar", params={"card": "Sol Ring", "n": -3}).json()
        self.assertEqual(len(body["neighbours"]), 1)

    def test_missing_artifact_endpoint_still_200(self):
        old = mech_similar._default
        mech_similar._default = mech_similar.MechSimilar(os.path.join(self.tmp.name, "gone.npz"))
        try:
            with self.assertLogs("commander_lab.mech_similar", level="WARNING"):
                r = self.client.get("/api/similar", params={"card": "Sol Ring"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.json()["neighbours"], [])
            self.assertEqual(r.json()["model"]["status"], "unavailable")
        finally:
            mech_similar._default = old


class OracleFeedTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.path = os.path.join(cls.tmp.name, "mech.npz")
        # 30 near-identical "rocks" so every card has many neighbours above the threshold
        cards = {f"Rock {i:02d}": [1.0, 0.001 * i, 0.0] for i in range(30)}
        cards["Island"] = [1.0, 0.0, 0.0]
        write_artifact(cls.path, cards)
        cls.m = mech_similar.MechSimilar(cls.path)
        cls.names = [f"Rock {i:02d}" for i in range(30)]

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_caps_are_module_constants_and_respected(self):
        self.assertIsInstance(mech_similar.FEED_MAX_CARDS, int)
        self.assertIsInstance(mech_similar.FEED_PER_CARD, int)
        feed = mech_similar.oracle_feed(self.names, model=self.m)
        self.assertEqual(len(feed), mech_similar.FEED_MAX_CARDS)
        self.assertTrue(all(len(v) <= mech_similar.FEED_PER_CARD for v in feed.values()))
        self.assertTrue(all(len(v) == mech_similar.FEED_PER_CARD for v in feed.values()))
        self.assertEqual(list(feed)[0], "Rock 00")  # input order kept: commander first

    def test_basics_unknown_and_low_similarity_are_left_out(self):
        feed = mech_similar.oracle_feed(["Island", "Not A Card", "Rock 00"], model=self.m)
        self.assertEqual(list(feed), ["Rock 00"])
        self.assertEqual(mech_similar.oracle_feed(["Rock 00"], model=self.m, min_sim=1.1), {})

    def test_unavailable_model_gives_empty_feed(self):
        m = mech_similar.MechSimilar(os.path.join(self.tmp.name, "gone.npz"))
        with self.assertLogs("commander_lab.mech_similar", level="WARNING"):
            self.assertEqual(mech_similar.oracle_feed(["Rock 00"], model=m), {})

    def test_theory_request_carries_capped_field(self):
        old = mech_similar._default
        mech_similar._default = self.m
        deck = dict(DECK, commanders=["Rock 00"],
                    cards=[{"name": n, "quantity": 1} for n in self.names] + [{"name": "Island", "quantity": 70}])
        try:
            req = theory.build_request(FakeRedis(), deck, "rocks", "keep", owned={})
        finally:
            mech_similar._default = old
        feed = req["mechanically_similar"]
        self.assertIn("Rock 00", feed)  # the commander is considered first
        self.assertLessEqual(len(feed), mech_similar.FEED_MAX_CARDS)
        self.assertTrue(all(len(v) <= mech_similar.FEED_PER_CARD for v in feed.values()))
        self.assertEqual(set(next(iter(feed.values()))[0]), {"name", "sim"})
        json.dumps(req)  # still a plain JSON payload

    def test_theory_request_has_field_even_without_artifact(self):
        old = mech_similar._default
        mech_similar._default = mech_similar.MechSimilar(os.path.join(self.tmp.name, "gone.npz"))
        try:
            with self.assertLogs("commander_lab.mech_similar", level="WARNING"):
                req = theory.build_request(FakeRedis(), DECK, "angels", "keep", owned={})
        finally:
            mech_similar._default = old
        self.assertEqual(req["mechanically_similar"], {})

    def test_skill_documents_the_field(self):
        text = (Path(__file__).resolve().parent.parent / "docs/oracle/deck-theory/SKILL.md").read_text()
        self.assertIn("mechanically_similar", text)
        self.assertIn("NOT synergy", text)


if __name__ == "__main__":
    unittest.main()

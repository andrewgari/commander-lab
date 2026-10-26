"""
AN-3: weak performers (computed only, no game logs).

weak score = mean of three equally weighted parts, each 0..1 (1 = weak):
    edges    1 - min(1, degree / p75 degree of the deck's non-land cards)
    pillar   1 - pillar fit (0 when the deck has no live pillars yet: unknown is not weak)
    edhrec   1 - min(1, inclusion / 0.50); a card missing from the commander's
             EDHREC page counts as 0% inclusion
Lands and commanders are never weak performers. Pet cards (Journal) are
flagged `pet: true` and never come first.

EDHREC inclusion comes from the commander page (cached per commander slug,
7-day freshness, stale copy served if EDHREC is down).

Run: python -m pytest tests/test_weak_performers.py
"""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.analytics import edhrec_inclusion
from src.server.domain.analytics import weak_performers as weak
from src.server.domain.analytics.interactions import graph as graph_mod
from tests.test_interactions import FakeRedis
from tests.test_pillar_fit import load_hh

EDHREC_FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "edhrec_kaalia_commander_page.json")


def load_edhrec_page():
    with open(EDHREC_FIXTURE, encoding="utf-8") as fh:
        return json.load(fh)


class FakeClient:
    def __init__(self, page=None, fail=False):
        self.page = page
        self.fail = fail
        self.calls = 0

    def get_commander_page(self, names):
        self.calls += 1
        if self.fail:
            raise edhrec_inclusion.EDHRecError("edhrec down")
        return self.page


class TestInclusion(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.deck, _ = load_hh()

    def test_parses_and_caches_per_commander(self):
        client = FakeClient(load_edhrec_page())
        out = edhrec_inclusion.deck_inclusion(self.r, self.deck, client=client)
        self.assertEqual(out["commander_slug"], "kaalia-of-the-vast")
        self.assertGreater(out["decks_sampled"], 10000)
        per = {row["name"]: row for row in out["per_card"]}
        self.assertAlmostEqual(per["Sol Ring"]["inclusion"], 37520 / 40240, places=4)
        self.assertIsNone(per["Baneslayer Angel"]["inclusion"])  # not on the page
        self.assertFalse(per["Baneslayer Angel"]["listed"])
        self.assertTrue(0 < per["Dragon Tempest"]["inclusion"] < 1)
        self.assertNotIn("Kaalia of the Vast", per)  # the commander isn't scored
        self.assertNotIn("Mountain", per)            # basics aren't scored
        self.assertIn("buckets", out)
        self.assertEqual(sum(b["count"] for b in out["buckets"]), len([r for r in out["per_card"] if not r["land"]]))
        edhrec_inclusion.deck_inclusion(self.r, self.deck, client=client)
        self.assertEqual(client.calls, 1)  # cached

    def test_stale_copy_served_when_down(self):
        edhrec_inclusion.deck_inclusion(self.r, self.deck, client=FakeClient(load_edhrec_page()))
        key = edhrec_inclusion.cache_key("kaalia-of-the-vast")
        rec = json.loads(self.r.get(key))
        rec["fetched_at"] = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
        self.r.set(key, json.dumps(rec))
        out = edhrec_inclusion.deck_inclusion(self.r, self.deck, client=FakeClient(fail=True))
        self.assertTrue(out["stale"])
        self.assertGreater(len(out["per_card"]), 0)

    def test_down_and_no_cache(self):
        out = edhrec_inclusion.deck_inclusion(self.r, self.deck, client=FakeClient(fail=True))
        self.assertEqual(out["status"], "unavailable")
        self.assertEqual(out["per_card"], [])

    def test_buckets_spec_labels(self):
        self.assertEqual([b[0] for b in edhrec_inclusion.BUCKETS], ["<5", "5–14", "15–29", "30–49", "50–74", "75+"])
        self.assertEqual(edhrec_inclusion.bucket_of(0.0), "<5")
        self.assertEqual(edhrec_inclusion.bucket_of(0.05), "5–14")
        self.assertEqual(edhrec_inclusion.bucket_of(0.749), "50–74")
        self.assertEqual(edhrec_inclusion.bucket_of(0.75), "75+")


class TestWeakScore(unittest.TestCase):
    def test_parts_equal_weight(self):
        s = weak.weak_parts(degree=0, p75=10, fit=0.0, inclusion=0.0, has_pillars=True)
        self.assertEqual(s, {"edges": 1.0, "pillar": 1.0, "edhrec": 1.0, "score": 1.0})
        s = weak.weak_parts(degree=10, p75=10, fit=1.0, inclusion=0.6, has_pillars=True)
        self.assertEqual(s["score"], 0.0)
        s = weak.weak_parts(degree=5, p75=10, fit=0.5, inclusion=0.25, has_pillars=True)
        self.assertAlmostEqual(s["score"], 0.5)

    def test_no_pillars_is_not_weak(self):
        s = weak.weak_parts(degree=0, p75=10, fit=None, inclusion=0.0, has_pillars=False)
        self.assertEqual(s["pillar"], 0.0)
        self.assertAlmostEqual(s["score"], 2 / 3, places=4)

    def test_unlisted_on_edhrec_counts_as_zero(self):
        s = weak.weak_parts(degree=10, p75=10, fit=1.0, inclusion=None, has_pillars=True)
        self.assertEqual(s["edhrec"], 1.0)


class TestDeckWeakPerformers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.deck, cls.tags = load_hh()
        cls.cards = {n: t["card"] for n, t in cls.tags.items()}
        cls.graph = graph_mod.build_graph([c["name"] for c in cls.deck["cards"]], cls.tags,
                                          commanders=cls.deck["commanders"])

    def fit(self, low=()):
        rows = []
        for n in self.tags:
            rows.append({"name": n, "score": 0.1 if n in low else 0.8, "pillar_id": "p1",
                         "bucket": "generic" if n in low else "pillar"})
        return {"status": "ok", "per_card": rows}

    def inclusion(self):
        return edhrec_inclusion.deck_inclusion(FakeRedis(), self.deck, client=FakeClient(load_edhrec_page()))

    def test_ranks_non_land_non_commander_cards(self):
        out = weak.compute_weak_performers(self.deck, self.graph, self.fit(low={"Platinum Angel"}),
                                           self.inclusion(), pets=[], card_texts=self.cards)
        names = [row["name"] for row in out["cards"]]
        self.assertNotIn("Kaalia of the Vast", names)
        self.assertNotIn("Mountain", names)
        self.assertNotIn("Command Tower", names)
        self.assertEqual(len(names), 87 - 1 - 22)  # 87 unique - commander - 22 unique lands
        scores = [row["weak_score"] for row in out["cards"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        top = out["cards"][0]
        self.assertEqual(top["name"], "Platinum Angel")
        for k in ("edges", "fit", "edhrec_inclusion", "parts", "weak_score", "reasons"):
            self.assertIn(k, top)
        self.assertIn("p75_degree", out["weights"])
        self.assertEqual(out["weights"]["parts"], ["edges", "pillar", "edhrec"])

    def test_pets_flagged_and_moved_to_notice(self):
        out = weak.compute_weak_performers(self.deck, self.graph, self.fit(low={"Platinum Angel"}),
                                           self.inclusion(), pets=["Platinum Angel"], card_texts=self.cards)
        self.assertNotEqual(out["cards"][0]["name"], "Platinum Angel")
        pa = next(row for row in out["cards"] if row["name"] == "Platinum Angel")
        self.assertTrue(pa["pet"])
        self.assertEqual(out["pet_notice"]["name"], "Platinum Angel")

    def test_without_edhrec(self):
        out = weak.compute_weak_performers(self.deck, self.graph, self.fit(), {"status": "unavailable",
                                                                               "per_card": []}, pets=[], card_texts=self.cards)
        self.assertFalse(out["inputs"]["edhrec"])
        self.assertTrue(all(row["parts"]["edhrec"] == 0.0 for row in out["cards"]))


class TestRoutes(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from src.server.app.main import app
        self.client = TestClient(app)
        self.deck, _ = load_hh()

    @patch("src.server.app.main.interactions_service")
    @patch("src.server.app.main.insights")
    @patch("src.server.app.main.registry")
    def test_weak_route(self, mock_registry, mock_weak, mock_graph):
        mock_registry.find_deck.return_value = self.deck
        mock_graph.get_deck_graph.return_value = {"nodes": [], "edges": []}
        mock_weak.deck_weak_performers.return_value = {"cards": [{"name": "Platinum Angel"}]}
        res = self.client.get("/api/decks/archidekt:417707/weak-performers?limit=5")
        self.assertEqual(res.status_code, 200, res.text)
        self.assertEqual(res.json()["weak_performers"]["cards"][0]["name"], "Platinum Angel")
        self.assertEqual(mock_weak.deck_weak_performers.call_args.kwargs["limit"], 5)

    @patch("src.server.app.main.insights")
    @patch("src.server.app.main.registry")
    def test_edhrec_route(self, mock_registry, mock_inc):
        mock_registry.find_deck.return_value = self.deck
        mock_inc.deck_edhrec.return_value = {"decks_sampled": 40240, "per_card": []}
        res = self.client.get("/api/decks/archidekt:417707/edhrec")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["edhrec"]["decks_sampled"], 40240)


if __name__ == "__main__":
    unittest.main()

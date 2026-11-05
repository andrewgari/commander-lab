"""
AN-2: Commander Spellbook integration.

Covers the find-my-combos client (request shape, pagination, cache with
a freshness window and stale fallback), combo normalization, "in deck" vs
"one card away", owned-card flagging from the collection, and the JSON
API routes. Uses a trimmed real response for Holy Hellfire
(tests/fixtures/spellbook_holy_hellfire.json). No network.

Run: python -m pytest tests/test_combos.py
"""
import copy
import json
import os
import sys
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.server.domain.analytics.interactions import combos
from tests.test_interactions import FakeRedis

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "spellbook_holy_hellfire.json")

HH = {
    "id": 417707, "name": "Holy Hellfire", "registry_id": "archidekt:417707",
    "commanders": ["Kaalia of the Vast"],
    "cards": [{"name": n, "quantity": 1} for n in (
        "Kaalia of the Vast", "Aurelia, the Warleader", "Helm of the Host", "Master of Cruelties",
        "Dragon Tempest", "Animate Dead", "Sol Ring", "Mountain")],
}


def fixture():
    with open(FIXTURE) as fh:
        return json.load(fh)


class FakeHTTP:
    def __init__(self, pages=None, fail=False):
        self.pages = pages if pages is not None else [fixture()]
        self.fail = fail
        self.calls = []

    def __call__(self, url, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json})
        if self.fail:
            raise ConnectionError("spellbook down")
        page = self.pages[min(len(self.calls) - 1, len(self.pages) - 1)]

        class Resp:
            status_code = 200

            def raise_for_status(self_inner):
                return None

            def json(self_inner):
                return page
        return Resp()


def add_instance(r, iid, name, status, deck_id=None, deck_name=""):
    r.set(f"instance:{iid}", json.dumps({"id": iid, "card_name": name, "ownership_status": status,
                                         "deck_id": deck_id, "deck_name": deck_name}))


class TestClient(unittest.TestCase):
    def test_request_shape_commanders_split_from_main(self):
        http = FakeHTTP()
        client = combos.SpellbookClient(FakeRedis(), http_post=http)
        client.find_my_combos(["Kaalia of the Vast"], ["Sol Ring", "Kaalia of the Vast", "Helm of the Host"])
        body = http.calls[0]["json"]
        self.assertTrue(http.calls[0]["url"].endswith("/find-my-combos"))
        self.assertEqual(body["commanders"], [{"card": "Kaalia of the Vast"}])
        self.assertEqual(sorted(c["card"] for c in body["main"]), ["Helm of the Host", "Sol Ring"])

    def test_cached_by_decklist_and_reused(self):
        r = FakeRedis()
        http = FakeHTTP()
        client = combos.SpellbookClient(r, http_post=http)
        a = client.find_my_combos(["Kaalia of the Vast"], ["Sol Ring", "Helm of the Host"])
        b = client.find_my_combos(["Kaalia of the Vast"], ["Helm of the Host", "Sol Ring"])  # order-insensitive
        self.assertEqual(len(http.calls), 1)
        self.assertFalse(a["cached"])
        self.assertTrue(b["cached"])
        self.assertEqual(len(b["included"]), 2)
        self.assertTrue(any(k.startswith("spellbook:fmc:") for k in r.store))
        client.find_my_combos(["Kaalia of the Vast"], ["Sol Ring"])  # different list -> new fetch
        self.assertEqual(len(http.calls), 2)

    def test_refresh_bypasses_cache(self):
        http = FakeHTTP()
        client = combos.SpellbookClient(FakeRedis(), http_post=http)
        client.find_my_combos([], ["Sol Ring"])
        client.find_my_combos([], ["Sol Ring"], refresh=True)
        self.assertEqual(len(http.calls), 2)

    def test_expired_cache_refetches_and_stale_is_served_on_failure(self):
        r = FakeRedis()
        client = combos.SpellbookClient(r, http_post=FakeHTTP(), max_age_days=7)
        client.find_my_combos([], ["Sol Ring"])
        key = next(k for k in r.store if k.startswith("spellbook:fmc:"))
        rec = json.loads(r.store[key])
        rec["fetched_at"] = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        r.store[key] = json.dumps(rec)
        down = FakeHTTP(fail=True)
        out = combos.SpellbookClient(r, http_post=down, max_age_days=7).find_my_combos([], ["Sol Ring"])
        self.assertEqual(len(down.calls), 1)  # tried to refresh
        self.assertTrue(out["stale"])
        self.assertIn("spellbook down", out["error"])
        self.assertEqual(len(out["included"]), 2)

    def test_failure_without_cache_raises(self):
        client = combos.SpellbookClient(FakeRedis(), http_post=FakeHTTP(fail=True))
        with self.assertRaises(combos.SpellbookError):
            client.find_my_combos([], ["Sol Ring"])

    def test_pagination_is_followed(self):
        p1 = fixture()
        p1["next"] = "https://backend.commanderspellbook.com/find-my-combos?offset=2"
        p2 = copy.deepcopy(fixture())
        p2["next"] = None
        p2["results"]["included"] = []
        p2["results"]["almostIncluded"] = p2["results"]["almostIncluded"][:1]
        p2["results"]["almostIncluded"][0]["id"] = "extra-1"
        http = FakeHTTP(pages=[p1, p2])
        out = combos.SpellbookClient(FakeRedis(), http_post=http).find_my_combos([], ["Sol Ring"])
        self.assertEqual(len(http.calls), 2)
        self.assertIn("offset=2", http.calls[1]["url"])
        self.assertEqual(len(out["almost_included"]), 7)


class TestNormalize(unittest.TestCase):
    def test_combo_fields(self):
        raw = fixture()["results"]["included"][0]
        c = combos.normalize_variant(raw)
        self.assertEqual(c["id"], "1692-4364")
        self.assertEqual(c["url"], "https://commanderspellbook.com/combo/1692-4364/")
        self.assertEqual(sorted(c["card_names"]), ["Aurelia, the Warleader", "Helm of the Host"])
        self.assertIn("Infinite combat phases", c["produces"])
        self.assertTrue(c["description"].startswith("At the beginning of your combat"))
        self.assertEqual(c["prerequisites"]["notable"], "Helm attached to Aurelia.")
        self.assertEqual(c["bracket_tag"], "S")
        self.assertIsInstance(c["popularity"], int)
        self.assertEqual(c["cards"][0]["zones"], ["B"])

    def test_name_matching_handles_case_and_faces(self):
        self.assertEqual(combos.name_key("Delver of Secrets // Insectile Aberration"), "delver of secrets")
        self.assertEqual(combos.name_key("  SOL ring "), "sol ring")


class TestDeckCombos(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self.client = combos.SpellbookClient(self.r, http_post=FakeHTTP())

    def test_in_deck_and_one_away(self):
        out = combos.deck_combos(self.r, HH, client=self.client)
        self.assertEqual(out["source"], "commanderspellbook")
        self.assertEqual(out["counts"]["in_deck"], 2)
        in_deck = {tuple(sorted(c["card_names"])) for c in out["in_deck"]}
        self.assertIn(("Kaalia of the Vast", "Master of Cruelties"), in_deck)
        self.assertEqual(out["counts"]["one_away"], 6)
        for c in out["one_away"]:
            self.assertEqual(len(c["missing"]), 1)
            self.assertNotIn(c["missing"][0], [x["name"] for x in HH["cards"]])
        self.assertEqual(out["counts"]["off_identity_one_away"], 2)

    def test_owned_missing_card_is_flagged_and_sorted_first(self):
        add_instance(self.r, "i1", "Worldgorger Dragon", "in_collection")
        add_instance(self.r, "i2", "Combat Celebrant", "in_deck", deck_id="archidekt:5", deck_name="Other Deck")
        add_instance(self.r, "i3", "Kiki-Jiki, Mirror Breaker", "not_owned")
        out = combos.deck_combos(self.r, HH, client=self.client)
        owned = [c for c in out["one_away"] if c["missing_owned"]]
        self.assertEqual(sorted(c["missing"][0] for c in owned), ["Combat Celebrant", "Worldgorger Dragon"])
        self.assertTrue(all(c["missing_owned"] for c in out["one_away"][:2]))  # owned first
        wg = next(c for c in owned if c["missing"][0] == "Worldgorger Dragon")
        self.assertEqual(wg["owned"]["Worldgorger Dragon"]["available"], 1)
        cc = next(c for c in owned if c["missing"][0] == "Combat Celebrant")
        self.assertEqual(cc["owned"]["Combat Celebrant"]["available"], 0)
        self.assertEqual(cc["owned"]["Combat Celebrant"]["decks"], ["Other Deck"])
        kiki = next(c for c in out["one_away"] if c["missing"][0] == "Kiki-Jiki, Mirror Breaker")
        self.assertFalse(kiki["missing_owned"])  # not_owned does not count
        self.assertEqual(out["counts"]["one_away_owned"], 2)

    def test_owned_only_filter_and_limit(self):
        add_instance(self.r, "i1", "Worldgorger Dragon", "in_collection")
        out = combos.deck_combos(self.r, HH, client=self.client, owned_only=True)
        self.assertEqual([c["missing"][0] for c in out["one_away"]], ["Worldgorger Dragon"])
        out = combos.deck_combos(self.r, HH, client=self.client, limit=3)
        self.assertEqual(len(out["one_away"]), 3)
        self.assertEqual(out["counts"]["one_away"], 6)  # counts are pre-limit


class TestComboRoutes(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient as TC
        from src.server.app.main import app
        self.client = TC(app)

    @patch("src.server.app.main.combos")
    @patch("src.server.app.main.registry")
    def test_combos_route(self, mock_registry, mock_combos):
        mock_registry.find_deck.return_value = HH
        mock_combos.deck_combos.return_value = {"counts": {"in_deck": 2}, "in_deck": [], "one_away": []}
        res = self.client.get("/api/decks/archidekt:417707/combos?owned_only=true&limit=5")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["combos"]["counts"]["in_deck"], 2)
        kw = mock_combos.deck_combos.call_args.kwargs
        self.assertTrue(kw["owned_only"])
        self.assertEqual(kw["limit"], 5)
        self.assertFalse(kw["refresh"])

    @patch("src.server.app.main.combos")
    @patch("src.server.app.main.registry")
    def test_combos_upstream_error_is_502(self, mock_registry, mock_combos):
        mock_registry.find_deck.return_value = HH
        mock_combos.SpellbookError = combos.SpellbookError
        mock_combos.deck_combos.side_effect = combos.SpellbookError("down")
        res = self.client.get("/api/decks/archidekt:417707/combos")
        self.assertEqual(res.status_code, 502)
        self.assertFalse(res.json()["success"])

    @patch("src.server.app.main.registry")
    def test_unknown_deck_404(self, mock_registry):
        mock_registry.find_deck.return_value = None
        mock_registry.list_decks.return_value = []
        self.assertEqual(self.client.get("/api/decks/nope/combos").status_code, 404)
        self.assertEqual(self.client.get("/api/decks/nope/engines").status_code, 404)

    @patch("src.server.app.main.interactions_service")
    @patch("src.server.app.main.registry")
    def test_engines_route(self, mock_registry, mock_service):
        mock_registry.find_deck.return_value = HH
        mock_service.get_deck_engines.return_value = {"counts": {"engines": 1}, "engines": [{"name": "x"}]}
        res = self.client.get("/api/decks/archidekt:417707/engines")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["engines"]["counts"]["engines"], 1)


if __name__ == "__main__":
    unittest.main()

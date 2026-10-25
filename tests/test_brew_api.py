"""API + UI tests for the brew flow: /api/brew/* endpoints end-to-end and
the /brew + /brew/{id} pages (template-level, incl. TCGplayer cart markup).

Run: pytest tests/test_brew_api.py
"""
import json
import os
import sys
import unittest
from unittest.mock import patch, Mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import brew_builder
import instances
import reconstruct_pools
import registry
from fastapi.testclient import TestClient

from tests.test_brew import FakeRedis, make_deck, static_price_lookup


class TestBrewAPI(unittest.TestCase):
    def setUp(self):
        import app as app_module
        self.app_module = app_module
        self.r = FakeRedis()
        self.deck1 = make_deck(deck_id=42, name="Atraxa Superfriends", status="physical")
        self.deck2 = make_deck(deck_id=43, name="Urza Artifacts", status="digital",
                               commanders=["Urza, Lord High Artificer"],
                               cards=[{"name": "Mana Crypt", "quantity": 1}])
        self.r.set("decks", json.dumps([self.deck1, self.deck2]))
        instances.create_instance(self.r, "Sol Ring", ownership_status="in_deck",
                                  deck_id="archidekt:42", deck_name="Atraxa Superfriends")
        instances.create_instance(self.r, "Demonic Tutor", ownership_status="in_collection")
        self.r.set("card_meta:Lightning Greaves", json.dumps({"color": "", "type": "Artifact"}))

        self._orig_lookup = reconstruct_pools.redis_price_lookup
        reconstruct_pools.redis_price_lookup = static_price_lookup({"Lightning Greaves": 3.50})
        brew_builder.set_builder(brew_builder.StubBrewBuilder())

        self.patch = patch.object(app_module, "r", self.r)
        self.patch.start()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.patch.stop()
        reconstruct_pools.redis_price_lookup = self._orig_lookup

    def _start(self, **over):
        body = {
            "commander": "Atraxa, Praetors' Voice",
            "donor_deck_ids": ["archidekt:42"],
            "build_mode": "owned_first",
            "name": "Atraxa brew",
            "power_level": "3",
            "idea": "proliferate",
            "themes": ["counters"],
            "notes": "",
        }
        body.update(over)
        return self.client.post("/api/brew/start", json=body)

    def test_start_validation(self):
        res = self.client.post("/api/brew/start", json={"commander": "", "build_mode": "owned_first"})
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/brew/start", json={"commander": "X", "build_mode": "nope"})
        self.assertEqual(res.status_code, 400)
        res = self.client.post("/api/brew/start", json={
            "commander": "X", "build_mode": "owned_first", "donor_deck_ids": ["archidekt:9999"]})
        self.assertEqual(res.status_code, 404)

    def test_brew_end_to_end(self):
        # 1. Start
        res = self._start()
        self.assertEqual(res.status_code, 201)
        sid = res.json()["session_id"]
        self.assertTrue(sid.startswith("brew_"))

        # Donors untouched after start
        d1 = registry.find_deck(self.r, "archidekt:42")
        self.assertEqual(d1["status"], "physical")
        inst = [i for i in (self.r.get(k) for k in self.r.keys("instance:*"))]
        self.assertTrue(inst)

        # 2. Get session + pools
        res = self.client.get(f"/api/brew/{sid}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["session"]["status"], "pending_preferences")
        res = self.client.get(f"/api/brew/{sid}/pools")
        self.assertEqual(res.status_code, 200)
        tier1 = {c["name"] for c in res.json()["tier1"]}
        self.assertIn("Sol Ring", tier1)
        self.assertNotIn("Mana Crypt", tier1)  # unnamed deck excluded

        # 3. Build (sync)
        res = self.client.post(f"/api/brew/{sid}/build?sync=true")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "drafts_ready")

        # 4. Drafts + availability
        res = self.client.get(f"/api/brew/{sid}/drafts")
        self.assertEqual(len(res.json()["drafts"]), 1)
        res = self.client.get(f"/api/brew/{sid}/availability")
        self.assertEqual(res.status_code, 200)
        rep = res.json()
        self.assertIn("buy_list", rep)
        self.assertTrue(all(b["needs_purchase"] for b in rep["buy_list"]))

        # 5. Accept -> virtual deck, donors intact
        before = self.r.get("decks")
        res = self.client.post(f"/api/brew/{sid}/accept", json={"draft_index": 0})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["session"]["status"], "accepted")
        deck = res.json()["created_decks"][0]
        self.assertEqual(deck["status"], "testing")
        d1_after = registry.find_deck(self.r, "archidekt:42")
        self.assertEqual(d1_after["status"], "physical")
        self.assertIn("custom_tag", d1_after.get("tags", []))

        # 6. Sessions list + 404 + abandon
        res = self.client.get("/api/brew/sessions")
        self.assertTrue(any(s["session_id"] == sid for s in res.json()["sessions"]))
        self.assertEqual(self.client.get("/api/brew/brew_nope").status_code, 404)

        res = self._start(name="Second brew")
        sid2 = res.json()["session_id"]
        res = self.client.post(f"/api/brew/{sid2}/abandon", json={"reason": "nope"})
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["session"]["status"], "abandoned")

    def test_build_modes_accepted(self):
        for mode in ("owned_first", "best_build"):
            res = self._start(build_mode=mode)
            self.assertEqual(res.status_code, 201, res.text)
            sid = res.json()["session_id"]
            res = self.client.post(f"/api/brew/{sid}/build?sync=true")
            self.assertEqual(res.status_code, 200)
            self.assertEqual(res.json()["status"], "drafts_ready")

    def test_commanders_endpoint_short_query(self):
        res = self.client.get("/api/brew/commanders?q=a")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["results"], [])

    @staticmethod
    def _card(name, type_line="Legendary Creature — Spider Hero", legality="legal"):
        return {
            "id": name.lower().replace(" ", "-"), "name": name, "type_line": type_line,
            "legalities": {"commander": legality}, "colors": [], "color_identity": [],
            "keywords": [], "mana_cost": "", "image_uris": {},
        }

    def test_commander_search_paginates_and_prioritizes_relevant_matches(self):
        first = [self._card(f"Spider Hero {i}") for i in range(30)]
        second = [self._card("Amazing Spider Hero"), self._card("Alibou, Ancient Witness", "Legendary Artifact Creature")]
        pages = [
            {"data": first, "has_more": True, "next_page": "https://api.scryfall.com/page/2"},
            {"data": second, "has_more": False},
        ]
        def mocked_page(*args, **kwargs):
            return Mock(status_code=200, json=lambda: pages.pop(0))
        with patch.object(self.app_module.httpx, "get", side_effect=mocked_page) as get:
            results = self.client.get("/api/brew/commanders?q=spider").json()["results"]
            self.assertEqual(len(results), 32)
            self.assertEqual(get.call_count, 2)
        self.assertEqual(results[0]["name"], "Amazing Spider Hero")
        self.assertIn("Alibou, Ancient Witness", [row["name"] for row in results])

    def test_commander_search_fuzzy_fallback_is_legal_and_general(self):
        with patch.object(self.app_module.httpx, "get", side_effect=[
            Mock(status_code=200, json=lambda: {"data": [], "has_more": False}),
            Mock(status_code=200, json=lambda: self._card("Atraxa, Praetors’ Voice")),
        ]) as get:
            results = self.client.get("/api/brew/commanders?q=atraxa").json()["results"]
        self.assertEqual(results[0]["name"], "Atraxa, Praetors’ Voice")
        self.assertEqual(get.call_count, 2)
        # A misspelled arbitrary query gets the same general Scryfall fuzzy path.
        with patch.object(self.app_module.httpx, "get", side_effect=[
            Mock(status_code=200, json=lambda: {"data": [], "has_more": False}),
            Mock(status_code=200, json=lambda: self._card("The Gitrog Monster")),
        ]):
            results = self.client.get("/api/brew/commanders?q=gitrog").json()["results"]
        self.assertEqual(results[0]["name"], "The Gitrog Monster")

    def test_commander_search_cache_expires_and_does_not_keep_empty_results_forever(self):
        old = json.dumps({"cached_at": 1, "results": []})
        self.r.set("brew:commander_search:spider", old)
        fresh = self._card("Spider Hero")
        with patch.object(self.app_module.httpx, "get", return_value=Mock(
            status_code=200, json=lambda: {"data": [fresh], "has_more": False}
        )) as get:
            results = self.client.get("/api/brew/commanders?q=spider").json()["results"]
        self.assertEqual(results[0]["name"], "Spider Hero")
        get.assert_called_once()


class TestBrewPages(unittest.TestCase):
    """Template-level checks for /brew (console) and /brew/{id} (deck view)."""

    def setUp(self):
        import app as app_module
        self.r = FakeRedis()
        self.r.set("decks", json.dumps([make_deck(deck_id=42, name="Atraxa Superfriends")]))
        self.patch = patch.object(app_module, "r", self.r)
        self.patch.start()
        self.client = TestClient(app_module.app)

    def tearDown(self):
        self.patch.stop()

    def test_brew_console_renders(self):
        html = self.client.get("/brew").text
        self.assertIn("Brew a deck", html)
        self.assertIn('id="brewCmdSearch"', html)
        self.assertIn('id="brewDonorList"', html)
        self.assertIn('name="build_mode" value="owned_first" checked', html)
        self.assertIn('name="build_mode" value="best_build"', html)
        self.assertIn('id="brewGenerate"', html)
        self.assertIn("/js/brew.js", html)
        self.assertIn("brew-donor-data", html)
        # Donor named only
        self.assertIn("Only named decks are sourced", html)
        # Nothing moves copy
        self.assertIn("no cards move", html.lower())

    def test_brew_deck_page_renders_and_cart_markup(self):
        html = self.client.get("/brew/brew_abc123").text
        self.assertIn('id="brewRoot" data-session-id="brew_abc123"', html)
        self.assertIn('id="brewStats"', html)
        self.assertIn('id="brewProvBar"', html)
        self.assertIn('id="brewBuyCart"', html)
        self.assertIn('id="brewTabs"', html)
        self.assertIn('id="brewMakePhysical"', html)
        # TCGplayer cart: page loads the shared TcgCart renderer (base.html)
        # and brew.js aggregates buys via TcgCart.mount.
        self.assertIn("tcgplayer_cart.js", html)
        js = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                               "static", "js", "brew.js")).read()
        self.assertIn("TcgCart.mount", js)

    def test_decks_page_brew_button(self):
        html = self.client.get("/decks").text
        self.assertIn('id="brewBtn"', html)
        self.assertIn('href="/brew"', html)
        self.assertIn("+ Brew a deck", html)


if __name__ == "__main__":
    unittest.main()

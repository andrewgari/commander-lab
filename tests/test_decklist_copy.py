"""Copy decklist control: /api/deck/{name}/decklist + deck_head wiring.

The API route is a thin adapter — decklist_export.export_decklist_text owns
all format logic (covered by tests/test_decklist_export.py). These tests pin
the adapter (stored-deck shape -> export input), the route's status codes,
and the control's placement: present on every deck page (the shared
deck_head partial), absent from the /decks list page.
"""
import json
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from tests.test_instances import FakeRedis

import src.server.app.main as app_module
import src.server.domain.inventory.instances as instance_store
import src.server.domain.decks.registry as registry

ROOT = Path(__file__).resolve().parent.parent
COPY_JS = ROOT / "src/web/static" / "js" / "decklist_copy.js"

SID = "716d0b3b-bac9-4fb8-882e-bd6171864043"


class FakeRedisCase(unittest.TestCase):
    def setUp(self):
        self.r = FakeRedis()
        self._orig_r = app_module.r
        app_module.r = self.r
        self.client = TestClient(app_module.app)
        self.deck = registry.upsert_deck(self.r, {
            "source": "archidekt", "source_id": "555", "name": "Copy Deck", "color": "G",
            "commanders": ["Omnath, Locus of Mana"], "commander_uids": ["cmdr-uid"],
            "folder": "x", "description": "",
            "cards": [{"name": "Omnath, Locus of Mana", "quantity": 1},
                      {"name": "Sol Ring", "quantity": 1},
                      {"name": "Forest", "quantity": 30}],
            "slots": {"maybeboard": [{"name": "Rest in Peace", "quantity": 2},
                                     {"name": "Dryad Arbor", "quantity": 1, "remapped_from": "sideboard"}]},
            "url": "",
        }, default_status="testing")
        instance_store.create_instance(
            self.r, "Sol Ring", ownership_status="in_deck",
            deck_id=registry.registry_id_of(self.deck),
            set="C21", set_name="Commander 2021", collector_number="263", archidekt_uid=SID)

    def tearDown(self):
        app_module.r = self._orig_r

    def decklist(self, query=""):
        return self.client.get("/api/deck/Copy%20Deck/decklist" + query)


class TestDecklistApi(FakeRedisCase):
    def test_plain_default_commander_first_and_no_boards(self):
        res = self.decklist()
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["format"], "plain")
        lines = body["text"].splitlines()
        self.assertEqual(lines[0], "1 Omnath, Locus of Mana")
        self.assertIn("1 Sol Ring", lines)
        self.assertIn("30 Forest", lines)
        # Boards off by default; commander not duplicated into the main list.
        self.assertNotIn("Rest in Peace", body["text"])
        self.assertNotIn("Dryad Arbor", body["text"])
        self.assertEqual(body["text"].count("Omnath, Locus of Mana"), 1)

    def test_board_toggles(self):
        body = self.decklist("?include_sideboard=true&include_maybeboard=true").json()
        self.assertIn("2 Rest in Peace", body["text"])
        self.assertIn("1 Dryad Arbor", body["text"])
        only_sb = self.decklist("?include_sideboard=true").json()["text"]
        self.assertIn("Dryad Arbor", only_sb)          # remapped_from=sideboard entry
        self.assertNotIn("Rest in Peace", only_sb)     # plain maybeboard stays out
        only_mb = self.decklist("?include_maybeboard=true").json()["text"]
        self.assertIn("Rest in Peace", only_mb)
        self.assertNotIn("Dryad Arbor", only_mb)

    def test_format_passthrough(self):
        body = self.decklist("?format=moxfield").json()
        self.assertIn("1 Omnath, Locus of Mana *CMDR*", body["text"].splitlines())
        forge = self.decklist("?format=forge").json()["text"]
        self.assertIn("[commander]", forge)
        self.assertIn("Name=Copy Deck", forge)

    def test_unknown_format_is_400(self):
        res = self.decklist("?format=nope")
        self.assertEqual(res.status_code, 400)
        self.assertFalse(res.json()["success"])

    def test_unknown_deck_is_404(self):
        res = self.client.get("/api/deck/No%20Such%20Deck/decklist")
        self.assertEqual(res.status_code, 404)
        self.assertFalse(res.json()["success"])

    def test_printing_toggle_enriches_from_owned_instance(self):
        body = self.decklist("?include_printing=true").json()["text"]
        self.assertIn("1 Sol Ring (C21) 263", body)
        # No printing data for Forest: falls back to name-only, never fails.
        self.assertIn("30 Forest", body.splitlines())
        self.assertNotIn("(C21)", self.decklist().json()["text"])  # off by default


class TestCopyControlPlacement(FakeRedisCase):
    def test_deck_page_has_control(self):
        page = self.client.get("/deck/Copy%20Deck")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="copyDeckBtn"', page.text)
        self.assertIn("Copy decklist", page.text)
        for opt in ("plain", "archidekt", "moxfield", "forge", "tcgplayer"):
            self.assertIn(f'value="{opt}"', page.text)
        self.assertIn('<option value="plain" selected>', page.text)  # Plain default
        for toggle in ("copyDeckSideboard", "copyDeckMaybeboard", "copyDeckPrinting"):
            self.assertIn(f'id="{toggle}"', page.text)
        self.assertNotIn('id="copyDeckSideboard" checked', page.text)  # toggles off by default
        self.assertIn('id="copyDeckText"', page.text)                  # manual-copy fallback
        self.assertIn('data-deck-name="Copy Deck"', page.text)
        self.assertIn("/static/js/decklist_copy.js", page.text)

    def test_control_on_other_deck_tabs(self):
        for tab in ("stats", "journal"):
            page = self.client.get(f"/deck/Copy%20Deck/{tab}")
            self.assertEqual(page.status_code, 200, tab)
            self.assertIn('id="copyDeckBtn"', page.text, tab)

    def test_deck_list_page_has_no_control(self):
        page = self.client.get("/decks")
        self.assertEqual(page.status_code, 200)
        self.assertNotIn("copyDeck", page.text)

    def test_unknown_deck_page_has_no_control(self):
        page = self.client.get("/deck/Ghost%20Deck")
        self.assertNotIn('id="copyDeckBtn"', page.text)


class TestCopyJs(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.js = COPY_JS.read_text()

    def test_calls_the_export_route_not_local_logic(self):
        self.assertIn('/api/deck/" + encodeURIComponent(deckName) + "/decklist?', self.js)
        # No format rendering in the UI: no line building at all.
        for marker in ("*CMDR*", "[Commander{top}]", "[metadata]", "SB: "):
            self.assertNotIn(marker, self.js)

    def test_clipboard_api_with_fallback(self):
        self.assertIn("navigator.clipboard.writeText", self.js)
        self.assertIn("Copied!", self.js)
        self.assertIn("copy manually", self.js)
        self.assertIn("fallbackText.select()", self.js)

    def test_format_persistence(self):
        self.assertIn("localStorage.setItem(STORE_KEY, select.value)", self.js)
        self.assertIn("localStorage.getItem(STORE_KEY)", self.js)
        self.assertIn("commanderLab.decklistCopyFormat", self.js)

    def test_toggles_default_off_in_js_query(self):
        for param in ("include_sideboard", "include_maybeboard", "include_printing"):
            self.assertIn(param, self.js)


if __name__ == "__main__":
    unittest.main()

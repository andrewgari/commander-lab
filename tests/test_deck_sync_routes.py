"""Integration tests for the /api/decks/{id}/links and /sync REST endpoints
in app.py.

These hit the routes via FastAPI's TestClient and mock app.deck_links /
app.deck_sync, mirroring tests/test_linked_accounts_routes.py -- these
assert the thin route wrappers call through correctly and translate
DeckLinkError/DeckSyncError into the right HTTP status/response shape, not
that deck_links.py/deck_sync.py's own logic is correct (covered by
tests/test_deck_links.py and tests/test_deck_sync.py).
"""
import unittest
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient

from app import app
from deck_links import DeckLinkError
from deck_sync import DeckSyncError


class TestGetDeckLinks(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.registry")
    @patch("app.deck_links")
    def test_returns_links_for_known_deck(self, mock_links, mock_registry):
        mock_registry.find_deck.return_value = {"id": 42, "links": {"archidekt": {"identifier": "42"}}}
        mock_links.get_links.return_value = {"archidekt": {"identifier": "42"}}

        response = self.client.get("/api/decks/archidekt:42/links")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["links"], {"archidekt": {"identifier": "42"}})

    @patch("app.registry")
    def test_404_for_unknown_deck(self, mock_registry):
        mock_registry.find_deck.return_value = None
        response = self.client.get("/api/decks/nope/links")
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])


class TestSetDeckLink(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.deck_links")
    def test_saves_link_and_returns_updated_links(self, mock_links):
        updated_deck = {"id": 42, "links": {"moxfield": {"identifier": "abc123", "deck_id": "abc123"}}}
        mock_links.set_link.return_value = updated_deck
        mock_links.get_links.return_value = updated_deck["links"]

        response = self.client.post(
            "/api/decks/archidekt:42/links",
            json={"provider": "moxfield", "identifier": "https://moxfield.com/decks/abc123"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["links"]["moxfield"]["deck_id"], "abc123")
        call_args = mock_links.set_link.call_args[0]
        self.assertEqual(call_args[1:], ("archidekt:42", "moxfield", "https://moxfield.com/decks/abc123"))

    @patch("app.deck_links")
    def test_bad_identifier_returns_400(self, mock_links):
        mock_links.set_link.side_effect = DeckLinkError("could not parse an Archidekt deck id from: not-a-deck")

        response = self.client.post(
            "/api/decks/archidekt:42/links",
            json={"provider": "archidekt", "identifier": "not-a-deck"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    @patch("app.deck_links")
    def test_unknown_provider_returns_400(self, mock_links):
        mock_links.set_link.side_effect = DeckLinkError("unknown provider: edhrec")

        response = self.client.post(
            "/api/decks/archidekt:42/links",
            json={"provider": "edhrec", "identifier": "https://edhrec.com/foo"},
        )

        self.assertEqual(response.status_code, 400)


class TestRemoveDeckLink(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.deck_links")
    def test_removes_link(self, mock_links):
        mock_links.remove_link.return_value = {"id": 42, "links": {}}
        mock_links.get_links.return_value = {}

        response = self.client.delete("/api/decks/archidekt:42/links/moxfield")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])
        mock_links.remove_link.assert_called_once()

    @patch("app.deck_links")
    def test_unknown_deck_returns_400(self, mock_links):
        mock_links.remove_link.side_effect = DeckLinkError("deck not found: nope")
        response = self.client.delete("/api/decks/nope/links/moxfield")
        self.assertEqual(response.status_code, 400)


class TestSyncDeckRoute(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.deck_sync")
    def test_successful_sync_returns_report(self, mock_sync):
        report = MagicMock()
        report.to_dict.return_value = {
            "registry_id": "archidekt:42",
            "sources": [
                {"provider": "archidekt", "status": "ok", "card_count": 99, "error": None},
                {"provider": "moxfield", "status": "ok", "card_count": 99, "error": None},
            ],
            "conflicts": [],
            "merged_card_count": 100,
            "warnings": [],
        }
        mock_sync.sync_deck.return_value = report

        response = self.client.post("/api/decks/archidekt:42/sync")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["report"]["merged_card_count"], 100)
        mock_sync.sync_deck.assert_called_once()

    @patch("app.deck_sync")
    def test_partial_failure_still_returns_200_with_warnings(self, mock_sync):
        report = MagicMock()
        report.to_dict.return_value = {
            "registry_id": "archidekt:42",
            "sources": [
                {"provider": "archidekt", "status": "ok", "card_count": 99, "error": None},
                {"provider": "moxfield", "status": "error", "card_count": 0, "error": "timed out"},
            ],
            "conflicts": [],
            "merged_card_count": 99,
            "warnings": ["moxfield: timed out"],
        }
        mock_sync.sync_deck.return_value = report

        response = self.client.post("/api/decks/archidekt:42/sync")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(len(data["report"]["warnings"]), 1)

    @patch("app.deck_sync")
    def test_all_sources_failed_returns_400(self, mock_sync):
        mock_sync.sync_deck.side_effect = DeckSyncError("all linked sources failed for deck archidekt:42: a; b")

        response = self.client.post("/api/decks/archidekt:42/sync")

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    @patch("app.deck_sync")
    def test_no_links_returns_400(self, mock_sync):
        mock_sync.sync_deck.side_effect = DeckSyncError("deck archidekt:42 has no linked Archidekt/Moxfield source to sync from")

        response = self.client.post("/api/decks/archidekt:42/sync")

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()

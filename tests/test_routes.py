from fastapi.testclient import TestClient
import os
import json
import unittest
from unittest.mock import patch, MagicMock, ANY

# Import FastAPI app
from app import app
from instances import InstanceError

class TestAppRoutes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_root_route(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

    def test_decks_route(self):
        response = self.client.get("/decks")
        self.assertEqual(response.status_code, 200)

    def test_inventory_route(self):
        response = self.client.get("/inventory")
        self.assertEqual(response.status_code, 200)

    def test_tags_route(self):
        response = self.client.get("/tags")
        self.assertEqual(response.status_code, 200)

    def test_instances_route(self):
        response = self.client.get("/instances")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])

    def test_deck_view_route(self):
        response = self.client.get("/deck/TestDeck")
        self.assertEqual(response.status_code, 200)

    @patch("app.r")
    def test_api_decks(self, mock_redis):
        mock_redis.get.return_value = '[{"id": 1, "name": "Deck A"}]'
        response = self.client.get("/api/decks")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("decks", data)
        self.assertEqual(len(data["decks"]), 1)

    @patch("app.r")
    def test_api_tags(self, mock_redis):
        mock_redis.get.return_value = '{"Card A": ["Ramp", "Draw"]}'
        response = self.client.get("/api/tags")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("tags", data)
        self.assertEqual(data["tags"], ["Draw", "Ramp"])

if __name__ == "__main__":
    unittest.main()


class TestAddCardToDeck(unittest.TestCase):
    """Integration tests for POST /api/decks/{id}/cards, mocking registry.find_deck
    and the instance_store calls the endpoint delegates to (per the pattern used
    by tests/test_routes.py's @patch("app.r") tests)."""

    def setUp(self):
        self.client = TestClient(app)
        self.deck = {
            "id": 42,
            "name": "Atraxa Superfriends",
            "registry_id": "archidekt:42",
            "status": "physical",
            "cards": [],
        }

    @patch("app.registry")
    @patch("app.instance_store")
    def test_reuses_existing_in_collection_instance(self, mock_store, mock_registry):
        mock_registry.find_deck.return_value = self.deck
        mock_registry.registry_id_of.return_value = "archidekt:42"
        existing_instance = {"id": "inst-1", "card_name": "Sol Ring", "ownership_status": "in_collection"}
        mock_store.list_instances.return_value = [existing_instance]
        mock_store.transition_status.return_value = {**existing_instance, "ownership_status": "in_deck", "deck_id": "archidekt:42"}

        response = self.client.post("/api/decks/archidekt:42/cards", json={"card_name": "Sol Ring"})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        mock_store.create_instance.assert_not_called()
        mock_store.transition_status.assert_called_once()
        _, kwargs = mock_store.transition_status.call_args
        self.assertEqual(kwargs["new_status"], "in_deck")
        self.assertEqual(kwargs["deck_id"], "archidekt:42")
        self.assertTrue(kwargs["_allow_physical_lock_bypass"])
        mock_registry.add_card_to_decklist.assert_called_once_with(ANY, "archidekt:42", "Sol Ring")

    @patch("app.registry")
    @patch("app.instance_store")
    def test_creates_new_instance_when_none_in_collection(self, mock_store, mock_registry):
        mock_registry.find_deck.return_value = self.deck
        mock_registry.registry_id_of.return_value = "archidekt:42"
        mock_store.list_instances.return_value = []
        new_instance = {"id": "inst-2", "card_name": "Arcane Signet", "ownership_status": "in_collection"}
        mock_store.create_instance.return_value = new_instance
        mock_store.transition_status.return_value = {**new_instance, "ownership_status": "in_deck", "deck_id": "archidekt:42"}

        response = self.client.post("/api/decks/archidekt:42/cards", json={"card_name": "Arcane Signet"})

        self.assertEqual(response.status_code, 200)
        mock_store.create_instance.assert_called_once()
        self.assertEqual(mock_store.create_instance.call_args.kwargs["card_name"], "Arcane Signet")
        self.assertEqual(mock_store.create_instance.call_args.kwargs["ownership_status"], "in_collection")
        mock_store.transition_status.assert_called_once_with(
            ANY, "inst-2",
            new_status="in_deck", deck_id="archidekt:42", deck_name="Atraxa Superfriends",
            _allow_physical_lock_bypass=True,
        )

    @patch("app.registry")
    @patch("app.instance_store")
    def test_bypasses_physical_lock(self, mock_store, mock_registry):
        """Deck is status=physical; the endpoint must still succeed by passing
        _allow_physical_lock_bypass=True through to transition_status, rather
        than letting the normal is_deck_physical guard reject the move."""
        physical_deck = {**self.deck, "status": "physical"}
        mock_registry.find_deck.return_value = physical_deck
        mock_registry.registry_id_of.return_value = "archidekt:42"
        existing_instance = {"id": "inst-1", "card_name": "Sol Ring", "ownership_status": "in_collection"}
        mock_store.list_instances.return_value = [existing_instance]
        mock_store.transition_status.return_value = {**existing_instance, "ownership_status": "in_deck"}

        response = self.client.post("/api/decks/archidekt:42/cards", json={"card_name": "Sol Ring"})

        self.assertEqual(response.status_code, 200)
        kwargs = mock_store.transition_status.call_args.kwargs
        self.assertTrue(kwargs["_allow_physical_lock_bypass"])

    @patch("app.registry")
    def test_deck_not_found_returns_404(self, mock_registry):
        mock_registry.find_deck.return_value = None
        response = self.client.post("/api/decks/does-not-exist/cards", json={"card_name": "Sol Ring"})
        self.assertEqual(response.status_code, 404)
        self.assertFalse(response.json()["success"])

    @patch("app.registry")
    def test_missing_card_name_returns_400(self, mock_registry):
        mock_registry.find_deck.return_value = self.deck
        response = self.client.post("/api/decks/archidekt:42/cards", json={})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    @patch("app.registry")
    @patch("app.instance_store")
    def test_instance_error_returns_400(self, mock_store, mock_registry):
        mock_registry.find_deck.return_value = self.deck
        mock_registry.registry_id_of.return_value = "archidekt:42"
        mock_store.list_instances.return_value = []
        mock_store.create_instance.side_effect = InstanceError("invalid ownership_status: bogus")

        response = self.client.post("/api/decks/archidekt:42/cards", json={"card_name": "Sol Ring"})

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])


if __name__ == "__main__":
    unittest.main()

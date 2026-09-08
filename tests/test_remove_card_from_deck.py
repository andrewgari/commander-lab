from fastapi.testclient import TestClient
import unittest
from unittest.mock import patch, MagicMock, ANY

from app import app
from instances import InstanceError


class TestRemoveCardFromDeck(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.instance_store.transition_status")
    @patch("app.instance_store.get_instance")
    @patch("app.registry.find_deck")
    @patch("app.registry.registry_id_of")
    def test_remove_success(self, mock_reg_id, mock_find_deck, mock_get_instance, mock_transition):
        mock_find_deck.return_value = {"id": "archidekt:123", "name": "Test Deck"}
        mock_reg_id.return_value = "archidekt:123"
        mock_get_instance.return_value = {
            "id": "inst-1",
            "ownership_status": "in_deck",
            "deck_id": "archidekt:123",
            "card_name": "Sol Ring",
        }
        mock_transition.return_value = {
            "id": "inst-1",
            "ownership_status": "in_collection",
            "deck_id": None,
            "card_name": "Sol Ring",
        }

        response = self.client.delete("/api/decks/archidekt:123/cards/inst-1")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["instance"]["ownership_status"], "in_collection")
        self.assertEqual(data["instance_id"], "inst-1")

        mock_transition.assert_called_once_with(
            ANY,
            "inst-1",
            new_status="in_collection",
            _allow_physical_lock_bypass=True,
        )

    @patch("app.registry.find_deck")
    def test_remove_deck_not_found(self, mock_find_deck):
        mock_find_deck.return_value = None

        response = self.client.delete("/api/decks/nope/cards/inst-1")

        self.assertEqual(response.status_code, 404)
        self.assertIn("Deck not found", response.json()["error"])

    @patch("app.instance_store.get_instance")
    @patch("app.registry.find_deck")
    @patch("app.registry.registry_id_of")
    def test_remove_instance_not_found(self, mock_reg_id, mock_find_deck, mock_get_instance):
        mock_find_deck.return_value = {"id": "archidekt:123"}
        mock_reg_id.return_value = "archidekt:123"
        mock_get_instance.return_value = None

        response = self.client.delete("/api/decks/archidekt:123/cards/bogus-id")

        self.assertEqual(response.status_code, 404)
        self.assertIn("Instance not found", response.json()["error"])

    @patch("app.instance_store.get_instance")
    @patch("app.registry.find_deck")
    @patch("app.registry.registry_id_of")
    def test_remove_mismatched_deck_id(self, mock_reg_id, mock_find_deck, mock_get_instance):
        mock_find_deck.return_value = {"id": "archidekt:123"}
        mock_reg_id.return_value = "archidekt:123"
        mock_get_instance.return_value = {
            "id": "inst-1",
            "ownership_status": "in_deck",
            "deck_id": "archidekt:999",
            "card_name": "Sol Ring",
        }

        response = self.client.delete("/api/decks/archidekt:123/cards/inst-1")

        self.assertEqual(response.status_code, 404)
        self.assertIn("not bound to this deck", response.json()["error"])

    @patch("app.instance_store.get_instance")
    @patch("app.registry.find_deck")
    @patch("app.registry.registry_id_of")
    def test_remove_instance_not_in_deck(self, mock_reg_id, mock_find_deck, mock_get_instance):
        mock_find_deck.return_value = {"id": "archidekt:123"}
        mock_reg_id.return_value = "archidekt:123"
        mock_get_instance.return_value = {
            "id": "inst-1",
            "ownership_status": "in_collection",
            "deck_id": None,
            "card_name": "Sol Ring",
        }

        response = self.client.delete("/api/decks/archidekt:123/cards/inst-1")

        self.assertEqual(response.status_code, 404)
        self.assertIn("not bound to this deck", response.json()["error"])

    @patch("app.instance_store.transition_status")
    @patch("app.instance_store.get_instance")
    @patch("app.registry.find_deck")
    @patch("app.registry.registry_id_of")
    def test_remove_transition_error(self, mock_reg_id, mock_find_deck, mock_get_instance, mock_transition):
        mock_find_deck.return_value = {"id": "archidekt:123"}
        mock_reg_id.return_value = "archidekt:123"
        mock_get_instance.return_value = {
            "id": "inst-1",
            "ownership_status": "in_deck",
            "deck_id": "archidekt:123",
            "card_name": "Sol Ring",
        }
        mock_transition.side_effect = InstanceError("illegal transition")

        response = self.client.delete("/api/decks/archidekt:123/cards/inst-1")

        self.assertEqual(response.status_code, 400)
        self.assertIn("illegal transition", response.json()["error"])


if __name__ == "__main__":
    unittest.main()

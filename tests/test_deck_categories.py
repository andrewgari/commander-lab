from fastapi.testclient import TestClient
import unittest
from unittest.mock import patch, MagicMock
import json

from app import app


class TestDeckCategories(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_get_deck_categories(self, mock_redis, mock_find_deck):
        mock_find_deck.return_value = {"id": "archidekt:123", "name": "Test Deck"}
        mock_redis.get.return_value = json.dumps({"Sol Ring": ["Ramp"]})

        response = self.client.get("/api/decks/archidekt:123/categories")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["deck_id"], "archidekt:123")
        self.assertEqual(data["categories"], {"Sol Ring": ["Ramp"]})
        mock_redis.get.assert_called_with("deck_categories:archidekt:123")

    @patch("app.registry.find_deck")
    def test_get_deck_categories_missing_deck(self, mock_find_deck):
        mock_find_deck.return_value = None

        response = self.client.get("/api/decks/nope/categories")

        self.assertEqual(response.status_code, 404)

    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_get_deck_categories_empty(self, mock_redis, mock_find_deck):
        mock_find_deck.return_value = {"id": "archidekt:123"}
        mock_redis.get.return_value = None

        response = self.client.get("/api/decks/archidekt:123/categories")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["categories"], {})

    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_post_add_tag_new_card(self, mock_redis, mock_find_deck):
        mock_find_deck.return_value = {"id": "archidekt:123"}
        mock_redis.get.return_value = json.dumps({})

        response = self.client.post(
            "/api/decks/archidekt:123/categories",
            json={"card_name": "Sol Ring", "action": "add", "tag": "Ramp"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["categories"], ["Ramp"])
        saved = json.loads(mock_redis.set.call_args[0][1])
        self.assertEqual(saved, {"Sol Ring": ["Ramp"]})

    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_post_add_tag_existing_card(self, mock_redis, mock_find_deck):
        mock_find_deck.return_value = {"id": "archidekt:123"}
        mock_redis.get.return_value = json.dumps({"Sol Ring": ["Ramp"]})

        response = self.client.post(
            "/api/decks/archidekt:123/categories",
            json={"card_name": "Sol Ring", "action": "add", "tag": "Card Draw"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(sorted(data["categories"]), ["Card Draw", "Ramp"])

    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_post_remove_tag(self, mock_redis, mock_find_deck):
        mock_find_deck.return_value = {"id": "archidekt:123"}
        mock_redis.get.return_value = json.dumps({"Sol Ring": ["Ramp", "Card Draw"]})

        response = self.client.post(
            "/api/decks/archidekt:123/categories",
            json={"card_name": "Sol Ring", "action": "remove", "tag": "Ramp"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["categories"], ["Card Draw"])
        saved = json.loads(mock_redis.set.call_args[0][1])
        self.assertEqual(saved, {"Sol Ring": ["Card Draw"]})

    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_post_remove_last_tag_drops_entry(self, mock_redis, mock_find_deck):
        mock_find_deck.return_value = {"id": "archidekt:123"}
        mock_redis.get.return_value = json.dumps({"Sol Ring": ["Ramp"]})

        response = self.client.post(
            "/api/decks/archidekt:123/categories",
            json={"card_name": "Sol Ring", "action": "remove", "tag": "Ramp"},
        )

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["categories"], [])
        saved = json.loads(mock_redis.set.call_args[0][1])
        self.assertEqual(saved, {})

    @patch("app.registry.find_deck")
    def test_post_missing_deck(self, mock_find_deck):
        mock_find_deck.return_value = None

        response = self.client.post(
            "/api/decks/nope/categories",
            json={"card_name": "Sol Ring", "action": "add", "tag": "Ramp"},
        )

        self.assertEqual(response.status_code, 404)

    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_post_invalid_action(self, mock_redis, mock_find_deck):
        mock_find_deck.return_value = {"id": "archidekt:123"}

        response = self.client.post(
            "/api/decks/archidekt:123/categories",
            json={"card_name": "Sol Ring", "action": "bogus", "tag": "Ramp"},
        )

        self.assertEqual(response.status_code, 400)

    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_post_missing_fields(self, mock_redis, mock_find_deck):
        mock_find_deck.return_value = {"id": "archidekt:123"}

        response = self.client.post(
            "/api/decks/archidekt:123/categories",
            json={"action": "add", "tag": "Ramp"},
        )

        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()

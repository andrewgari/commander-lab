from fastapi.testclient import TestClient
import os
import unittest
from unittest.mock import patch, MagicMock

# Import FastAPI app
from app import app

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

from fastapi.testclient import TestClient
import unittest
from unittest.mock import patch
import json

from app import app


class TestDeckSearch(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_search_matches_case_insensitive_substring(self, mock_redis, mock_find_deck):
        mock_find_deck.return_value = {"id": "archidekt:123", "name": "Test Deck"}
        mock_redis.keys.return_value = [
            "card_meta:Sol Ring",
            "card_meta:Solemn Simulacrum",
            "card_meta:Lightning Bolt",
        ]
        mock_redis.get.side_effect = lambda key: {
            "card_meta:Sol Ring": json.dumps({"type": "Artifact", "cmc": 1}),
            "card_meta:Solemn Simulacrum": json.dumps({"type": "Artifact Creature", "cmc": 4}),
            "card_meta:Lightning Bolt": json.dumps({"type": "Instant", "cmc": 1}),
        }[key]

        response = self.client.get("/api/decks/archidekt:123/search?q=sol")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["deck_id"], "archidekt:123")
        names = sorted(c["name"] for c in data["results"])
        self.assertEqual(names, ["Sol Ring", "Solemn Simulacrum"])
        for card in data["results"]:
            self.assertIn("name", card)
            self.assertIn("type", card)
            self.assertIn("cmc", card)

    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_search_no_matches(self, mock_redis, mock_find_deck):
        mock_find_deck.return_value = {"id": "archidekt:123"}
        mock_redis.keys.return_value = ["card_meta:Sol Ring"]
        mock_redis.get.return_value = json.dumps({"type": "Artifact", "cmc": 1})

        response = self.client.get("/api/decks/archidekt:123/search?q=zzzzz")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_search_empty_query_returns_empty_results(self, mock_redis, mock_find_deck):
        mock_find_deck.return_value = {"id": "archidekt:123"}

        response = self.client.get("/api/decks/archidekt:123/search")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["results"], [])
        mock_redis.keys.assert_not_called()

    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_search_whitespace_only_query_returns_empty_results(self, mock_redis, mock_find_deck):
        mock_find_deck.return_value = {"id": "archidekt:123"}

        response = self.client.get("/api/decks/archidekt:123/search?q=%20%20")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], [])

    @patch("app.registry.find_deck")
    def test_search_missing_deck_returns_404(self, mock_find_deck):
        mock_find_deck.return_value = None

        response = self.client.get("/api/decks/nope/search?q=sol")

        self.assertEqual(response.status_code, 404)

    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_search_result_limit(self, mock_redis, mock_find_deck):
        mock_find_deck.return_value = {"id": "archidekt:123"}
        keys = [f"card_meta:Card {i:03d}" for i in range(75)]
        mock_redis.keys.return_value = keys
        mock_redis.get.return_value = json.dumps({"type": "Creature", "cmc": 2})

        response = self.client.get("/api/decks/archidekt:123/search?q=card")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertLessEqual(len(data["results"]), 50)

    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_search_skips_malformed_json(self, mock_redis, mock_find_deck):
        mock_find_deck.return_value = {"id": "archidekt:123"}
        mock_redis.keys.return_value = ["card_meta:Sol Ring", "card_meta:Broken Card"]
        mock_redis.get.side_effect = lambda key: {
            "card_meta:Sol Ring": json.dumps({"type": "Artifact", "cmc": 1}),
            "card_meta:Broken Card": "not valid json",
        }[key]

        response = self.client.get("/api/decks/archidekt:123/search?q=card")

        self.assertEqual(response.status_code, 200)
        names = [c["name"] for c in response.json()["results"]]
        # Broken Card should be skipped due to invalid JSON; only cards that
        # actually match "card" and parse cleanly should show up.
        self.assertNotIn("Broken Card", names)


if __name__ == "__main__":
    unittest.main()

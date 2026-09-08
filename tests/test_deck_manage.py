from fastapi.testclient import TestClient
import unittest
from unittest.mock import patch, MagicMock
import json

from app import app


def _pipeline_returning(values):
    """Build a fake redis pipeline whose .execute() returns `values` in order,
    matching how app.py batches card_meta lookups via r.pipeline()."""
    pipe = MagicMock()
    pipe.execute.return_value = values
    return pipe


class TestDeckManage(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.instance_store.list_instances")
    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_manage_aggregate_full_response(self, mock_redis, mock_find_deck, mock_list_instances):
        mock_find_deck.return_value = {"id": "archidekt:123", "registry_id": "archidekt:123", "name": "Test Deck", "status": "physical"}
        mock_list_instances.return_value = [
            {"id": "inst-1", "card_name": "Sol Ring", "ownership_status": "in_deck", "deck_id": "archidekt:123"},
            {"id": "inst-2", "card_name": "Lightning Bolt", "ownership_status": "in_deck", "deck_id": "archidekt:123"},
        ]

        def mock_get(key):
            return {
                "deck_categories:archidekt:123": json.dumps({"Sol Ring": ["Fast Mana"]}),
                "lab_tags": json.dumps({"Sol Ring": ["Ramp"], "Lightning Bolt": ["Removal"]}),
            }.get(key)

        mock_redis.get.side_effect = mock_get
        mock_redis.pipeline.return_value = _pipeline_returning([
            json.dumps({"type": "Instant", "cmc": 1, "color": "R"}),
            json.dumps({"type": "Artifact", "cmc": 1, "color": "C"}),
        ])

        response = self.client.get("/api/decks/archidekt:123/manage")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["deck_id"], "archidekt:123")
        self.assertEqual(data["deck_name"], "Test Deck")
        self.assertEqual(data["status"], "physical")
        self.assertEqual(len(data["cards"]), 2)

        by_name = {c["card_name"]: c for c in data["cards"]}

        sol_ring = by_name["Sol Ring"]
        self.assertEqual(sol_ring["instance_id"], "inst-1")
        self.assertEqual(sol_ring["type"], "Artifact")
        self.assertEqual(sol_ring["cmc"], 1)
        self.assertEqual(sol_ring["lab_tags"], ["Ramp"])
        self.assertEqual(sol_ring["deck_categories"], ["Fast Mana"])
        # Deck category override wins over the global lab tag.
        self.assertEqual(sol_ring["effective_category"], "Fast Mana")

        bolt = by_name["Lightning Bolt"]
        self.assertEqual(bolt["instance_id"], "inst-2")
        self.assertEqual(bolt["lab_tags"], ["Removal"])
        self.assertEqual(bolt["deck_categories"], [])
        # No deck override -> falls back to the global lab tag.
        self.assertEqual(bolt["effective_category"], "Removal")

        self.assertEqual(data["deck_categories"], {"Sol Ring": ["Fast Mana"]})

    @patch("app.instance_store.list_instances")
    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_manage_uncategorized_fallback(self, mock_redis, mock_find_deck, mock_list_instances):
        mock_find_deck.return_value = {"id": "archidekt:123", "registry_id": "archidekt:123", "name": "Test Deck"}
        mock_list_instances.return_value = [
            {"id": "inst-1", "card_name": "Unknown Card", "ownership_status": "in_deck", "deck_id": "archidekt:123"},
        ]
        mock_redis.get.return_value = None
        mock_redis.pipeline.return_value = _pipeline_returning([None])

        response = self.client.get("/api/decks/archidekt:123/manage")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        card = data["cards"][0]
        self.assertEqual(card["type"], "Unknown")
        self.assertEqual(card["cmc"], 0)
        self.assertEqual(card["lab_tags"], [])
        self.assertEqual(card["deck_categories"], [])
        self.assertEqual(card["effective_category"], "Uncategorized")

    @patch("app.instance_store.list_instances")
    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_manage_empty_deck_no_instances(self, mock_redis, mock_find_deck, mock_list_instances):
        mock_find_deck.return_value = {"id": "archidekt:123", "registry_id": "archidekt:123", "name": "Empty Deck"}
        mock_list_instances.return_value = []
        mock_redis.get.return_value = None

        response = self.client.get("/api/decks/archidekt:123/manage")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["cards"], [])
        self.assertEqual(data["deck_categories"], {})
        # No cards bound -> the pipeline should never even be built.
        mock_redis.pipeline.assert_not_called()

    @patch("app.registry.find_deck")
    def test_manage_missing_deck_404(self, mock_find_deck):
        mock_find_deck.return_value = None

        response = self.client.get("/api/decks/nope/manage")

        self.assertEqual(response.status_code, 404)

    @patch("app.instance_store.list_instances")
    @patch("app.registry.find_deck")
    @patch("app.r")
    def test_manage_numeric_id_reads_categories_from_matching_key(
        self, mock_redis, mock_find_deck, mock_list_instances
    ):
        """Regression test: /api/decks/{deck_id}/manage must read deck
        category overrides from the SAME key as
        /api/decks/{deck_id}/categories — i.e. keyed by the raw URL
        deck_id (here the numeric deck.id), not registry_id — otherwise
        overrides written via /categories for numeric-id decks are
        silently dropped by /manage.
        """
        mock_find_deck.return_value = {
            "id": 123,
            "registry_id": "archidekt:123",
            "name": "Test Deck",
            "status": "physical",
        }
        mock_list_instances.return_value = [
            {"id": "inst-1", "card_name": "Sol Ring", "ownership_status": "in_deck", "deck_id": "archidekt:123"},
        ]

        def mock_get(key):
            return {
                # Keyed by the numeric URL deck_id, matching what
                # /api/decks/{deck_id}/categories would read/write for
                # this same URL.
                "deck_categories:123": json.dumps({"Sol Ring": ["Fast Mana"]}),
                "lab_tags": json.dumps({"Sol Ring": ["Ramp"]}),
            }.get(key)

        mock_redis.get.side_effect = mock_get
        mock_redis.pipeline.return_value = _pipeline_returning([
            json.dumps({"type": "Artifact", "cmc": 1, "color": "C"}),
        ])

        response = self.client.get("/api/decks/123/manage")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        # deck_id in the response must match the URL deck_id used by other
        # deck endpoints, not registry_id.
        self.assertEqual(data["deck_id"], "123")
        self.assertEqual(data["deck_categories"], {"Sol Ring": ["Fast Mana"]})

        sol_ring = data["cards"][0]
        self.assertEqual(sol_ring["deck_categories"], ["Fast Mana"])
        self.assertEqual(sol_ring["effective_category"], "Fast Mana")


if __name__ == "__main__":
    unittest.main()

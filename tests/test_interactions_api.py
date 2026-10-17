"""
Route tests for the AN-1 interaction graph API (thin wrappers in app.py over
analytics.interactions.service). Service logic is covered by
tests/test_interactions.py; these assert routing, deck lookup by id /
registry_id / name, 404s and the refresh trigger.
"""
import os
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app import app

DECK = {"id": 417707, "name": "Holy Hellfire", "registry_id": "archidekt:417707",
        "cards": [{"name": "Sol Ring", "quantity": 1}]}
GRAPH = {"deck": {"registry_id": "archidekt:417707", "name": "Holy Hellfire"},
         "counts": {"nodes": 1, "edges": 0, "untagged": 0}, "nodes": [], "edges": [], "top_connected": []}


class TestDeckGraphRoute(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.interactions_service")
    @patch("app.registry")
    def test_by_name(self, mock_registry, mock_service):
        mock_registry.find_deck.return_value = None
        mock_registry.list_decks.return_value = [DECK]
        mock_service.get_deck_graph.return_value = GRAPH
        res = self.client.get("/api/decks/Holy Hellfire/interactions")
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["success"])
        self.assertEqual(body["graph"]["counts"]["nodes"], 1)
        mock_service.get_deck_graph.assert_called_once()
        self.assertEqual(mock_service.get_deck_graph.call_args[0][1]["registry_id"], "archidekt:417707")

    @patch("app.interactions_service")
    @patch("app.registry")
    def test_by_registry_id(self, mock_registry, mock_service):
        mock_registry.find_deck.return_value = DECK
        mock_service.get_deck_graph.return_value = GRAPH
        res = self.client.get("/api/decks/archidekt:417707/interactions")
        self.assertEqual(res.status_code, 200)

    @patch("app.registry")
    def test_unknown_deck_404(self, mock_registry):
        mock_registry.find_deck.return_value = None
        mock_registry.list_decks.return_value = []
        res = self.client.get("/api/decks/nope/interactions")
        self.assertEqual(res.status_code, 404)
        self.assertFalse(res.json()["success"])


class TestRefreshRoutes(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.interactions_service")
    def test_refresh_all_starts_background_rebuild(self, mock_service):
        mock_service.start_rebuild.return_value = {"started": True, "trigger": "manual"}
        res = self.client.post("/api/interactions/refresh")
        self.assertEqual(res.status_code, 202)
        mock_service.start_rebuild.assert_called_once()
        self.assertEqual(mock_service.start_rebuild.call_args.kwargs["trigger"], "manual")

    @patch("app.interactions_service")
    def test_refresh_wait_runs_inline_and_returns_run(self, mock_service):
        mock_service.rebuild.return_value = {"run_id": "x", "status": "ok"}
        res = self.client.post("/api/interactions/refresh?wait=true")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["run"]["status"], "ok")

    @patch("app.interactions_service")
    @patch("app.registry")
    def test_refresh_one_deck(self, mock_registry, mock_service):
        mock_registry.find_deck.return_value = DECK
        mock_service.start_rebuild.return_value = {"started": True}
        res = self.client.post("/api/decks/archidekt:417707/interactions/refresh")
        self.assertEqual(res.status_code, 202)
        self.assertEqual(mock_service.start_rebuild.call_args.kwargs["deck_ids"], ["archidekt:417707"])

    @patch("app.interactions_service")
    def test_runs_and_collection(self, mock_service):
        mock_service.list_runs.return_value = [{"run_id": "a"}]
        mock_service.get_collection_graph.return_value = {"counts": {"nodes": 3, "edges": 2}, "nodes": [], "index": {}}
        self.assertEqual(self.client.get("/api/interactions/runs").json()["runs"], [{"run_id": "a"}])
        body = self.client.get("/api/interactions/collection").json()
        self.assertEqual(body["graph"]["counts"]["edges"], 2)

    @patch("app.interactions_service")
    def test_collection_missing_is_404(self, mock_service):
        mock_service.get_collection_graph.return_value = None
        self.assertEqual(self.client.get("/api/interactions/collection").status_code, 404)


class TestSyncTriggersRebuild(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("app.interactions_service")
    @patch("app.deck_sync")
    def test_deck_sync_route_triggers_scoped_rebuild(self, mock_sync, mock_service):
        class Rep:
            def to_dict(self):
                return {"ok": True}
        mock_sync.sync_deck.return_value = Rep()
        res = self.client.post("/api/decks/archidekt:417707/sync")
        self.assertEqual(res.status_code, 200)
        mock_service.start_rebuild.assert_called_once()
        self.assertEqual(mock_service.start_rebuild.call_args.kwargs["trigger"], "sync")

    @patch("app.interactions_service")
    @patch("app.linked_accounts")
    def test_sync_all_triggers_rebuild(self, mock_la, mock_service):
        mock_la.sync_all.return_value = []
        res = self.client.post("/api/linked-accounts/sync-all")
        self.assertEqual(res.status_code, 200)
        mock_service.start_rebuild.assert_called_once()


if __name__ == "__main__":
    unittest.main()

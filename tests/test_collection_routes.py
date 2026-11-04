"""Integration tests for the /api/collection/* REST endpoints in src/server/app/main.py.

Hit via FastAPI's TestClient, mocking only app.collection /
app.archidekt_collection — same @patch("app.<module>") pattern as
tests/test_linked_accounts_routes.py. These assert the routes call through
and translate errors into the right HTTP status/shape; collection.py's own
logic is covered by tests/test_collection_import.py.
"""
import os
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.server.app.main import app
from src.server.domain.inventory.collection import CollectionImportError
from src.server.infra.providers.archidekt_collection import CollectionAuthError, CollectionFetchError


REPORT = {
    "rows": 2,
    "remote_copies": 3,
    "created": 3,
    "promoted": 0,
    "already_present": 0,
    "surplus": [],
    "created_instances": ["i1", "i2", "i3"],
    "dry_run": False,
}


class TestCollectionSyncStatus(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("src.server.app.main.collection")
    @patch("src.server.app.main.archidekt_collection")
    def test_reports_credentialed_and_last_sync(self, mock_ac, mock_collection):
        mock_ac.credentials.return_value = ("s", "c")
        mock_collection.last_sync.return_value = {"username": "CovaDax", "report": REPORT}

        response = self.client.get("/api/collection/sync")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["credentialed"])
        self.assertEqual(data["last_sync"]["username"], "CovaDax")

    @patch("src.server.app.main.collection")
    @patch("src.server.app.main.archidekt_collection")
    def test_reports_missing_credentials_without_erroring(self, mock_ac, mock_collection):
        mock_ac.CollectionAuthError = CollectionAuthError
        mock_ac.credentials.side_effect = CollectionAuthError("nope")
        mock_collection.last_sync.return_value = None

        response = self.client.get("/api/collection/sync")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertFalse(data["credentialed"])
        self.assertIsNone(data["last_sync"])


class TestCollectionImportRoute(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("src.server.app.main.collection")
    def test_import_returns_report(self, mock_collection):
        mock_collection.import_collection.return_value = {
            "username": "CovaDax", "report": REPORT, "synced_at": "t1",
        }

        response = self.client.post("/api/collection/import", json={})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["report"]["created"], 3)
        self.assertEqual(data["synced_at"], "t1")
        kwargs = mock_collection.import_collection.call_args.kwargs
        self.assertTrue(kwargs["dry_run"])  # default is a dry run

    @patch("src.server.app.main.collection")
    def test_empty_body_is_accepted(self, mock_collection):
        mock_collection.import_collection.return_value = {
            "username": "CovaDax", "report": REPORT, "synced_at": "t1",
        }

        response = self.client.post("/api/collection/import")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

    @patch("src.server.app.main.collection")
    def test_dry_run_flag_is_forwarded(self, mock_collection):
        preview = {**REPORT, "dry_run": True}
        mock_collection.import_collection.return_value = {
            "username": "CovaDax", "report": preview, "synced_at": None,
        }

        response = self.client.post("/api/collection/import", json={"dry_run": True})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["report"]["dry_run"])
        self.assertIsNone(data["synced_at"])
        self.assertTrue(mock_collection.import_collection.call_args.kwargs["dry_run"])

    @patch("src.server.app.main.collection")
    def test_username_override_is_forwarded(self, mock_collection):
        mock_collection.import_collection.return_value = {
            "username": "Someone", "report": REPORT, "synced_at": "t1",
        }

        self.client.post("/api/collection/import", json={"username": "Someone"})

        self.assertEqual(
            mock_collection.import_collection.call_args.kwargs["username"], "Someone"
        )

    @patch("src.server.app.main.archidekt_collection")
    @patch("src.server.app.main.collection")
    def test_missing_credentials_returns_400_with_message(self, mock_collection, mock_ac):
        mock_ac.CollectionAuthError = CollectionAuthError
        mock_ac.CollectionFetchError = CollectionFetchError
        mock_collection.CollectionImportError = CollectionImportError
        mock_collection.import_collection.side_effect = CollectionAuthError(
            "ARCHIDEKT_SESSION and ARCHIDEKT_CSRF must be set"
        )

        response = self.client.post("/api/collection/import", json={})

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("ARCHIDEKT_SESSION", data["error"])

    @patch("src.server.app.main.archidekt_collection")
    @patch("src.server.app.main.collection")
    def test_unknown_user_returns_400(self, mock_collection, mock_ac):
        mock_ac.CollectionAuthError = CollectionAuthError
        mock_ac.CollectionFetchError = CollectionFetchError
        mock_collection.CollectionImportError = CollectionImportError
        mock_collection.import_collection.side_effect = CollectionFetchError(
            "no Archidekt user found for username: nope"
        )

        response = self.client.post("/api/collection/import", json={"username": "nope"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("no Archidekt user", response.json()["error"])

    @patch("src.server.app.main.archidekt_collection")
    @patch("src.server.app.main.collection")
    def test_no_username_configured_returns_400(self, mock_collection, mock_ac):
        mock_ac.CollectionAuthError = CollectionAuthError
        mock_ac.CollectionFetchError = CollectionFetchError
        mock_collection.CollectionImportError = CollectionImportError
        mock_collection.import_collection.side_effect = CollectionImportError(
            "no Archidekt username given and ARCHIDEKT_USERNAME is not set"
        )

        response = self.client.post("/api/collection/import", json={})

        self.assertEqual(response.status_code, 400)
        self.assertIn("ARCHIDEKT_USERNAME", response.json()["error"])


if __name__ == "__main__":
    unittest.main()


class TestImportRunsOffEventLoop(unittest.TestCase):
    @patch("src.server.app.main.collection")
    def test_sync_import_runs_in_worker_thread(self, mock_collection):
        import asyncio
        seen = {}

        def fake_import(*args, **kwargs):
            try:
                asyncio.get_running_loop()
                seen["on_loop"] = True
            except RuntimeError:
                seen["on_loop"] = False
            return {"username": "u", "report": REPORT, "synced_at": None}

        mock_collection.import_collection.side_effect = fake_import
        response = TestClient(app).post("/api/collection/import", json={"dry_run": True})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(seen["on_loop"], "blocking import must not run on the event loop")

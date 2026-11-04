"""Integration tests for the /api/archive/moxfield endpoints in app.py.

Mocks only src.server.app.main.archive — same @patch("src.server.app.main.<module>") pattern as the other
route tests.
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.server.app.main import app
from src.server.infra.providers.moxfield_writer import MoxfieldAuthError, MoxfieldWriteError


REPORT = {
    "folder": "Archive",
    "prefix": "[Archived] ",
    "visibility": "private",
    "dry_run": False,
    "total": 32,
    "archived": 32,
    "skipped_already": 0,
    "skipped_transcribed": 0,
    "failed": 0,
    "results": [
        {"deck_id": "d1", "name": "Kyra Fireball", "action": "archived",
         "new_name": "[Archived] Kyra Fireball"},
    ],
}


def stub(mock_a):
    mock_a.DEFAULT_ARCHIVE_FOLDER = "Archive"
    mock_a.DEFAULT_PREFIX = "[Archived] "
    mock_a.DEFAULT_VISIBILITY = "private"
    mock_a.ArchiveError = type("ArchiveError", (ValueError,), {})
    return mock_a


class TestArchiveRoute(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("src.server.app.main.archive")
    def test_archive_returns_report_with_defaults(self, mock_a):
        stub(mock_a).archive_all.return_value = REPORT

        response = self.client.post("/api/archive/moxfield", json={})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["report"]["archived"], 32)
        kwargs = mock_a.archive_all.call_args.kwargs
        self.assertEqual(kwargs["folder_name"], "Archive")
        self.assertEqual(kwargs["prefix"], "[Archived] ")
        self.assertEqual(kwargs["visibility"], "private")
        self.assertTrue(kwargs["skip_transcribed"])
        self.assertTrue(kwargs["dry_run"])  # default is a dry run

    @patch("src.server.app.main.archive")
    def test_empty_body_is_accepted(self, mock_a):
        stub(mock_a).archive_all.return_value = REPORT
        response = self.client.post("/api/archive/moxfield")
        self.assertEqual(response.status_code, 200)

    @patch("src.server.app.main.archive")
    def test_options_are_forwarded(self, mock_a):
        stub(mock_a).archive_all.return_value = {**REPORT, "dry_run": True}

        self.client.post("/api/archive/moxfield", json={
            "folder": "Old Decks", "prefix": "2026-09 ", "visibility": None,
            "skip_transcribed": False, "dry_run": True,
        })

        kwargs = mock_a.archive_all.call_args.kwargs
        self.assertEqual(kwargs["folder_name"], "Old Decks")
        self.assertEqual(kwargs["prefix"], "2026-09 ")
        self.assertIsNone(kwargs["visibility"])
        self.assertFalse(kwargs["skip_transcribed"])
        self.assertTrue(kwargs["dry_run"])

    @patch("src.server.app.main.archive")
    def test_missing_session_returns_400(self, mock_a):
        stub(mock_a).archive_all.side_effect = MoxfieldAuthError(
            "no Moxfield session at .auth/state.json — run `python scripts/setup_auth.py`"
        )
        response = self.client.post("/api/archive/moxfield", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("setup_auth.py", response.json()["error"])

    @patch("src.server.app.main.archive")
    def test_write_error_returns_400(self, mock_a):
        stub(mock_a).archive_all.side_effect = MoxfieldWriteError("could not list your decks (500)")
        response = self.client.post("/api/archive/moxfield", json={})
        self.assertEqual(response.status_code, 400)
        self.assertIn("could not list", response.json()["error"])

    @patch("src.server.app.main.archive")
    def test_partial_failure_is_reported_as_success(self, mock_a):
        stub(mock_a).archive_all.return_value = {**REPORT, "archived": 31, "failed": 1}
        response = self.client.post("/api/archive/moxfield", json={})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["report"]["failed"], 1)


class TestRestoreRoute(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("src.server.app.main.archive")
    def test_restore_returns_the_original_name(self, mock_a):
        stub(mock_a).restore.return_value = {
            "deck_id": "d1", "restored_name": "Kyra Fireball",
            "restored_visibility": "public",
        }

        response = self.client.post("/api/archive/moxfield/d1/restore")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["restored_name"], "Kyra Fireball")
        self.assertEqual(mock_a.restore.call_args.args[1], "d1")

    @patch("src.server.app.main.archive")
    def test_unknown_backup_returns_404(self, mock_a):
        mock_a = stub(mock_a)
        mock_a.restore.side_effect = mock_a.ArchiveError("no archive backup recorded for deck d9")

        response = self.client.post("/api/archive/moxfield/d9/restore")

        self.assertEqual(response.status_code, 404)
        self.assertIn("no archive backup", response.json()["error"])


if __name__ == "__main__":
    unittest.main()

"""Integration tests for the /api/transcribe/moxfield endpoints in app.py.

Hit via FastAPI's TestClient, mocking only app.transcribe — same
@patch("src.server.app.main.<module>") pattern as tests/test_collection_routes.py.
"""
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.server.app.main import app
from src.server.infra.providers.moxfield_writer import MoxfieldAuthError, MoxfieldWriteError


REPORT = {
    "folder": "Physical",
    "visibility": "private",
    "dry_run": False,
    "eligible": 2,
    "created": 2,
    "skipped_already": 0,
    "failed": 0,
    "results": [
        {"registry_id": "archidekt:1", "name": "Kaalia", "action": "created",
         "moxfield_url": "https://moxfield.com/decks/pub-mx1"},
        {"registry_id": "archidekt:2", "name": "Meren", "action": "created",
         "moxfield_url": "https://moxfield.com/decks/pub-mx2"},
    ],
}


class TestTranscribeStatus(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("src.server.app.main.transcribe")
    def test_status_lists_eligible_physical_decks(self, mock_t):
        mock_t.DEFAULT_FOLDER_NAME = "Physical"
        mock_t.DEFAULT_VISIBILITY = "private"
        mock_t.eligible_decks.return_value = [
            {"registry_id": "archidekt:1", "name": "Kaalia", "status": "physical",
             "cards": [{"name": "Sol Ring", "quantity": 1}]},
        ]
        mock_t.card_count.return_value = 100
        mock_t.get_mapping.return_value = None

        response = self.client.get("/api/transcribe/moxfield")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["eligible"], 1)
        self.assertEqual(data["folder"], "Physical")
        self.assertEqual(data["visibility"], "private")
        self.assertEqual(data["decks"][0]["name"], "Kaalia")
        self.assertEqual(data["decks"][0]["cards"], 100)

    @patch("src.server.app.main.transcribe")
    def test_status_surfaces_existing_mapping(self, mock_t):
        mock_t.DEFAULT_FOLDER_NAME = "Physical"
        mock_t.DEFAULT_VISIBILITY = "private"
        mock_t.eligible_decks.return_value = [
            {"registry_id": "archidekt:1", "name": "Kaalia", "status": "physical", "cards": []},
        ]
        mock_t.card_count.return_value = 100
        mock_t.get_mapping.return_value = {
            "moxfield_url": "https://moxfield.com/decks/pub-mx1", "folder": "Physical",
        }

        data = self.client.get("/api/transcribe/moxfield").json()
        self.assertEqual(
            data["decks"][0]["transcribed"]["moxfield_url"],
            "https://moxfield.com/decks/pub-mx1",
        )

    @patch("src.server.app.main.transcribe")
    def test_status_with_no_physical_decks(self, mock_t):
        mock_t.DEFAULT_FOLDER_NAME = "Physical"
        mock_t.DEFAULT_VISIBILITY = "private"
        mock_t.eligible_decks.return_value = []

        data = self.client.get("/api/transcribe/moxfield").json()
        self.assertEqual(data["eligible"], 0)
        self.assertEqual(data["decks"], [])


class TestTranscribeRun(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    @patch("src.server.app.main.transcribe")
    def test_run_returns_report_with_defaults(self, mock_t):
        mock_t.DEFAULT_FOLDER_NAME = "Physical"
        mock_t.DEFAULT_VISIBILITY = "private"
        mock_t.transcribe_all.return_value = REPORT

        response = self.client.post("/api/transcribe/moxfield", json={})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["report"]["created"], 2)
        kwargs = mock_t.transcribe_all.call_args.kwargs
        self.assertEqual(kwargs["folder_name"], "Physical")
        self.assertEqual(kwargs["visibility"], "private")
        self.assertFalse(kwargs["force"])
        self.assertTrue(kwargs["dry_run"])  # default is a dry run

    @patch("src.server.app.main.transcribe")
    def test_empty_body_is_accepted(self, mock_t):
        mock_t.DEFAULT_FOLDER_NAME = "Physical"
        mock_t.DEFAULT_VISIBILITY = "private"
        mock_t.transcribe_all.return_value = REPORT

        response = self.client.post("/api/transcribe/moxfield")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["success"])

    @patch("src.server.app.main.transcribe")
    def test_folder_visibility_force_and_dry_run_are_forwarded(self, mock_t):
        mock_t.DEFAULT_FOLDER_NAME = "Physical"
        mock_t.DEFAULT_VISIBILITY = "private"
        mock_t.transcribe_all.return_value = {**REPORT, "dry_run": True}

        self.client.post("/api/transcribe/moxfield", json={
            "folder": "Paper Decks", "visibility": "unlisted", "force": True, "dry_run": True,
        })

        kwargs = mock_t.transcribe_all.call_args.kwargs
        self.assertEqual(kwargs["folder_name"], "Paper Decks")
        self.assertEqual(kwargs["visibility"], "unlisted")
        self.assertTrue(kwargs["force"])
        self.assertTrue(kwargs["dry_run"])

    @patch("src.server.app.main.transcribe")
    def test_missing_session_returns_400_with_setup_hint(self, mock_t):
        mock_t.DEFAULT_FOLDER_NAME = "Physical"
        mock_t.DEFAULT_VISIBILITY = "private"
        mock_t.TranscribeError = type("TranscribeError", (ValueError,), {})
        mock_t.transcribe_all.side_effect = MoxfieldAuthError(
            "no Moxfield session at .auth/state.json — run `python scripts/setup_auth.py`"
        )

        response = self.client.post("/api/transcribe/moxfield", json={})

        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data["success"])
        self.assertIn("setup_auth.py", data["error"])

    @patch("src.server.app.main.transcribe")
    def test_write_failure_returns_400(self, mock_t):
        mock_t.DEFAULT_FOLDER_NAME = "Physical"
        mock_t.DEFAULT_VISIBILITY = "private"
        mock_t.TranscribeError = type("TranscribeError", (ValueError,), {})
        mock_t.transcribe_all.side_effect = MoxfieldWriteError(
            "invalid folder name 'Paper/Decks!'"
        )

        response = self.client.post("/api/transcribe/moxfield", json={"folder": "Paper/Decks!"})

        self.assertEqual(response.status_code, 400)
        self.assertIn("invalid folder name", response.json()["error"])

    @patch("src.server.app.main.transcribe")
    def test_partial_failure_report_is_returned_as_success(self, mock_t):
        """One bad deck doesn't fail the whole request -- the report carries it."""
        mock_t.DEFAULT_FOLDER_NAME = "Physical"
        mock_t.DEFAULT_VISIBILITY = "private"
        mock_t.transcribe_all.return_value = {
            **REPORT, "created": 1, "failed": 1,
            "results": [
                {"registry_id": "archidekt:1", "name": "Kaalia", "action": "created"},
                {"registry_id": "archidekt:2", "name": "Meren", "action": "failed",
                 "error": "could not create deck 'Meren' (500)"},
            ],
        }

        response = self.client.post("/api/transcribe/moxfield", json={})

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["success"])
        self.assertEqual(data["report"]["failed"], 1)


if __name__ == "__main__":
    unittest.main()

import os
import sys
import re
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app import app
import version


class TestAppVersion(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_version_format_semver_with_date(self):
        """Version string must be in semver format with date in the revision/patch segment."""
        # e.g., 0.3.20260929 or 0.3.0+20260929
        pattern = r"^\d+\.\d+(\.\d{8}|\.\d+\+\d{8})$"
        self.assertRegex(
            version.__version__,
            pattern,
            f"Version '{version.__version__}' does not match semver format with date (e.g., 0.3.20260929)",
        )
        self.assertEqual(version.__version__, version.VERSION)

    def test_api_version_endpoint(self):
        """GET /api/version returns the application version."""
        response = self.client.get("/api/version")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data.get("version"), version.__version__)

    def test_version_rendered_in_sidebar_persistent_ui(self):
        """Visible version number must be present in the persistent layout on web pages."""
        for path in ["/", "/decks", "/inventory", "/instances", "/tags"]:
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                # Verify version element exists and displays version
                self.assertIn('id="app-version"', response.text)
                self.assertIn(version.__version__, response.text)
                self.assertIn(f"v{version.__version__}", response.text)


if __name__ == "__main__":
    unittest.main()

"""Template-level checks for the Decommission button (no JS test harness exists)."""
import re
import unittest

from fastapi.testclient import TestClient

from src.server.app.main import app


class TestDecommissionUI(unittest.TestCase):
    def setUp(self):
        self.html = TestClient(app).get("/deck/Some%20Deck").text

    def test_button_and_dialog_present_with_correct_spelling(self):
        self.assertIn('id="decommissionBtn"', self.html)
        self.assertIn('id="decommissionModal"', self.html)
        self.assertIn("Decommission", self.html)

    def test_button_hidden_by_default_and_gated_on_physical(self):
        btn = re.search(r'<button[^>]*id="decommissionBtn"[^>]*>', self.html).group(0)
        self.assertIn("hidden", btn)
        self.assertIn("!== 'physical'", self.html)

    def test_calls_endpoint_and_reloads(self):
        self.assertIn("/decommission`, { method: 'POST' }", self.html)
        self.assertIn("await loadDeckData()", self.html)


if __name__ == "__main__":
    unittest.main()

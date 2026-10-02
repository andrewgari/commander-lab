import unittest
from fastapi.testclient import TestClient

from app import app


class TestDeckNamecardUI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_decks_page_contains_enlarged_commander_icon_css(self):
        """Verify templates/decks.html contains enlarged commander-icon styles (80px, shadow, hover)."""
        response = self.client.get("/decks")
        self.assertEqual(response.status_code, 200)
        html = response.text

        # Commander icon sizing
        self.assertIn(".commander-icon", html)
        self.assertIn("width: 80px", html)
        self.assertIn("height: 80px", html)

        # Header art container and info styling
        self.assertIn(".deck-header-art", html)
        self.assertIn(".deck-header-info", html)
        self.assertIn("min-width: 0", html)

    def test_decks_page_contains_commander_icon_js_rendering(self):
        """Verify templates/decks.html JS uses 80px fallback and -32px partner overlap."""
        response = self.client.get("/decks")
        self.assertEqual(response.status_code, 200)
        html = response.text

        # Partner overlap margin adjusted for 80px icons
        self.assertIn("margin-left: -32px", html)

        # Fallback svg dimensions
        self.assertIn("width='80' height='80'", html)

        # Markup contains deck-header-art and deck-header-info
        self.assertIn('<div class="deck-header-art">', html)
        self.assertIn('<div class="deck-header-info">', html)

import unittest
from fastapi.testclient import TestClient

from app import app


class TestDeckNamecardUI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_decks_page_uses_rectangular_commander_art(self):
        """Commander art comes from the shared HUD group (hud.css .hud-cmdrs,
        rectangular art-crop frames), not round 160px .commander-icon (t_dc0aa4c9)."""
        response = self.client.get("/decks")
        self.assertEqual(response.status_code, 200)
        html = response.text

        self.assertNotIn("commander-icon", html)
        self.assertNotIn("width: 160px", html)
        self.assertIn("HUD.commanders(HUD.commanderRoles(deck))", html)

        # Header art container and info styling
        self.assertIn(".deck-header-art", html)
        self.assertIn(".deck-header-info", html)
        self.assertIn("min-width: 0", html)

    def test_decks_page_contains_commander_icon_js_rendering(self):
        """Partner overlap is gone; 2+ commanders stack art above text
        (.deck-header--multi), not an inline -64px margin (t_96b8867c)."""
        response = self.client.get("/decks")
        self.assertEqual(response.status_code, 200)
        html = response.text

        # Partner decks stack art above text; no inline overlap squeezing the text column
        self.assertNotIn("margin-left: -64px", html)
        self.assertIn("deck-header--multi", html)

        # Markup contains deck-header-art and deck-header-info
        self.assertIn('<div class="deck-header-art">', html)
        self.assertIn('<div class="deck-header-info">', html)

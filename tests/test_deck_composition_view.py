"""
Tests for /deck/{deck_name}/composition route and templates/deck_composition.html.
"""
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

import app as app_mod

client = TestClient(app_mod.app)


class TestDeckCompositionView(unittest.TestCase):
    @patch("app.registry.list_decks")
    @patch("app._find_deck_any")
    @patch("app.comp_svc.resolve_for_deck")
    def test_deck_composition_view_200(self, mock_resolve, mock_find, mock_list_decks):
        deck_data = {
            "name": "Holy Hellfire",
            "registry_id": "archidekt:12345",
            "commanders": ["Kaalia of the Vast"],
            "color": "WBR",
            "cards": [{"name": "Sol Ring", "quantity": 1}],
        }
        mock_list_decks.return_value = [deck_data]
        mock_find.return_value = deck_data
        mock_resolve.return_value = {
            "mode": "auto",
            "preset_id": "tcz_2025",
            "preset": {
                "id": "tcz_2025",
                "name": "Command Zone 2025",
                "description": "Standard Commander template",
                "kind": "builtin",
                "source": "The Command Zone ep. 658",
                "structure": {"deck_size": 100, "lands": 38},
                "slots": [
                    {"ref": "core:ramp", "target": 10, "min": 8, "max": 12, "why": "Fast mana"},
                    {"ref": "core:card_advantage", "target": 12, "min": 10, "max": 14, "why": "Card draw"},
                ],
            },
        }

        resp = client.get("/deck/Holy%20Hellfire/composition")
        self.assertEqual(resp.status_code, 200)
        html = resp.text
        self.assertIn("Composition · Holy Hellfire | Commander Lab", html)
        self.assertIn("Composition Preset", html)
        self.assertIn("Command Zone 2025", html)
        self.assertIn("Auto", html)
        self.assertIn("Ramp", html)
        self.assertIn("Card Advantage", html)

    @patch("app.registry.list_decks")
    @patch("app._find_deck_any")
    def test_deck_composition_view_404(self, mock_find, mock_list_decks):
        mock_list_decks.return_value = []
        mock_find.return_value = None
        resp = client.get("/deck/NonexistentDeck/composition")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("Deck Not Found", resp.text)


if __name__ == "__main__":
    unittest.main()

"""
Tests for the /deck/{deck_name}/composition redirect (NAV_IA §3.2, N5):
the standalone page is gone — it 301-redirects to /deck/{n}/stats#composition,
where the composition section now lives.
"""
import unittest
from unittest.mock import patch
from fastapi.testclient import TestClient

import src.server.app.main as app_mod

client = TestClient(app_mod.app)


class TestDeckCompositionRedirect(unittest.TestCase):
    def test_composition_301s_to_stats_anchor(self):
        resp = client.get("/deck/Holy%20Hellfire/composition", follow_redirects=False)
        self.assertEqual(resp.status_code, 301)
        self.assertEqual(resp.headers["location"], "/deck/Holy%20Hellfire/stats#composition")

    @patch("src.server.app.main.comp_svc.resolve_for_deck")
    @patch("src.server.app.main._find_deck_any")
    @patch("src.server.app.main.registry.list_decks")
    def test_stats_page_carries_the_composition_section(self, mock_list_decks, mock_find, mock_resolve):
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

        # The redirect target serves the folded-in composition section.
        resp = client.get("/deck/Holy%20Hellfire/composition")
        self.assertEqual(resp.status_code, 200)
        html = resp.text
        self.assertIn("Stats · Holy Hellfire | Commander Lab", html)
        self.assertIn('id="composition"', html)
        self.assertIn("Composition", html)
        self.assertIn("Command Zone 2025", html)
        self.assertIn("Ramp", html)
        self.assertIn("Card Advantage", html)
        self.assertIn("compPickerForm", html)

    @patch("src.server.app.main.registry.list_decks")
    def test_unknown_deck_still_404s_after_the_redirect(self, mock_list_decks):
        mock_list_decks.return_value = []
        resp = client.get("/deck/NonexistentDeck/composition")
        self.assertEqual(resp.status_code, 404)
        self.assertIn("Deck Not Found", resp.text)


if __name__ == "__main__":
    unittest.main()

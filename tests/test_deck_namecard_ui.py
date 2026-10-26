import unittest
from fastapi.testclient import TestClient

from src.server.app.main import app


class TestDeckNamecardUI(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_decks_page_uses_rectangular_commander_art(self):
        """/decks cards are art tiles (option C, t_bb6c7c79): rectangular
        art-crop frames from the role helper, never round 160px .commander-icon
        (t_dc0aa4c9)."""
        response = self.client.get("/decks")
        self.assertEqual(response.status_code, 200)
        html = response.text

        self.assertNotIn("commander-icon", html)
        self.assertNotIn("width: 160px", html)
        self.assertIn("HUD.commanderRoles(deck)", html)
        self.assertIn("HUD.scryfall(c.uid, 'art_crop')", html)

        # Fixed-shape tile; text column can shrink (no wrap/bloat).
        self.assertIn("aspect-ratio: var(--tile-ratio)", html)
        self.assertIn(".deck-header-info { min-width: 0; }", html)

    def test_decks_page_tile_markup(self):
        """Partner overlap is gone (t_96b8867c); the tile carries art, a top
        row (status + pips), a plate (title, commander, inventory) and a
        stretched quick-view button."""
        response = self.client.get("/decks")
        self.assertEqual(response.status_code, 200)
        html = response.text

        self.assertNotIn("margin-left: -64px", html)
        for frag in ('${deckArt(deck)}', '<div class="deck-top">', '<div class="deck-plate">',
                     '<div class="deck-header-info">', 'class="deck-inv"', 'class="deck-open"',
                     "HUD.statusChip(", "HUD.pips("):
            self.assertIn(frag, html)
        # Quick view a11y: focus returns via a stable deck key (tiles re-render),
        # focus moves into the dialog on open, and Tab is contained.
        self.assertIn('data-deck-key="', html)
        self.assertIn("b.dataset.deckKey === deckModalReturnKey", html)
        self.assertIn("document.querySelector('#deckModal .modal-close').focus();", html)
        self.assertIn("deckModalFocusables()", html)
        # Repo badge clicks forward to the tile's quick-view button.
        self.assertIn("closest('.deck-card .repo-badge')", html)
        # Title clamps to 2 lines; commander line is one ellipsized line.
        self.assertRegex(html, r"\.deck-name \{[^}]*-webkit-line-clamp: 2")
        self.assertRegex(html, r"\.deck-commanders \{[^}]*white-space: nowrap[^}]*text-overflow: ellipsis")

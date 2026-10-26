"""Regression: two-commander (partner / background) decks must not crush the
deck name and status chip.

Bug (t_96b8867c): on /decks the two 160px portraits sat side by side (only
64px overlap, about 256px of art), leaving the text column ~32px wide. The
deck name wrapped every 1-2 letters and the PHYSICAL chip got clipped.

Fix under test:
- /decks: every deck is the same art tile (option C, t_bb6c7c79). Two
  commanders split one frame on a diagonal (no overlapping portraits); a
  true companion is a small inset. The inline `margin-left: -64px` is gone.
- shared deck header (every deck tab): both commanders share the one fixed
  art frame (`hud-deckhead-art--multi`), so the title column keeps its width.
"""
import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from src.server.app.main import app

ROOT = Path(__file__).resolve().parent.parent
DECKS_HTML = ROOT / "src/web/templates" / "decks.html"
HUD_CSS = ROOT / "src/web/static" / "css" / "hud.css"
HUD_JS = ROOT / "src/web/static" / "js" / "hud.js"

UID_A = "0a1b2c3d-1111-4222-8333-444455556666"
UID_B = "9f8e7d6c-aaaa-4bbb-8ccc-ddddeeeeffff"
PARTNER = {"id": 9001, "name": "The Laws of Time are Mine!!", "color": "RW",
           "commanders": ["The Tenth Doctor", "Rose Tyler"], "commander_uids": [UID_A, UID_B],
           "status": "physical", "registry_id": "archidekt:9001", "source": "archidekt", "source_id": "9001",
           "cards": [{"name": "The Tenth Doctor", "quantity": 1}, {"name": "Rose Tyler", "quantity": 1}]}
SOLO = {"id": 9002, "name": "Holy Hellfire", "color": "WBR", "commanders": ["Kaalia of the Vast"],
        "commander_uids": [UID_A], "status": "physical", "registry_id": "archidekt:9002",
        "source": "archidekt", "source_id": "9002", "cards": [{"name": "Kaalia of the Vast", "quantity": 1}]}
INVENTORY = [{"name": "The Tenth Doctor", "type": "Creature", "cmc": 3, "is_commander": True},
             {"name": "Rose Tyler", "type": "Creature", "cmc": 2, "is_commander": True}]


def _js_function(src, name):
    """Pull `function name(...) { ... }` out of an inline <script> (brace-matched)."""
    start = src.index("function " + name + "(")
    i = src.index("{", start)
    depth = 0
    for j in range(i, len(src)):
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[start:j + 1]
    raise AssertionError("unbalanced function " + name)


class TestDeckListMultiCommander(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = DECKS_HTML.read_text()

    def test_no_inline_portrait_overlap(self):
        self.assertNotIn("margin-left: -64px", self.src)

    def test_tile_uses_one_art_frame(self):
        """Option C (t_bb6c7c79): every deck gets the same tile; 1 or 2
        commanders share one art frame, so there's no second card shape."""
        self.assertIn("${deckArt(deck)}", self.src)
        self.assertNotIn("deck-header--multi", self.src)
        self.assertNotIn("commander-icon", self.src)

    def test_split_css_is_a_diagonal_not_overlap(self):
        m = re.search(r"\.deck-art--split img \+ img\s*\{([^}]*)\}", self.src)
        self.assertIsNotNone(m, "missing partner split rule")
        self.assertIn("clip-path: polygon(", m.group(1))
        self.assertRegex(self.src, r"\.deck-art--split::after\s*\{[^}]*clip-path")
        # Doctor's companion gets the smaller wedge (role sizing).
        self.assertRegex(self.src, r"\.deck-art--doctors-companion\s*\{[^}]*--tile-split:")
        # Pips wrap under the chip on narrow tiles instead of clipping.
        self.assertRegex(self.src, r"\.deck-top\s*\{[^}]*flex-wrap:\s*wrap")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_deck_art_for_roles(self):
        hud = HUD_JS.read_text()
        code = ("global.window = {}; global.document = {readyState: 'complete', addEventListener(){}, querySelectorAll(){return []}};\n"
                + hud + "\nconst HUD = window.HUD;\n"
                + _js_function(self.src, "deckArtClass") + "\n" + _js_function(self.src, "deckArtImg") + "\n"
                + _js_function(self.src, "deckArt") + "\n"
                "const decks = JSON.parse(process.argv[1]);\n"
                "process.stdout.write(JSON.stringify(decks.map(deckArt)));")
        doctor = dict(PARTNER, commander_roles=[{"name": "The Tenth Doctor", "uid": UID_A, "role": "doctor"},
                                                {"name": "Rose Tyler", "uid": UID_B, "role": "doctors_companion"}])
        companion = dict(SOLO, commander_roles=[{"name": "Kaalia of the Vast", "uid": UID_A, "role": "commander"},
                                                {"name": "Lurrus", "uid": UID_B, "role": "companion"}])
        decks = [PARTNER, SOLO, doctor, companion,
                 {"name": "one commander, two printings", "commanders": ["A"], "commander_uids": [UID_A, UID_B]},
                 {"name": "none"}]
        res = subprocess.run(["node", "-e", code, json.dumps(decks)], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        partner, solo, doc, comp, printings, none = json.loads(res.stdout)
        self.assertIn('class="deck-art deck-art--split"', partner)
        self.assertEqual(partner.count("<img"), 2)
        self.assertIn('class="deck-art"', solo)
        self.assertEqual(solo.count("<img"), 1)
        self.assertIn("deck-art--split deck-art--doctors-companion", doc)
        self.assertIn('class="deck-art"', comp)
        self.assertIn('<div class="deck-companion"', comp)
        self.assertEqual(printings.count("<img"), 1)
        self.assertIn("No commander", none)
        for html in (partner, solo, doc, comp):
            self.assertIn("cards.scryfall.io/art_crop/", html)
            self.assertIn("data-fallback=", html)

    def test_portraits_come_from_shared_role_helper(self):
        self.assertIn("const roles = HUD.commanderRoles(deck);", self.src)


class TestDeckHeadMultiCommander(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _get(self, path, decks):
        with patch("src.server.app.main.registry.list_decks", return_value=decks), \
                patch("src.server.app.main.inventory_view.build_inventory", return_value=INVENTORY):
            res = self.client.get(path)
        self.assertEqual(res.status_code, 200)
        return res.text.split('<header class="hud-deckhead">', 1)[1].split("</header>", 1)[0]

    def _art(self, head):
        return head.split('class="hud-cmdrs', 1)[1].split("</div>", 1)[0]

    def test_partner_deck_header_shows_both(self):
        name = "The%20Laws%20of%20Time%20are%20Mine!!"
        for path in ("/deck/" + name, "/deck/" + name + "/stats"):
            with self.subTest(path=path):
                head = self._get(path, [PARTNER])
                art = self._art(head)
                self.assertTrue(art.startswith(' hud-cmdrs--multi hud-cmdrs--many hud-deckhead-art"'), art[:80])
                self.assertEqual(art.count("<img"), 2)
                self.assertIn(UID_A, art)
                self.assertIn(UID_B, art)
                self.assertIn("The Tenth Doctor + Rose Tyler", head)
                self.assertIn("The Laws of Time are Mine!!", head)

    def test_single_commander_header_unchanged(self):
        head = self._get("/deck/Holy%20Hellfire", [SOLO])
        self.assertIn('<div class="hud-cmdrs hud-deckhead-art">', head)
        self.assertNotIn("hud-cmdrs--multi", head)
        self.assertEqual(head.count("<img"), 1)

    def test_one_commander_two_printings_is_not_split(self):
        dual = dict(SOLO, commander_uids=[UID_A, UID_B])
        head = self._get("/deck/Holy%20Hellfire", [dual])
        self.assertNotIn("hud-cmdrs--multi", head)
        self.assertEqual(head.count("<img"), 1)

    def test_multi_frame_css_shrinks_portraits(self):
        css = HUD_CSS.read_text()
        self.assertRegex(css, r"\.hud-cmdrs--multi\s*\{[^}]*--cmdr-w:")
        self.assertRegex(css, r"\.hud-cmdr-art\s*\{[^}]*aspect-ratio:\s*var\(--art-ratio\)")
        self.assertRegex(css, r"\.hud-deckhead-meta \.hud-chip\s*\{[^}]*white-space:\s*nowrap")


if __name__ == "__main__":
    unittest.main()

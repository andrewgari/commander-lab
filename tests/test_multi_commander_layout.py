"""Regression: two-commander (partner / background) decks must not crush the
deck name and status chip.

Bug (t_96b8867c): on /decks the two 160px portraits sat side by side (only
64px overlap, about 256px of art), leaving the text column ~32px wide. The
deck name wrapped every 1-2 letters and the PHYSICAL chip got clipped.

Fix under test:
- /decks: a deck with 2+ commanders gets `deck-header deck-header--multi`
  (art row stacked above the text, full width, smaller overlapping
  portraits). Single-commander decks keep the plain `deck-header`. The
  inline `margin-left: -64px` is gone; overlap lives in CSS.
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

from app import app

ROOT = Path(__file__).resolve().parent.parent
DECKS_HTML = ROOT / "templates" / "decks.html"
HUD_CSS = ROOT / "static" / "css" / "hud.css"

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

    def test_card_header_uses_layout_class_helper(self):
        self.assertIn('<div class="${deckHeaderClass(deck)}">', self.src)
        self.assertNotIn('<div class="deck-header">', self.src)

    def test_multi_header_css_stacks_art_above_text(self):
        m = re.search(r"\.deck-header--multi\s*\{([^}]*)\}", self.src)
        self.assertIsNotNone(m, "missing .deck-header--multi rule")
        self.assertIn("flex-direction: column", m.group(1))
        # Smaller portraits than the 160px single-commander icon, overlap in CSS.
        self.assertRegex(self.src, r"\.deck-header--multi \.commander-icon\s*\{[^}]*width:")
        self.assertRegex(self.src, r"\.deck-header--multi \.commander-icon \+ \.commander-icon\s*\{[^}]*margin-left:\s*-")
        # Chips never wrap/clip; names wrap at word boundaries, not per letter.
        self.assertRegex(self.src, r"\.deck-status\s*\{[^}]*white-space:\s*nowrap")
        self.assertRegex(self.src, r"\.deck-header--multi \.deck-name\s*\{[^}]*word-break:\s*normal")

    @unittest.skipUnless(shutil.which("node"), "node not installed")
    def test_header_class_for_one_and_two_commanders(self):
        code = (_js_function(self.src, "commanderCount") + "\n" + _js_function(self.src, "deckHeaderClass") + "\n"
                "const decks = JSON.parse(process.argv[1]);\n"
                "process.stdout.write(JSON.stringify(decks.map(deckHeaderClass)));")
        decks = [PARTNER, SOLO,
                 {"name": "names only", "commanders": ["A", "B"]},
                 {"name": "none"}]
        res = subprocess.run(["node", "-e", code, json.dumps(decks)], capture_output=True, text=True, timeout=30)
        self.assertEqual(res.returncode, 0, res.stderr)
        self.assertEqual(json.loads(res.stdout), [
            "deck-header deck-header--multi", "deck-header",
            "deck-header deck-header--multi", "deck-header"])


class TestDeckHeadMultiCommander(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def _get(self, path, decks):
        with patch("app.registry.list_decks", return_value=decks), \
                patch("app.inventory_view.build_inventory", return_value=INVENTORY):
            res = self.client.get(path)
        self.assertEqual(res.status_code, 200)
        return res.text.split('<header class="hud-deckhead">', 1)[1].split("</header>", 1)[0]

    def test_partner_deck_header_shares_one_frame(self):
        name = "The%20Laws%20of%20Time%20are%20Mine!!"
        for path in ("/deck/" + name, "/deck/" + name + "/stats"):
            with self.subTest(path=path):
                head = self._get(path, [PARTNER])
                art = head.split('class="hud-deckhead-art', 1)[1].split("</div>", 1)[0]
                self.assertTrue(art.startswith(' hud-deckhead-art--multi"'), art[:80])
                self.assertEqual(art.count("<img"), 2)
                self.assertIn(UID_A, art)
                self.assertIn(UID_B, art)
                self.assertIn("The Tenth Doctor + Rose Tyler", head)
                self.assertIn("The Laws of Time are Mine!!", head)

    def test_single_commander_header_unchanged(self):
        head = self._get("/deck/Holy%20Hellfire", [SOLO])
        self.assertIn('<div class="hud-deckhead-art">', head)
        self.assertNotIn("hud-deckhead-art--multi", head)
        self.assertEqual(head.count("<img"), 1)

    def test_multi_frame_css_keeps_frame_width(self):
        css = HUD_CSS.read_text()
        m = re.search(r"\.hud-deckhead-art--multi\s*\{([^}]*)\}", css)
        self.assertIsNotNone(m)
        self.assertIn("grid-template-columns", m.group(1))
        self.assertNotIn("width", m.group(1))  # frame width comes from .hud-deckhead-art only
        self.assertRegex(css, r"\.hud-deckhead-meta \.hud-chip\s*\{[^}]*white-space:\s*nowrap")


if __name__ == "__main__":
    unittest.main()

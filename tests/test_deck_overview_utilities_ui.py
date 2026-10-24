"""Structural checks for the deck Overview "Deck utilities" section (PR #288, DV-02).

Checks templates/deck.html: #footerLinks, #syncControls and #deckStatusField each
appear exactly once, all inside #deckUtilities, none inside .deck-hero-side (which
keeps only #commanderShowcase), and #deckUtilities is the last child of .ov-summary,
after the Briefing (#deckDescriptionPanel). The Primer block left the Overview for
its own tab (NAV_IA §3.2, D5).
"""
import unittest
from html.parser import HTMLParser
from pathlib import Path

DECK_HTML = (Path(__file__).resolve().parent.parent / "templates" / "deck.html").read_text()
MOVED = ("footerLinks", "syncControls", "deckStatusField")
VOID = {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
        "source", "track", "wbr"}


class _Node:
    def __init__(self, tag, attrs, parent):
        self.tag = tag
        self.id = attrs.get("id")
        self.classes = set((attrs.get("class") or "").split())
        self.parent = parent
        self.children = []
        self.pos = 0  # document order index


class _Tree(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.stack = [self.root]
        self.nodes = []

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self.stack[-1])
        node.pos = len(self.nodes)
        self.stack[-1].children.append(node)
        self.nodes.append(node)
        if tag not in VOID:
            self.stack.append(node)

    def handle_startendtag(self, tag, attrs):
        node = _Node(tag, dict(attrs), self.stack[-1])
        node.pos = len(self.nodes)
        self.stack[-1].children.append(node)
        self.nodes.append(node)

    def handle_endtag(self, tag):
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return


def _ancestors(node):
    out, p = [], node.parent
    while p is not None and p.tag != "#root":
        out.append(p)
        p = p.parent
    return out


class TestDeckUtilitiesPlacement(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tree = _Tree()
        cls.tree.feed(DECK_HTML)

    def by_id(self, ident):
        return [n for n in self.tree.nodes if n.id == ident]

    def one(self, ident):
        found = self.by_id(ident)
        self.assertEqual(len(found), 1, f"#{ident} must appear exactly once")
        return found[0]

    def test_moved_panels_appear_exactly_once(self):
        for ident in MOVED:
            with self.subTest(id=ident):
                self.one(ident)

    def test_moved_panels_inside_deck_utilities(self):
        utils = self.one("deckUtilities")
        for ident in MOVED:
            with self.subTest(id=ident):
                self.assertIn(utils, _ancestors(self.one(ident)))

    def test_moved_panels_not_inside_hero_side(self):
        sides = [n for n in self.tree.nodes if "deck-hero-side" in n.classes]
        self.assertEqual(len(sides), 1)
        for ident in MOVED:
            with self.subTest(id=ident):
                self.assertNotIn(sides[0], _ancestors(self.one(ident)))

    def test_hero_side_keeps_only_commander_showcase(self):
        side = [n for n in self.tree.nodes if "deck-hero-side" in n.classes][0]
        inside = [n.id for n in self.tree.nodes if side in _ancestors(n) and n.id]
        self.assertEqual(inside, ["commanderShowcase"])

    def test_deck_utilities_is_last_child_of_ov_summary(self):
        summaries = [n for n in self.tree.nodes if "ov-summary" in n.classes]
        self.assertEqual(len(summaries), 1)
        elem_kids = [c for c in summaries[0].children]
        self.assertEqual(elem_kids[-1].id, "deckUtilities")

    def test_deck_utilities_after_briefing(self):
        utils = self.one("deckUtilities")
        briefing = self.one("deckDescriptionPanel")
        summary = [n for n in self.tree.nodes if "ov-summary" in n.classes][0]
        self.assertIn(summary, _ancestors(utils))
        self.assertLess(briefing.pos, utils.pos)

    def test_primer_is_gone_from_the_overview(self):
        # NAV_IA §3.2 (D5): the Primer lives on its own tab (/deck/{n}/primer).
        self.assertNotIn("Primer</h3>", DECK_HTML)


if __name__ == "__main__":
    unittest.main()

"""Structural checks for the deck Overview content order (PR #288 DV-02, NAV_IA §3.3 D4).

Checks templates/deck.html: the decklist (.ov-gallery) is the first content block,
followed by Briefing, the stats-at-a-glance strip (#ovStats) and then utilities;
#footerLinks, #syncControls and #deckStatusField each appear exactly once, all
inside #deckUtilities, none inside .deck-hero-side (which keeps only
#commanderShowcase), and #deckUtilities is the last child of .ov-summary,
after the Briefing (#deckDescriptionPanel). The Primer block left the Overview for
its own tab (NAV_IA §3.2, D5).
The Visual/List decklist toggle is a .hud-seg segmented control, not .hud-tabs.
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
        self.attrs = attrs
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

    def test_decklist_is_first_content_block(self):
        split = [n for n in self.tree.nodes if "ov-split" in n.classes]
        self.assertEqual(len(split), 1)
        first = split[0].children[0]
        self.assertIn("ov-gallery", first.classes)
        self.assertIn(first, _ancestors(self.one("decklistContainer")))
        # ...and it precedes the Briefing, the stats strip and the utilities.
        gallery_pos = first.pos
        self.assertLess(gallery_pos, self.one("deckDescriptionPanel").pos)
        self.assertLess(gallery_pos, self.one("ovStats").pos)
        self.assertLess(gallery_pos, self.one("deckUtilities").pos)

    def test_decklist_view_toggle_is_segmented_control_not_tabs(self):
        views = self.one("decklistViews")
        self.assertIn("hud-seg", views.classes)
        self.assertNotIn("hud-tabs", views.classes)
        self.assertEqual(views.tag, "div")
        kids = [c for c in views.children]
        radios = [c for c in kids if c.tag == "input" and c.attrs.get("type") == "radio"]
        labels = [c for c in kids if c.tag == "label"]
        self.assertEqual(len(radios), 2)
        self.assertEqual(len(labels), 2)
        self.assertEqual({r.attrs.get("value") for r in radios}, {"visual", "list"})

    def test_stats_strip_links_to_stats_tab(self):
        strip = self.one("ovStats")
        self.assertLess(self.one("deckDescriptionPanel").pos, strip.pos)
        links = [n for n in self.tree.nodes
                 if strip in _ancestors(n) and n.tag == "a" and n.attrs.get("href", "").endswith("/stats")]
        self.assertTrue(links, "stats strip must link through to the Stats tab")
        for ident in ("deckStats", "deckCurve", "deckTypes"):
            with self.subTest(id=ident):
                self.assertIn(strip, _ancestors(self.one(ident)))

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

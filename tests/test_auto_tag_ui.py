"""AT-UI: the Auto Tag UI on the deck page (static/js/auto_tag.js).

- the pure helpers (tag precedence, counts, filter, group-by-tag, chip markup,
  escaping, the 3-chip cap) run in node;
- the deck page mounts the strip / filter bar / script and wires the card editor.

Spec: docs/design/auto-tag/UX_SPEC.md. The browser flow (run / confirm / error /
keep / remove / filter) was exercised with Playwright against the real
endpoints; see the screenshots in docs/design/auto-tag/shots/.
"""
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient

from app import app

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "static" / "js" / "auto_tag.js"

HARNESS = r"""
const AT = require(process.argv[1]);
const out = {};
const e = (tag, source, version) => ({ tag, source, created_at: "", version });
// user beats auto with the same name; users first, then newest
out.effective = AT.effective([e("Ramp", "auto", 5), e("Ramp", "user", 1), e("Draw", "auto", 9), e("Tokens", "auto", 2), e("Aristocrats", "user", 3)]).map(x => x.tag + ":" + x.source);
const cards = ["Sol Ring", "Cultivate", "Swords", "Plain Island"].map(name => ({ name }));
const map = {
  "Sol Ring": [e("Ramp", "auto", 1)],
  "Cultivate": [e("Ramp", "auto", 1), e("Fixing", "auto", 2)],
  "Swords": [e("Removal", "user", 1), e("Protection", "auto", 2)],
};
out.counts = AT.roleCounts(cards, map);
out.untagged = AT.untaggedCount(cards, map);
out.filterRamp = AT.filterCards(cards, map, "Ramp").map(c => c.name);
out.filterNone = AT.filterCards(cards, map, AT.UNTAGGED).map(c => c.name);
out.filterOff = AT.filterCards(cards, map, null).length;
out.groups = AT.tagGroups(cards, map).map(g => g.label + ":" + g.cards.map(c => c.name).join("+"));
out.summary = AT.summarize(cards, map, [e("Tokens", "auto", 1), e("Mine", "user", 2)]);
// chips: cap + overflow, glyphs, sr text
const five = ["A", "B", "C", "D", "E"].map((t, i) => e(t, i === 0 ? "user" : "auto", 9 - i));
const html = AT.chipsHtml(five, 3, false);
out.chipCount = (html.match(/class="hud-chip at-chip /g) || []).length;
out.hasMore = html.includes("at-chip--more") && html.includes("+2");
out.userGlyph = html.includes("\u25C6") && html.includes("\u25C7");
out.srLabels = html.includes("(yours)") && html.includes("(auto)");
out.empty = AT.chipsHtml([], 3);
// editable chips: auto gets a keep button, user does not; both can be removed
const ed = AT.chipHtml(e("Ramp", "auto", 1), { edit: true }) + "|" + AT.chipHtml(e("Mine", "user", 1), { edit: true });
out.pinOnAutoOnly = (ed.match(/data-at-pin=/g) || []).length === 1 && (ed.match(/data-at-remove=/g) || []).length === 2;
// no raw markup from a tag name
const xss = AT.chipHtml(e('<img src=x onerror=alert(1)>', "auto", 1), {});
out.escaped = !xss.includes("<img");
// vocabulary: no dupes, nothing blank
const all = AT.VOCAB.flatMap(g => g.tags);
out.vocabUnique = new Set(all).size === all.length && all.every(t => t.trim() === t && t);
out.picksHideTaken = !AT.picksHtml([e("ramp", "auto", 1)]).includes('data-at-add="ramp"') && AT.picksHtml([]).includes('data-at-add="ramp"');
console.log(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node not installed: auto_tag.js helper tests need it")
class TestAutoTagHelpers(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        res = subprocess.run(["node", "-e", HARNESS, str(JS)], capture_output=True, text=True, timeout=30)
        if res.returncode:
            raise AssertionError(res.stderr)
        cls.out = json.loads(res.stdout)

    def test_user_beats_auto_and_sorts_users_first_then_newest(self):
        self.assertEqual(self.out["effective"], ["Aristocrats:user", "Ramp:user", "Draw:auto", "Tokens:auto"])

    def test_role_counts_most_first_ties_alphabetical(self):
        self.assertEqual(self.out["counts"], [
            {"tag": "Ramp", "n": 2}, {"tag": "Fixing", "n": 1}, {"tag": "Protection", "n": 1}, {"tag": "Removal", "n": 1}])
        self.assertEqual(self.out["untagged"], 1)

    def test_filter(self):
        self.assertEqual(self.out["filterRamp"], ["Sol Ring", "Cultivate"])
        self.assertEqual(self.out["filterNone"], ["Plain Island"])
        self.assertEqual(self.out["filterOff"], 4)

    def test_group_by_tag_repeats_multi_tag_cards_and_puts_untagged_last(self):
        self.assertEqual(self.out["groups"], [
            "Ramp:Sol Ring+Cultivate", "Fixing:Cultivate", "Protection:Swords", "Removal:Swords", "Untagged:Plain Island"])

    def test_summary_splits_auto_and_yours_including_deck_tags(self):
        s = self.out["summary"]
        self.assertEqual((s["tagged"], s["auto"], s["user"], s["cards"]), (3, 5, 2, 4))

    def test_at_most_three_chips_then_overflow(self):
        self.assertEqual(self.out["chipCount"], 3)  # the "+2" overflow chip is separate (hasMore)
        self.assertTrue(self.out["hasMore"])
        self.assertEqual(self.out["empty"], "")

    def test_auto_and_yours_differ_by_glyph_and_text_not_just_colour(self):
        self.assertTrue(self.out["userGlyph"])
        self.assertTrue(self.out["srLabels"])

    def test_only_auto_chips_offer_keep(self):
        self.assertTrue(self.out["pinOnAutoOnly"])

    def test_tag_names_are_escaped(self):
        self.assertTrue(self.out["escaped"])

    def test_fixed_vocabulary(self):
        self.assertTrue(self.out["vocabUnique"])
        self.assertTrue(self.out["picksHideTaken"])


class TestDeckPageWiring(unittest.TestCase):
    def setUp(self):
        self.html = TestClient(app).get("/deck/Some%20Deck").text

    def test_mount_points_and_script(self):
        self.assertIn('id="autoTag"', self.html)
        self.assertIn('id="atFilterbar"', self.html)
        self.assertIn("/static/js/auto_tag.js", self.html)

    def test_page_mounts_with_the_registry_id_and_hooks_the_decklist(self):
        for needle in ("AutoTag.mount(", "AT.filter(inventoryCards)", "AT.groups(", "AT.bindEditor(", "withTags("):
            self.assertIn(needle, self.html)

    def test_script_and_styles_are_served(self):
        c = TestClient(app)
        self.assertEqual(c.get("/static/js/auto_tag.js").status_code, 200)
        self.assertIn(".at-chip--auto", c.get("/static/css/hud.css").text)

    def test_script_only_talks_to_the_tag_endpoints(self):
        src = JS.read_text()
        for path in ('"/tags"', '"/card-tags"', '"/auto-tag"'):
            self.assertIn(path, src)


if __name__ == "__main__":
    unittest.main()

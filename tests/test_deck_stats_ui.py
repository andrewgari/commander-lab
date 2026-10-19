"""AN-4: Deck Stats + Identity pages on the AN-1..AN-3 APIs (UX_SPEC.md §2-§3.2).

Structure and behaviour only (same rules as the HUD-4 tests):
- D9 kit fixes are in hud.css (contrast is computed, not compared to literals),
  analytics.css is ported as hud.css section 7, new tokens exist;
- d3-force is vendored and loaded on /stats only;
- /deck/{name}/stats and /identity render the shared deck header + tabs, are
  built from kit classes, and keep page CSS layout-only;
- the pure data mappers in static/js/deck_analytics.js and HUD.barChart's
  template reference line are exercised in node on the real Holy Hellfire
  payloads (tests/fixtures/an4/, snapshot of prod 2026-10-12).
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
HUD_CSS = ROOT / "static" / "css" / "hud.css"
HUD_JS = ROOT / "static" / "js" / "hud.js"
DECK_JS = ROOT / "static" / "js" / "deck_analytics.js"
MOCK_CSS = ROOT / "docs" / "design" / "analytics" / "analytics.css"
FIX = ROOT / "tests" / "fixtures" / "an4"
VENDOR = "/static/vendor/d3-force-3.0.0.min.js"

DECK = {"id": 417707, "name": "Holy Hellfire", "color": "WBR", "commanders": ["Kaalia of the Vast"],
        "commander_uids": ["ef099f25-2b0b-4028-8490-fbc859e35f6c"], "status": "physical",
        "registry_id": "archidekt:417707", "source": "archidekt", "source_id": "417707",
        "cards": [{"name": "Kaalia of the Vast", "quantity": 1}, {"name": "Plains", "quantity": 5}]}
INVENTORY = [{"name": "Kaalia of the Vast", "type": "Creature", "cmc": 4, "is_commander": True},
             {"name": "Plains", "type": "Basic Land", "cmc": 0, "is_commander": False}]


def _classes(html):
    out = set()
    for attr in re.findall(r'class="([^"]*)"', html):
        out.update(attr.split())
    return out


def _page_style(html):
    head = html.split("</head>", 1)[0]
    return "\n".join(re.findall(r"<style>(.*?)</style>", head, re.S))


def _token(css, name):
    m = re.search(re.escape(name) + r"\s*:\s*(#[0-9a-fA-F]{6})", css)
    return m.group(1) if m else None


def _lum(hex_):
    def ch(c):
        c = int(c, 16) / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = ch(hex_[1:3]), ch(hex_[3:5]), ch(hex_[5:7])
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def _contrast(a, b):
    la, lb = sorted([_lum(a), _lum(b)], reverse=True)
    return (la + 0.05) / (lb + 0.05)


def _patched(fn):
    """Route tests: a fake registry with Holy Hellfire and a 2-card inventory."""
    fn = patch("app.inventory_view.build_inventory", return_value=INVENTORY)(fn)
    fn = patch("app.registry.list_decks", return_value=[DECK])(fn)
    return fn


class TestKitFixesAndSection7(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.css = HUD_CSS.read_text()
        cls.mock = MOCK_CSS.read_text()

    def test_d9_contrast_fixes_pass_aa(self):
        # Contrast is computed from the shipped tokens, so the palette can
        # still be tuned; it just can't fall below AA again.
        self.assertGreaterEqual(_contrast(_token(self.css, "--text-3"), _token(self.css, "--surface-1")), 4.5)
        self.assertGreaterEqual(_contrast(_token(self.css, "--danger"), _token(self.css, "--danger-dim")), 4.5)

    def test_d9_rank_bars_are_block_level(self):
        # The live bug: .hud-rank-fill is an inline <span>, so width:% was ignored.
        for sel in (".hud-rank-fill", ".hud-rank-track"):
            with self.subTest(sel=sel):
                rules = re.findall(re.escape(sel) + r"\s*\{([^}]*)\}", self.css)
                self.assertTrue(any(re.search(r"display\s*:\s*block", r) for r in rules), rules)

    def test_new_tokens_live_in_the_token_block(self):
        tokens = self.css.split("/* 2. LEGACY", 1)[0]
        for t in ["--fs-display", "--dur-slow", "--stagger", "--graph-h", "--graph-node-min",
                  "--graph-node-max", "--graph-edge", "--graph-dim"]:
            with self.subTest(token=t):
                self.assertRegex(tokens, re.escape(t) + r"\s*:")

    def test_section_7_is_ported(self):
        self.assertRegex(self.css, r"/\* 7\. ANALYTICS II =+")
        defined = set(re.findall(r"\.((?:hud|g)-[a-z0-9-]+)", self.css))
        wanted = set(re.findall(r"\.((?:hud|g)-[a-z0-9-]+)", self.mock))
        self.assertEqual(sorted(wanted - defined), [], "analytics.css classes missing from hud.css")
        self.assertNotIn("mock-note", self.css)

    def test_every_var_reference_is_defined(self):
        defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", self.css))
        used = set(re.findall(r"var\((--[a-z0-9-]+)", self.css))
        self.assertEqual(sorted(used - defined), [])

    def test_reduced_motion_disables_section_7_motion(self):
        rm = self.css.split("prefers-reduced-motion: reduce", 1)
        self.assertEqual(len(rm), 2)
        for sel in (".hud-enter", ".hud-comp-fill", ".g-node-in.is-entering"):
            with self.subTest(sel=sel):
                self.assertIn(sel, rm[1].split("}", 2)[0] + rm[1].split("}", 2)[1])


class TestVendorScript(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_d3_force_is_vendored(self):
        res = self.client.get(VENDOR)
        self.assertEqual(res.status_code, 200)
        self.assertIn("forceSimulation", res.text)

    @_patched
    def test_loaded_on_stats_only(self, *_):
        self.assertIn(VENDOR, self.client.get("/deck/Holy%20Hellfire/stats").text)
        for path in ["/", "/decks", "/deck/Holy%20Hellfire", "/deck/Holy%20Hellfire/identity"]:
            with self.subTest(path=path):
                self.assertNotIn("d3-force", self.client.get(path).text)


class _DeckPage:
    PATH = None
    ACTIVE = None

    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)
        with patch("app.registry.list_decks", return_value=[DECK]), \
                patch("app.inventory_view.build_inventory", return_value=INVENTORY):
            cls.res = cls.client.get(cls.PATH)
        cls.html = cls.res.text
        cls.body = cls.html.split("<body", 1)[1]
        cls.css = HUD_CSS.read_text()
        cls.style = _page_style(cls.html)

    def test_renders(self):
        self.assertEqual(self.res.status_code, 200)

    def test_shared_deck_header(self):
        for cls_ in ["hud-deckhead", "hud-deckhead-art", "hud-deckhead-meta", "hud-deckhead-actions",
                     "hud-deck-tabs", "hud-crumbs"]:
            with self.subTest(cls=cls_):
                self.assertIn(cls_, _classes(self.html))
        self.assertIn("Commander deck · Kaalia of the Vast", self.body)
        self.assertIn("hud-pip--w", self.body)
        self.assertIn("6 cards · 5 lands", self.body)
        self.assertIn("cards.scryfall.io/art_crop/front/e/f/ef099f25", self.body)

    def test_tabs_have_keys_and_mark_the_active_tab(self):
        nav = self.body.split('class="hud-tabs hud-deck-tabs"', 1)[1].split("</nav>", 1)[0]
        tabs = re.findall(r'<a class="hud-tab[^"]*" href="([^"]+)"[^>]*data-key="(\d)"', nav)
        self.assertEqual([k for _, k in tabs], ["1", "2", "3", "4", "5", "6", "7"])
        self.assertEqual([h for h, _ in tabs], ["/deck/Holy%20Hellfire", "/deck/Holy%20Hellfire/stats",
                                                 "/deck/Holy%20Hellfire/identity", "/deck/Holy%20Hellfire/journal",
                                                 "/deck/Holy%20Hellfire/tune", "/deck/Holy%20Hellfire/ideas",
                                                 "/deck/Holy%20Hellfire/review"])
        active = re.findall(r'<a class="hud-tab active"[^>]*aria-current="page"[^>]*>([^<]+)', nav)
        self.assertEqual([a.strip() for a in active], [self.ACTIVE])
        # No tuner run yet: Tune's badge is hidden, never "0" (UX_SPEC §1).
        self.assertNotRegex(nav, r'/tune"[^>]*>Tune <span')

    def test_every_hud_class_used_is_defined(self):
        used = set(re.findall(r"(?<![-\w])(hud-[a-z0-9-]+)", self.body))
        defined = set(re.findall(r"\.(hud-[a-z0-9-]+)", self.css))
        self.assertEqual(sorted(used - defined), [])

    def test_page_style_is_layout_only(self):
        self.assertNotRegex(self.style, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotRegex(self.style, r"rgba?\(")
        for banned in ["box-shadow", "backdrop-filter", "gradient(", "border-radius"]:
            with self.subTest(banned=banned):
                self.assertNotIn(banned, self.style)
        defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", self.css))
        self.assertEqual(sorted(set(re.findall(r"var\((--[a-z0-9-]+)", self.style)) - defined), [])

    def test_no_inline_colour_literals(self):
        for attr in re.findall(r'style="([^"]*)"', self.body):
            with self.subTest(style=attr):
                self.assertNotRegex(attr, r"#[0-9a-fA-F]{3,8}\b|rgba?\(")

    def test_tab_hotkeys_ignore_text_fields(self):
        self.assertIn("deck_analytics.js", self.body)
        js = DECK_JS.read_text()
        self.assertIn("isContentEditable", js)
        self.assertRegex(js, r"INPUT.*TEXTAREA.*SELECT")


class TestStatsPage(_DeckPage, unittest.TestCase):
    PATH = "/deck/Holy%20Hellfire/stats"
    ACTIVE = "Stats"

    def test_sections_in_spec_order(self):
        marks = ['id="statStrip"', "<h2>Interactions</h2>", "<h2>Shape</h2>", "<h2>Identity</h2>", "<h2>Combos</h2>"]
        idx = [self.body.find(m) for m in marks]
        self.assertTrue(all(i > 0 for i in idx), idx)
        self.assertEqual(idx, sorted(idx))

    def test_graph_block_structure(self):
        for cls_ in ["hud-graph", "hud-graph-engines", "hud-graph-stage", "hud-graph-toolbar", "hud-graph-svg",
                     "hud-graph-legend", "hud-inspector", "hud-inspector-body", "hud-isolated"]:
            with self.subTest(cls=cls_):
                self.assertIn(cls_, _classes(self.html))
        self.assertRegex(self.body, r'<svg class="hud-graph-svg" id="gsvg" role="group" aria-label="[^"]+"')
        self.assertRegex(self.body, r'class="hud-inspector"[^>]*aria-live="polite"')
        self.assertIn('data-view="list"', self.body)
        self.assertRegex(self.body, r'<input id="gsearch" type="search"')
        self.assertRegex(self.body, r'<input type="checkbox" id="gmana"')

    def test_panels_present(self):
        for pid in ["comp", "tplForm", "curve", "fit", "lowfit", "edhrec", "combosIn", "combosNear",
                    "playedMost", "playedLeast"]:
            with self.subTest(id=pid):
                self.assertIn('id="' + pid + '"', self.body)
        self.assertRegex(self.body, r'id="tplEdit"[^>]*aria-expanded="false"[^>]*aria-controls="tplForm"')
        self.assertIn("Low % ≠ bad. Low % + few edges + off-pillar = weak performer.", self.body)
        self.assertIn("A card can count in two rows. Plan = everything else.", self.body)

    def test_reads_the_shipped_apis(self):
        for api in ["interactions", "engines", "combos", "composition", "identity", "edhrec", "weak-performers",
                    "played-with"]:
            with self.subTest(api=api):
                self.assertIn("/" + api, self.body)

    def test_deck_name_is_escaped(self):
        evil = dict(DECK, name="<img src=x onerror=alert(1)>")
        with patch("app.registry.list_decks", return_value=[evil]), \
                patch("app.inventory_view.build_inventory", return_value=INVENTORY):
            html = self.client.get("/deck/%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E/stats").text
        self.assertNotIn("<img src=x onerror=alert(1)>", html)

    def test_unknown_deck_is_404(self):
        with patch("app.registry.list_decks", return_value=[DECK]):
            self.assertEqual(self.client.get("/deck/Nope/stats").status_code, 404)
            self.assertEqual(self.client.get("/deck/Nope/identity").status_code, 404)


class TestIdentityPage(_DeckPage, unittest.TestCase):
    PATH = "/deck/Holy%20Hellfire/identity"
    ACTIVE = "Identity"

    def test_editor_structure(self):
        for cls_ in ["hud-thesis", "hud-thesis-bar", "hud-pillars", "hud-tray", "hud-savebar", "hud-meter"]:
            with self.subTest(cls=cls_):
                self.assertIn(cls_, _classes(self.html))
        self.assertRegex(self.body, r'<div class="hud-pillars" id="pillars" role="list"')
        self.assertIn('id="thesis"', self.body)
        self.assertRegex(self.body, r'<details class="hud-tray[ "]')

    def test_uses_the_edit_endpoints(self):
        for frag in ["/identity", '"/pillars"', '"/thesis"', '"/pillars/order"', '"/generate"',
                     "PATCH", "PUT", "POST", "accept_draft"]:
            with self.subTest(frag=frag):
                self.assertIn(frag, self.body)

    def test_save_shortcut_and_letter_keys(self):
        self.assertIn("Save &amp; rescore", self.body)
        for key in ['e.key === "n"', 'e.key === "e"', 'e.key === "p"', 'e.key === "x"']:
            with self.subTest(key=key):
                self.assertIn(key, self.body)


class TestOverviewUsesDeckHeader(_DeckPage, unittest.TestCase):
    PATH = "/deck/Holy%20Hellfire"
    ACTIVE = "Overview"

    def test_existing_actions_kept(self):
        for frag in ['href="/deck/Holy%20Hellfire/manage"', "reviewDeck()", 'id="syncControls"']:
            with self.subTest(frag=frag):
                self.assertIn(frag, self.body)
        self.assertNotIn("Sync to External", self.body)  # replaced by Update / Push

    def test_unknown_deck_still_renders(self):
        # The Overview page renders a "not synced" shell for unknown names (pre-AN-4 behaviour).
        with patch("app.registry.list_decks", return_value=[]):
            self.assertEqual(self.client.get("/deck/Unknown").status_code, 200)


@unittest.skipUnless(shutil.which("node"), "node not installed: deck_analytics.js tests need it")
class TestDeckAnalyticsJs(unittest.TestCase):
    """Pure mappers from API payloads to view models, run on the real HH payloads."""

    HARNESS = r"""
const fs = require('fs');
global.document = { readyState: 'complete', addEventListener: () => {}, querySelectorAll: () => [],
                    getElementById: () => null, querySelector: () => null };
global.window = {};
eval(fs.readFileSync(process.argv[1], 'utf8'));
eval(fs.readFileSync(process.argv[2], 'utf8'));
const A = window.DeckAnalytics, H = window.HUD;
const fx = (n) => JSON.parse(fs.readFileSync(process.argv[3] + '/' + n + '.json', 'utf8'));
const graph = fx('hh_interactions'), engines = fx('hh_engines'), ident = fx('hh_identity'),
      comp = fx('hh_composition'), combos = fx('hh_combos'), weak = fx('hh_weak_performers');
const g = A.graphData(graph, { mana: false }), gAll = A.graphData(graph, { mana: true });
const lands = new Set(Object.keys(comp.by_card).filter(n => comp.by_card[n].indexOf('lands') !== -1));
const out = {
  g: { nodes: g.nodes.length, edges: g.edges.length, commander: g.commander,
       reasons: Array.from(new Set([].concat.apply([], g.edges.map(e => e.keys)))).sort(),
       kaalia: g.nodes.find(n => n.id === 'Kaalia of the Vast'), isolated: g.isolated.length,
       labelSample: g.edges[0].reasons },
  gAll: { nodes: gAll.nodes.length, edges: gAll.edges.length },
  engines: A.engineList(engines, ident),
  rows: comp.rows.map(A.compRow),
  ref: A.curveRef(58), ref60: A.curveRef(60), ref65: A.curveRef(65), ref0: A.curveRef(0),
  curve: A.curveBuckets([{ cmc: 0, type: 'Artifact' }, { cmc: 3 }, { cmc: 9 }, { cmc: 0, type: 'Basic Land' }]),
  own: combos.one_away.map(A.comboOwnership),
  sorted: A.sortCombos(combos.one_away).map(c => A.comboOwnership(c).rank),
  low: A.lowFit(ident, weak, lands, 6),
  meter: A.fitMeter(ident.fit),
  chartRef: H.barChart([{ label: '0', value: 2 }, { label: '1', value: 30 }], { ref: [4, 40], legend: ['Cards', 'Template'] }),
  delta: [A.deltaChip(0), A.deltaChip(1), A.deltaChip(-1), A.deltaChip(-3), A.deltaChip(17)],
};
process.stdout.write(JSON.stringify(out));
"""

    @classmethod
    def setUpClass(cls):
        res = subprocess.run(["node", "-e", cls.HARNESS, str(HUD_JS), str(DECK_JS), str(FIX)],
                             capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.out = json.loads(res.stdout)

    def test_graph_hides_mana_and_land_only_edges_by_default(self):
        g, gall = self.out["g"], self.out["gAll"]
        self.assertNotIn("mana", g["reasons"])
        self.assertNotIn("lands", g["reasons"])
        self.assertLess(g["nodes"], gall["nodes"])
        self.assertLess(g["edges"], gall["edges"])
        self.assertEqual(gall["edges"], 241)  # ?mana=1 keeps every AN-1 edge

    def test_graph_degree_is_distinct_neighbours_and_labels_are_readable(self):
        g = self.out["g"]
        self.assertEqual(g["commander"], "Kaalia of the Vast")
        self.assertTrue(g["kaalia"]["is_commander"])
        self.assertGreater(g["kaalia"]["degree"], 0)
        self.assertGreater(g["isolated"], 0)
        self.assertTrue(all(r == r.lower() and "_" not in r for r in g["labelSample"]), g["labelSample"])

    def test_engines_map_an2_names_to_name_and_flow(self):
        e = self.out["engines"]
        self.assertEqual(len(e), 10)
        first = e[0]
        self.assertEqual(first["name"], "Attack cheat")
        self.assertEqual(first["flow"], "attack → ETB → attack")
        self.assertIn(first["kind"], ("chain", "loop"))
        self.assertTrue(first["id"])
        self.assertGreater(len(first["cards"]), 1)
        # Pillar = the pillar most members fit (deterministic, from AN-3 fit), else null.
        self.assertTrue(all(x["pillar"] is None or isinstance(x["pillar"], str) for x in e))
        self.assertTrue(any(x["pillar"] for x in e))

    def test_composition_rows(self):
        rows = {r["label"]: r for r in self.out["rows"]}
        self.assertEqual((rows["Lands"]["tone"], rows["Lands"]["text"], rows["Lands"]["under"]), ("signal", "−3 short", True))
        self.assertEqual((rows["Mass disruption"]["tone"], rows["Mass disruption"]["text"]), ("ok", "+1 ok"))
        self.assertEqual((rows["Plan"]["tone"], rows["Plan"]["text"], rows["Plan"]["under"]), ("neutral", "+17 over", False))

    def test_delta_chip_rules(self):
        self.assertEqual(self.out["delta"], [["on target", "ok"], ["+1 ok", "ok"], ["−1 ok", "ok"],
                                             ["−3 short", "signal"], ["+17 over", "neutral"]])

    def test_curve_reference_is_the_template_scaled_to_the_deck(self):
        ref = self.out["ref"]
        self.assertEqual(len(ref), 8)
        self.assertEqual(ref, [0, 4, 18, 12, 10, 7, 4, 3])  # 58 non-land cards (the template's sum) = the template itself
        for n in (60, 65):  # normalised: the line totals the deck's non-land count
            self.assertAlmostEqual(sum(self.out["ref%d" % n]), n, places=6)
        self.assertEqual(ref.index(max(ref)), 2)  # most at 2, tapering
        self.assertEqual(self.out["ref0"], [0] * 8)
        self.assertEqual(self.out["curve"], [1, 0, 0, 1, 0, 0, 0, 1])

    def test_bar_chart_draws_the_reference_line_like_the_macro(self):
        c = self.out["chartRef"]
        self.assertIn('class="hud-chart-ref"', c)
        self.assertIn("hud-swatch--ref", c)
        self.assertNotIn("NaN", c)
        self.assertIn(">40<", c)  # y axis scales to include the reference peak

    def test_combo_ownership_and_order(self):
        own = self.out["own"]
        texts = [o["text"] for o in own]
        self.assertTrue(any(t.startswith("owned · in ") for t in texts), texts)
        self.assertIn("not owned", texts)
        self.assertEqual({o["tone"] for o in own}, {"signal", "neutral"})
        self.assertEqual(self.out["sorted"], sorted(self.out["sorted"]))

    def test_least_on_plan(self):
        low = self.out["low"]
        self.assertEqual(len(low), 6)
        scores = [x["score"] for x in low]
        self.assertEqual(scores, sorted(scores))
        for x in low:
            with self.subTest(card=x["name"]):
                self.assertRegex(x["sub"], r"^\d+ edges? · \S")
                self.assertEqual(x["display"], "%.2f" % x["score"])
        self.assertNotIn("Plains", [x["name"] for x in low])

    def test_fit_meter(self):
        m = self.out["meter"]
        self.assertEqual([s["key"] for s in m], ["pillar", "glue", "generic"])
        self.assertEqual([s["count"] for s in m], [51, 47, 2])
        self.assertAlmostEqual(sum(s["pct"] for s in m), 100, delta=0.5)


if __name__ == "__main__":
    unittest.main()

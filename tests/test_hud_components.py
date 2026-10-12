"""HUD component kit + /styleguide page (HUD-3).

Structure and behaviour only: every documented component renders on
/styleguide from the shared macros, the kit's classes are defined in hud.css,
the macros produce correct markup for edge cases, and hud.js is served and
linked. No literal CSS values are asserted.
"""
import re
import unittest

from fastapi.testclient import TestClient

from app import app, templates

STYLESHEET = "/static/css/hud.css"
SCRIPT = "/static/js/hud.js"

# Root class of every component the styleguide must show.
COMPONENTS = {
    "stat tile": "hud-stat",
    "stat strip": "hud-stat-strip",
    "status chip": "hud-chip",
    "mana pip": "hud-pip",
    "colour distribution": "hud-stack",
    "button": "hud-btn",
    "tabs": "hud-tabs",
    "breadcrumb": "hud-crumbs",
    "sidebar nav": "sidebar",
    "panel": "hud-panel",
    "panel header": "hud-panel-head",
    "key/value": "hud-kv",
    "empty state": "hud-empty",
    "data table": "hud-table",
    "card-art frame": "hud-art",
    "bar chart": "hud-chart",
    "ranked-bar list": "hud-rank",
    "comparison": "hud-compare",
    "heatmap": "hud-heat",
}

TOKEN_GROUPS = ["--surface-0", "--signal", "--data-5", "--mana-w", "--fs-micro", "--sp-4", "--stroke", "--radius-2"]


def _classes(html):
    out = set()
    for attr in re.findall(r'class="([^"]*)"', html):
        out.update(attr.split())
    return out


def _macros():
    return templates.env.get_template("partials/hud.html").module


class TestStyleguidePage(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        client = TestClient(app)
        cls.response = client.get("/styleguide")
        cls.html = cls.response.text
        cls.classes = _classes(cls.html)
        cls.css = client.get(STYLESHEET).text
        cls.js_response = client.get(SCRIPT)

    def test_route_renders(self):
        self.assertEqual(self.response.status_code, 200)
        self.assertIn("text/html", self.response.headers["content-type"])

    def test_every_component_is_shown(self):
        missing = [name for name, cls in COMPONENTS.items() if cls not in self.classes]
        self.assertEqual(missing, [], f"styleguide is missing: {missing}")

    def test_every_component_class_is_styled(self):
        for name, cls in COMPONENTS.items():
            with self.subTest(component=name):
                self.assertRegex(self.css, r"\." + re.escape(cls) + r"[\s{,.:\[>]")

    def test_every_hud_class_used_is_defined_in_stylesheet(self):
        used = {c for c in self.classes if c.startswith("hud-")}
        defined = set(re.findall(r"\.(hud-[a-z0-9-]+)", self.css))
        self.assertEqual(sorted(used - defined), [])

    def test_tokens_are_shown(self):
        for token in TOKEN_GROUPS:
            with self.subTest(token=token):
                self.assertIn(token, self.html)

    def test_every_mana_colour_has_a_pip(self):
        for c in "wubrgcm":
            with self.subTest(colour=c):
                self.assertIn(f"hud-pip--{c}", self.classes)

    def test_table_is_sortable_ready(self):
        table = re.search(r'<table class="hud-table" id="sg-table" data-hud-sortable>(.*?)</table>', self.html, re.S)
        self.assertIsNotNone(table)
        heads = re.findall(r'<th scope="col"[^>]*data-sort="(num|text)" aria-sort="none"', table.group(1))
        self.assertIn("num", heads)
        self.assertIn("text", heads)

    def test_chart_is_svg_coloured_from_tokens(self):
        self.assertRegex(self.html, r'<figure class="hud-chart"[^>]*role="img"')
        self.assertIn('class="hud-chart-bar', self.html)
        self.assertIn('class="hud-chart-ref"', self.html)
        # Chart fills come from tokens in the stylesheet, not inline colours.
        bar_rule = re.search(r"\.hud-chart-bar\s*\{([^}]*)\}", self.css).group(1)
        self.assertIn("var(--data)", bar_rule)

    def test_heatmap_levels_cover_ramp(self):
        levels = set(re.findall(r'class="hud-heat-cell[^"]*" data-level="(\d)"', self.html))
        self.assertTrue({"1", "5"} <= levels, levels)
        for i in range(6):
            self.assertIn(f"--data-{i}", self.css)

    def test_hud_js_served_and_linked_on_every_page(self):
        self.assertEqual(self.js_response.status_code, 200)
        self.assertIn("javascript", self.js_response.headers["content-type"])
        self.assertIn("window.HUD", self.js_response.text)
        self.assertRegex(self.html, r'<script src="' + re.escape(SCRIPT) + r'(\?[^"]*)?"')

    def test_kit_uses_no_glow_or_gradient(self):
        kit = self.css.split("/* 5. COMPONENTS", 1)[1]
        self.assertNotIn("gradient(", kit)
        self.assertNotIn("backdrop-filter", kit)
        # box-shadow is only allowed as a hard inset indicator bar (no blur).
        for shadow in re.findall(r"box-shadow:\s*([^;]+);", kit):
            self.assertRegex(shadow, r"^inset var\(--indicator\) 0 0 var\(--[a-z-]+\)$")


class TestKitMacros(unittest.TestCase):
    def setUp(self):
        self.hud = _macros()

    def test_pips_colourless_and_unknown_symbols(self):
        out = str(self.hud.pips(""))
        self.assertIn("hud-pip--c", out)
        self.assertIn('aria-label="Colourless"', out)
        out = str(self.hud.pips("wuX"))
        self.assertEqual(re.findall(r"hud-pip--([a-z])\b", out), ["w", "u"])

    def test_pips_accepts_list_and_size(self):
        out = str(self.hud.pips(["B", "G"], "sm"))
        self.assertEqual(out.count("hud-pip--sm"), 2)

    def test_status_chip_tones(self):
        self.assertIn("hud-chip--ok", str(self.hud.status_chip("physical")))
        self.assertIn("hud-chip--signal", str(self.hud.status_chip("in_mail")))
        self.assertIn("in mail", str(self.hud.status_chip("in_mail")))
        self.assertIn("hud-chip--neutral", str(self.hud.status_chip("something_new")))

    def test_chip_escapes_text(self):
        self.assertNotIn("<b>", str(self.hud.chip("<b>x</b>")))

    def test_compare_marks_single_leader_only(self):
        out = str(self.hud.compare(
            [{"name": "A"}, {"name": "B"}],
            [{"label": None, "rows": [
                {"label": "Ramp", "values": [11, 8], "lead": "max"},
                {"label": "Tie", "values": [5, 5], "lead": "max"},
            ]}],
        ))
        self.assertEqual(out.count("is-lead"), 1)
        self.assertIn("--compare-n: 2", out)

    def test_heatmap_self_diagonal_and_missing(self):
        out = str(self.hud.heatmap(["a", "b"], ["a", "b"], [[None, 4], [None, None]], self_diag=True))
        self.assertEqual(out.count("is-self"), 2)
        self.assertIn("n/a", out)
        self.assertIn('data-level="5"', out)

    def test_bar_chart_handles_all_zero(self):
        out = str(self.hud.bar_chart([{"label": "1", "value": 0}, {"label": "2", "value": 0}]))
        self.assertEqual(out.count('class="hud-chart-bar'), 2)
        self.assertNotIn("nan", out.lower())

    def test_bar_chart_highlight(self):
        out = str(self.hud.bar_chart([{"label": "1", "value": 3}, {"label": "2", "value": 5}], hl=1))
        self.assertEqual(out.count("is-hl"), 1)

    def test_rank_list_clamps_to_max(self):
        out = str(self.hud.rank_list([{"label": "x", "value": 150}], max=100))
        self.assertIn("width: 100", out)

    def test_table_from_rows_marks_missing_values(self):
        out = str(self.hud.table([{"key": "n", "label": "Name"}, {"key": "c", "label": "CMC", "num": True}], [{"n": "Sol Ring", "c": None}]))
        self.assertIn("—", out)
        self.assertNotIn("data-hud-sortable", out)

    def test_art_without_image_shows_placeholder(self):
        out = str(self.hud.art("Card", None))
        self.assertIn("hud-art-placeholder", out)
        self.assertIn("<figure", out)
        self.assertIn("<a ", str(self.hud.art("Card", "x.jpg", href="/deck/x")))


if __name__ == "__main__":
    unittest.main()

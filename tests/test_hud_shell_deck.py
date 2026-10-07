"""HUD-4: app shell (base.html) and the deck view on the component kit.

Structure and behaviour only, never literal CSS values:
- the sidebar marks the active section by path prefix and exposes it to AT;
- the breadcrumb top bar is opt-in per page;
- the deck view is built from kit components, its page <style> is layout
  only (tokens, no raw colours, no glow/blur), and it uses no legacy
  per-page classes;
- the client-side builders in hud.js produce escaped kit markup (run in node).
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
HUD_JS = ROOT / "static" / "js" / "hud.js"


def _classes(html):
    out = set()
    for attr in re.findall(r'class="([^"]*)"', html):
        out.update(attr.split())
    return out


def _nav(html):
    return html.split('<nav class="sidebar"', 1)[1].split("</nav>", 1)[0]


def _active_nav_labels(html):
    return re.findall(r'class="nav-item active"[^>]*>.*?<span class="nav-label">([^<]+)</span>', _nav(html), re.S)


def _page_style(html):
    """The page-level <style> block(s) passed through {% block extra_css %}."""
    head = html.split("</head>", 1)[0]
    return "\n".join(re.findall(r"<style>(.*?)</style>", head, re.S))


class TestShell(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_active_section_follows_path_prefix(self):
        cases = {
            "/": ["Home"],
            "/decks": ["Commander Decks"],
            "/deck/Some%20Deck": ["Commander Decks"],
            "/inventory": ["Inventory"],
            "/changelog": ["Changelog"],
            "/styleguide": ["Styleguide"],
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                html = self.client.get(path).text
                self.assertEqual(_active_nav_labels(html), expected)

    @patch("app.registry")
    def test_deck_manage_lights_up_decks_section(self, mock_registry):
        mock_registry.list_decks.return_value = [{"name": "TestDeck", "id": "archidekt:1"}]
        mock_registry.registry_id_of.return_value = "archidekt:1"
        html = self.client.get("/deck/TestDeck/manage").text
        self.assertEqual(_active_nav_labels(html), ["Commander Decks"])

    def test_active_item_is_exposed_as_current_page(self):
        nav = _nav(self.client.get("/inventory").text)
        self.assertEqual(nav.count('aria-current="page"'), 1)
        self.assertRegex(nav, r'class="nav-item active"[^>]*aria-current="page"')

    def test_every_nav_link_resolves(self):
        hrefs = re.findall(r'<a href="([^"]+)" class="nav-item', _nav(self.client.get("/").text))
        self.assertGreaterEqual(len(hrefs), 7)
        for href in hrefs:
            with self.subTest(href=href):
                self.assertEqual(self.client.get(href).status_code, 200)

    def test_skip_link_targets_main(self):
        html = self.client.get("/decks").text
        self.assertIn('href="#main"', html)
        self.assertRegex(html, r'<main class="main-content" id="main">')

    def test_topbar_is_opt_in(self):
        self.assertNotIn('class="app-topbar"', self.client.get("/decks").text)
        self.assertNotIn('class="app-topbar"', self.client.get("/").text)
        deck = self.client.get("/deck/TestDeck").text
        self.assertIn('class="app-topbar"', deck)
        topbar = deck.split('class="app-topbar"', 1)[1].split("</nav>", 1)[0]
        self.assertIn('class="hud-crumbs"', topbar)

    def test_shell_classes_are_styled(self):
        css = self.client.get("/static/css/hud.css").text
        for cls in ["skip-link", "app-topbar", "app-topbar-meta", "nav-item", "sidebar"]:
            with self.subTest(cls=cls):
                self.assertRegex(css, r"\." + cls + r"[\s{,.:\[>]")


class TestDeckView(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        client = TestClient(app)
        cls.html = client.get("/deck/Magic%20in%20Stereo").text
        cls.css = client.get("/static/css/hud.css").text
        cls.style = _page_style(cls.html)
        cls.body = cls.html.split("<body", 1)[1]
        cls.client = client

    def test_page_leads_with_stats(self):
        # Page head, then the stat strip comes before the briefing and decklist.
        order = [self.body.find(m) for m in ('class="hud-page-head"', 'id="deckStats"', '<h2>Briefing</h2>', 'id="decklistContainer"')]
        self.assertTrue(all(i > 0 for i in order), order)
        self.assertEqual(order, sorted(order))
        self.assertRegex(self.body, r'<div class="hud-stat-strip" id="deckStats">\s*<div class="hud-stat hud-stat--hero">')

    def test_built_from_kit_components(self):
        classes = _classes(self.html)
        for cls in ["hud-page-head", "hud-stat-strip", "hud-stat", "hud-panel", "hud-panel-head",
                    "hud-kv", "hud-tabs", "hud-tab", "hud-btn", "hud-modal", "hud-modal-dialog", "hud-crumbs"]:
            with self.subTest(cls=cls):
                self.assertIn(cls, classes)
        # Client-rendered parts go through the HUD.* builders.
        for builder in ["HUD.stat(", "HUD.barChart(", "HUD.rankList(", "HUD.stack(", "HUD.art(",
                        "HUD.pips(", "HUD.statusChip(", "HUD.openModal(", "HUD.scryfall("]:
            with self.subTest(builder=builder):
                self.assertIn(builder, self.html)

    def test_no_legacy_page_classes(self):
        classes = _classes(self.html)
        for legacy in ["badge", "mana-pip", "btn", "btn-primary", "modal-overlay", "modal-content",
                       "card-item", "category-title", "deck-primer", "commander-avatar", "pill"]:
            with self.subTest(legacy=legacy):
                self.assertNotIn(legacy, classes)
        # Also not in JS template strings.
        self.assertNotRegex(self.html, r'class=\\?"(badge|mana-pip|btn|pill)[ "]')

    def test_every_hud_class_used_is_defined(self):
        # Covers static markup and the class names inside JS template strings
        # (data-hud-* attributes are hooks, not classes).
        used = set(re.findall(r"(?<![-\w])(hud-[a-z0-9-]+)", self.body))
        defined = set(re.findall(r"\.(hud-[a-z0-9-]+)", self.css))
        self.assertEqual(sorted(used - defined), [])

    def test_page_style_is_layout_only(self):
        self.assertTrue(self.style.strip())
        self.assertNotRegex(self.style, r"#[0-9a-fA-F]{3,8}\b", "raw hex colour in page style")
        self.assertNotRegex(self.style, r"rgba?\(", "raw rgb colour in page style")
        for banned in ["box-shadow", "backdrop-filter", "gradient(", "Inter", "font-family: '", "border-radius"]:
            with self.subTest(banned=banned):
                self.assertNotIn(banned, self.style)
        defined = set(re.findall(r"(--[a-z0-9-]+)\s*:", self.css))
        used = set(re.findall(r"var\((--[a-z0-9-]+)", self.style))
        self.assertEqual(sorted(used - defined), [])
        legacy = {"--bg-color", "--nav-bg", "--text-main", "--text-muted", "--text-heading", "--accent", "--accent-hover", "--row-hover", "--border"}
        self.assertEqual(sorted(used & legacy), [], "page style should use HUD tokens, not legacy aliases")

    def test_no_inline_colour_styles_in_markup(self):
        for attr in re.findall(r'style="([^"]*)"', self.body):
            with self.subTest(style=attr):
                self.assertNotRegex(attr, r"#[0-9a-fA-F]{3,8}\b|rgba?\(")

    def test_card_grid_uses_cdn_images_not_api_redirect(self):
        script = self.body.split("<script>", 1)[1]
        self.assertNotIn("api.scryfall.com/cards/${", script)
        self.assertNotIn("svgs.scryfall.io", script)

    def test_deck_name_is_escaped_everywhere(self):
        html = self.client.get("/deck/%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E").text
        self.assertNotIn("<img src=x onerror=alert(1)>", html)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html)

    def test_modal_starts_closed_and_is_a_dialog(self):
        self.assertRegex(self.body, r'<div id="cardModal" class="hud-modal" aria-hidden="true">')
        self.assertRegex(self.body, r'role="dialog" aria-modal="true" aria-labelledby="modalCardName"')
        self.assertIn('id="modalCardName"', self.body)
        self.assertIn("data-hud-close", self.body)

    def test_actions_and_links_preserved(self):
        self.assertIn('href="/deck/Magic%20in%20Stereo/manage"', self.body)
        for fn in ["syncDeck()", "reviewDeck()", "togglePrimerEdit()", "savePrimer()"]:
            with self.subTest(fn=fn):
                self.assertIn(fn, self.body)
        for host in ["edhrec.com/commanders", "archidekt.com/decks", "commandersalt.com/details/deck", "moxfield.com/decks"]:
            with self.subTest(host=host):
                self.assertIn(host, self.body)


@unittest.skipUnless(shutil.which("node"), "node not installed: hud.js builder tests need it")
class TestHudJsBuilders(unittest.TestCase):
    """Run static/js/hud.js in node with a minimal DOM stub and call the builders."""

    HARNESS = r"""
const fs = require('fs');
const listeners = {};
global.document = { readyState: 'complete', addEventListener: (t, f) => { listeners[t] = f; },
                    querySelectorAll: () => [], getElementById: () => null };
global.window = {};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const H = window.HUD;
const out = {
  stat: H.stat('Avg <CMC>', '3.45', {mod: 'hero signal', foot: 'non-land'}),
  art: H.art('Kaalia <x>', 'https://img/a.jpg', {kind: 'card', fallback: 'https://img/b.jpg', colors: 'WBR', chip: ['Core', 'signal'],
             attrs: {role: 'button', 'data-card-index': 3}}),
  artEmpty: H.art('No image', '', {}),
  sf: H.scryfall('716D0B3B-bac9-4fb8-882e-bd6171864043', 'art_crop'),
  sfNone: H.scryfall('', 'normal'),
  chart: H.barChart([{label: '0', value: 0}, {label: '1', value: 4}, {label: '2', value: 8}], {hl: 2}),
  chartZero: H.barChart([{label: '0', value: 0}]),
  rank: H.rankList([{label: 'Lands', value: 35}, {label: 'Creatures', value: 70}], {numbered: false}),
  stack: H.stack([{color: 'U', value: 3}, {color: 'G', value: 1}, {color: 'R', value: 0}]),
  listeners: Object.keys(listeners).sort(),
};
process.stdout.write(JSON.stringify(out));
"""

    @classmethod
    def setUpClass(cls):
        res = subprocess.run(["node", "-e", cls.HARNESS, str(HUD_JS)], capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.out = json.loads(res.stdout)

    def test_stat_escapes_and_applies_mods(self):
        s = self.out["stat"]
        self.assertIn('class="hud-stat hud-stat--hero hud-stat--signal"', s)
        self.assertIn("Avg &lt;CMC&gt;", s)
        self.assertIn('class="hud-stat-foot"', s)

    def test_art_frame(self):
        a = self.out["art"]
        self.assertTrue(a.startswith('<figure class="hud-art hud-art--card" role="button" data-card-index="3">'), a)
        self.assertIn('data-fallback="https://img/b.jpg"', a)
        self.assertIn("Kaalia &lt;x&gt;", a)
        self.assertNotIn("<x>", a)
        self.assertEqual(re.findall(r"hud-pip--([a-z])\b", a), ["w", "b", "r"])
        self.assertIn("hud-chip--signal", a)
        self.assertIn("hud-art-placeholder", self.out["artEmpty"])

    def test_scryfall_urls(self):
        sf = self.out["sf"]
        self.assertEqual(sf["src"], "https://cards.scryfall.io/art_crop/front/7/1/716d0b3b-bac9-4fb8-882e-bd6171864043.jpg")
        self.assertIn("api.scryfall.com/cards/716d0b3b-bac9-4fb8-882e-bd6171864043?format=image&version=art_crop", sf["fallback"])
        self.assertEqual(self.out["sfNone"], {"src": "", "fallback": ""})

    def test_bar_chart_matches_macro_structure(self):
        c = self.out["chart"]
        self.assertEqual(c.count('class="hud-chart-bar'), 3)
        self.assertEqual(c.count("is-hl"), 1)
        self.assertIn("--chart-n: 3", c)
        self.assertNotIn("NaN", c)
        self.assertNotIn("NaN", self.out["chartZero"])

    def test_rank_list_scales_to_top_value(self):
        r = self.out["rank"]
        self.assertIn("width: 50%", r)
        self.assertIn("width: 100%", r)
        self.assertEqual(r.count('class="hud-rank-pos"></span>'), 2)

    def test_stack_skips_zero_segments_in_bar(self):
        s = self.out["stack"]
        bar = s.split('<div class="hud-stack-legend">', 1)[0]
        self.assertIn("hud-swatch--u", bar)
        self.assertNotIn("hud-swatch--r", bar)
        self.assertIn("75%", s)

    def test_modal_and_fallback_handlers_registered(self):
        self.assertEqual(self.out["listeners"], ["click", "error", "keydown"])


if __name__ == "__main__":
    unittest.main()

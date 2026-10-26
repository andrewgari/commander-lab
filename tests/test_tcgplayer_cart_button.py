"""'Buy on TCGplayer' cart button component (hud.tcg_cart + TcgCart.button).

One component, two renderers: the Jinja macro in templates/partials/hud.html
(server-rendered buy lists) and static/js/tcgplayer_cart.js (lists rendered
from fetched JSON). Covers, for BOTH renderers:
  - hidden for 0 items: empty list, None, all-zero qty, blank names -> no markup
  - shown for 1 item (no count badge, "1 card") and N items (count badge = copies)
  - the list passed in is the list in the cart: data-tcg-cart and the URL's
    `c` param carry exactly the normalized input (merge, DFC front face, qty 1
    default), in order
  - new tab: target=_blank + rel="noopener noreferrer"
  - Jinja and JS emit byte-identical markup for the same list
  - TcgCart.mount() hides its host for an empty list and shows it otherwise
  - styling: amber BUY tokens only, reduced-motion fallback, styleguide demo
The JS half runs under node (same subprocess pattern as test_scryfall_image.py).
"""
import html as htmllib
import json
import re
import shutil
import subprocess
import unittest
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from fastapi.testclient import TestClient

from src.server.app.main import app, templates
import src.server.infra.providers.tcgplayer as tcgplayer

ROOT = Path(__file__).resolve().parent.parent
CART_JS = ROOT / "static" / "js" / "tcgplayer_cart.js"
HUD_CSS = ROOT / "static" / "css" / "hud.css"

DFC = "Fable of the Mirror-Breaker // Reflection of Kiki-Jiki"

# name -> (input list, expected normalized cart)
CASES = {
    "one": ([{"name": "Esper Sentinel", "qty": 1}], [{"name": "Esper Sentinel", "qty": 1}]),
    "one_qty_default": ([{"name": "Sol Ring"}], [{"name": "Sol Ring", "qty": 1}]),
    "one_qty_three": ([{"name": "Island", "qty": 3}], [{"name": "Island", "qty": 3}]),
    "many": (
        [{"name": "Smothering Tithe", "qty": 1}, {"name": DFC, "qty": 1}, {"name": "Lim-Dûl's Vault"},
         {"name": "Fire // Ice", "qty": "2"}, {"name": "Smothering Tithe", "qty": 1}],
        [{"name": "Smothering Tithe", "qty": 2}, {"name": "Fable of the Mirror-Breaker", "qty": 1},
         {"name": "Lim-Dûl's Vault", "qty": 1}, {"name": "Fire", "qty": 2}],
    ),
    "specials": (
        [{"name": "Borrowing 100,000 Arrows", "qty": 1}, {"name": "R&D's Secret Lair", "qty": 1},
         {"name": "<script>alert(1)</script>", "qty": 1}, {"name": 'Kongming, "Sleeping Dragon"', "qty": 1}],
        [{"name": "Borrowing 100,000 Arrows", "qty": 1}, {"name": "R&D's Secret Lair", "qty": 1},
         {"name": "<script>alert(1)</script>", "qty": 1}, {"name": 'Kongming, "Sleeping Dragon"', "qty": 1}],
    ),
    "skips_junk": (
        [{"name": "", "qty": 1}, {"name": "Sol Ring", "qty": 0}, {"name": "Mana Crypt", "qty": -2},
         {"name": "  Arcane Signet  ", "qty": 1}, None, "Sol Ring"],
        [{"name": "Arcane Signet", "qty": 1}],
    ),
    "with_set": (
        [{"name": "Lightning Bolt", "qty": 1, "set": "sld"},
         {"name": "Lightning Bolt", "qty": 2, "set": "2x2"},
         {"name": "Lightning Bolt", "qty": 1}],
        [{"name": "Lightning Bolt", "qty": 1, "set": "SLD"},
         {"name": "Lightning Bolt", "qty": 2, "set": "2X2"},
         {"name": "Lightning Bolt", "qty": 1}],
    ),
}
EMPTY = {
    "empty_list": [],
    "none": None,
    "all_zero": [{"name": "Sol Ring", "qty": 0}, {"name": "Arcane Signet", "qty": 0}],
    "blank_names": [{"name": "", "qty": 1}, {"name": "   ", "qty": 2}, {"qty": 1}],
}


def _macro():
    return templates.env.get_template("partials/hud.html").module.tcg_cart


def _jinja(cards, **kw):
    return str(_macro()(cards, **kw)).strip()


class _A(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.anchors = []

    def handle_starttag(self, tag, attrs):
        if tag == "a":
            self.anchors.append(dict(attrs))


def _anchor(markup):
    """Parse the single <a> the component emits -> (attrs dict, inner html)."""
    m = re.fullmatch(r"<a [^>]*>(.*)</a>", markup, re.S)
    assert m, f"not a single anchor: {markup!r}"
    p = _A()
    p.feed(markup)
    assert len(p.anchors) == 1, p.anchors
    return p.anchors[0], m.group(1)


def _cart_from_url(url):
    parts = urlsplit(url)
    q = parse_qs(parts.query)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == tcgplayer.BASE_URL
    assert q["productline"] == ["Magic"]
    out = []
    for line in q["c"][0].split("||"):
        m = re.fullmatch(r"(\d+) (.+?)(?: \[([^\]]+)\])?", line)
        assert m, f"unparseable cart line: {line!r}"
        it = {"name": m.group(2), "qty": int(m.group(1))}
        if m.group(3):
            it["set"] = m.group(3)
        out.append(it)
    return out


def _visible_text(inner):
    inner = re.sub(r'<span class="hud-sr">.*?</span>', "", inner)
    return htmllib.unescape(re.sub(r"<[^>]+>", "", inner))


class TestJinjaMacro(unittest.TestCase):
    """hud.tcg_cart(cards): the server-rendered component."""

    def test_hidden_for_zero_items(self):
        for name, cards in EMPTY.items():
            with self.subTest(case=name):
                self.assertEqual(_jinja(cards), "")

    def test_shown_for_one_item(self):
        attrs, inner = _anchor(_jinja([{"name": "Esper Sentinel", "qty": 1}]))
        self.assertEqual(attrs["data-tcg-count"], "1")
        self.assertNotIn("hud-btn-count", inner, "a single card gets no count badge")
        self.assertIn('<span class="hud-sr"> (1 card, opens in a new tab)</span>', inner)
        self.assertEqual(_visible_text(inner), "Buy on TCGplayer")

    def test_shown_for_n_items_with_copy_count(self):
        cards, expected = CASES["many"]
        attrs, inner = _anchor(_jinja(cards))
        copies = sum(c["qty"] for c in expected)  # 6 copies across 4 lines
        self.assertEqual(attrs["data-tcg-count"], str(copies))
        self.assertIn(f'<span class="hud-btn-count" aria-hidden="true">{copies}</span>', inner)
        self.assertIn(f"({copies} cards, opens in a new tab)", inner)

    def test_correct_list_is_passed_to_the_cart(self):
        for name, (cards, expected) in CASES.items():
            with self.subTest(case=name):
                attrs, _ = _anchor(_jinja(cards))
                self.assertEqual(json.loads(attrs["data-tcg-cart"]), expected)
                self.assertEqual(_cart_from_url(attrs["href"]), expected)
                self.assertEqual(attrs["href"], tcgplayer.build_tcgplayer_url(cards))

    def test_opens_in_new_tab_safely(self):
        attrs, _ = _anchor(_jinja(CASES["one"][0]))
        self.assertEqual(attrs["target"], "_blank")
        self.assertEqual(set(attrs["rel"].split()), {"noopener", "noreferrer"})

    def test_styling_hooks(self):
        attrs, inner = _anchor(_jinja(CASES["one"][0]))
        self.assertEqual(attrs["class"].split(), ["hud-btn", "hud-btn--buy"])
        attrs, _ = _anchor(_jinja(CASES["one"][0], size="sm"))
        self.assertEqual(attrs["class"].split(), ["hud-btn", "hud-btn--buy", "hud-btn--sm"])
        self.assertIn('aria-hidden="true" focusable="false"', inner, "icon is decorative")

    def test_custom_label(self):
        _, inner = _anchor(_jinja(CASES["one"][0], label="Buy this card"))
        self.assertTrue(_visible_text(inner).startswith("Buy this card"))

    def test_hostile_names_are_escaped(self):
        markup = _jinja(CASES["specials"][0])
        self.assertNotIn("<script>", markup)
        self.assertEqual(markup.count("<a "), 1)
        attrs, _ = _anchor(markup)
        self.assertEqual(json.loads(attrs["data-tcg-cart"]), CASES["specials"][1])


class TestCartHelper(unittest.TestCase):
    """tcgplayer.cart(): the data the macro renders."""

    def test_empty(self):
        for name, cards in EMPTY.items():
            with self.subTest(case=name):
                self.assertEqual(tcgplayer.cart(cards), {"items": [], "count": 0, "url": ""})

    def test_items_count_url(self):
        cards, expected = CASES["many"]
        c = tcgplayer.cart(cards)
        self.assertEqual(c["items"], expected)
        self.assertEqual(c["count"], 6)
        self.assertEqual(c["url"], tcgplayer.build_tcgplayer_url(cards))


JS_HARNESS = r"""
const T = require(process.argv[1]);
const cases = JSON.parse(process.argv[2]), empty = JSON.parse(process.argv[3]);
const out = { button: {}, buttonSm: {}, items: {}, url: {}, count: {}, empty: {}, mount: {} };
for (const [k, [cards]] of Object.entries(cases)) {
  out.button[k] = T.button(cards);
  out.buttonSm[k] = T.button(cards, { size: "sm" });
  out.items[k] = T.items(cards);
  out.url[k] = T.url(cards);
  out.count[k] = T.count(cards);
}
for (const [k, cards] of Object.entries(empty)) {
  out.empty[k] = { button: T.button(cards), url: T.url(cards), items: T.items(cards), count: T.count(cards) };
}
// mount(): fake host element; querySelector('a') finds the rendered anchor.
function host() {
  return { innerHTML: "stale", hidden: false,
           querySelector(sel) { return sel === "a" && this.innerHTML.startsWith("<a ") ? { tagName: "A", html: this.innerHTML } : null; } };
}
for (const [k, cards] of [["zero", []], ["one", cases.one[0]], ["many", cases.many[0]]]) {
  const el = host(), a = T.mount(el, cards);
  out.mount[k] = { hidden: el.hidden, html: el.innerHTML, anchor: !!a };
}
out.mountNull = T.mount(null, cases.one[0]);
out.label = T.button(cases.one[0], { label: "Buy this card" });
process.stdout.write(JSON.stringify(out));
"""


@unittest.skipUnless(shutil.which("node"), "node not installed: tcgplayer_cart.js tests need it")
class TestJsComponent(unittest.TestCase):
    """TcgCart.button(cards): the client-rendered component."""

    @classmethod
    def setUpClass(cls):
        res = subprocess.run(
            ["node", "-e", JS_HARNESS, str(CART_JS), json.dumps(CASES), json.dumps(EMPTY)],
            capture_output=True, text=True, timeout=60)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.r = json.loads(res.stdout)

    def test_hidden_for_zero_items(self):
        for name in EMPTY:
            with self.subTest(case=name):
                self.assertEqual(self.r["empty"][name], {"button": "", "url": "", "items": [], "count": 0})

    def test_shown_for_one_and_n_items(self):
        attrs, inner = _anchor(self.r["button"]["one"])
        self.assertEqual(attrs["data-tcg-count"], "1")
        self.assertNotIn("hud-btn-count", inner)
        attrs, inner = _anchor(self.r["button"]["many"])
        self.assertEqual(attrs["data-tcg-count"], "6")
        self.assertIn('<span class="hud-btn-count" aria-hidden="true">6</span>', inner)

    def test_correct_list_is_passed_to_the_cart(self):
        for name, (cards, expected) in CASES.items():
            with self.subTest(case=name):
                self.assertEqual(self.r["items"][name], expected)
                attrs, _ = _anchor(self.r["button"][name])
                self.assertEqual(json.loads(attrs["data-tcg-cart"]), expected)
                self.assertEqual(_cart_from_url(attrs["href"]), expected)

    def test_url_matches_python_builder(self):
        for name, (cards, _) in CASES.items():
            with self.subTest(case=name):
                self.assertEqual(self.r["url"][name], tcgplayer.build_tcgplayer_url(cards))
                self.assertEqual(self.r["count"][name], tcgplayer.cart(cards)["count"])

    def test_opens_in_new_tab_safely(self):
        attrs, _ = _anchor(self.r["button"]["one"])
        self.assertEqual(attrs["target"], "_blank")
        self.assertEqual(set(attrs["rel"].split()), {"noopener", "noreferrer"})

    def test_markup_identical_to_jinja_macro(self):
        for name, (cards, _) in CASES.items():
            with self.subTest(case=name):
                self.assertEqual(self.r["button"][name], _jinja(cards))
                self.assertEqual(self.r["buttonSm"][name], _jinja(cards, size="sm"))
        self.assertEqual(self.r["label"], _jinja(CASES["one"][0], label="Buy this card"))

    def test_mount_hides_host_when_empty(self):
        m = self.r["mount"]
        self.assertEqual(m["zero"], {"hidden": True, "html": "", "anchor": False})
        for k in ("one", "many"):
            with self.subTest(case=k):
                self.assertFalse(m[k]["hidden"])
                self.assertTrue(m[k]["anchor"])
                self.assertEqual(m[k]["html"], self.r["button"][k])
        self.assertIsNone(self.r["mountNull"])


class TestStylingAndWiring(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        client = TestClient(app)
        cls.css = HUD_CSS.read_text()
        cls.styleguide = client.get("/styleguide").text
        cls.js = client.get("/static/js/tcgplayer_cart.js")

    def _rule(self, selector):
        m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", self.css)
        self.assertIsNotNone(m, f"{selector} not styled")
        return m.group(1)

    def test_buy_button_wears_the_buy_chip_amber(self):
        # Same three tokens as .hud-chip--signal, the BUY chip.
        chip = self._rule(".hud-chip--signal")
        for tok in ("var(--signal)", "var(--signal-dim)", "var(--signal-line)"):
            self.assertIn(tok, chip)
        buy = self._rule(".hud-btn--buy")
        self.assertIn("background: var(--signal-dim)", buy)
        self.assertIn("border-color: var(--signal-line)", buy)
        self.assertIn("color: var(--signal)", buy)

    def test_no_magic_colours(self):
        for block in re.findall(r"\.hud-btn--buy[^{]*\{([^}]*)\}", self.css):
            self.assertNotRegex(block, r"#[0-9a-fA-F]{3,8}\b|rgba?\(")

    def test_hover_fills_solid_signal_focus_keeps_ring_visible(self):
        rule = self._rule(".hud-btn--buy:hover")
        self.assertIn("background: var(--signal)", rule)
        self.assertIn("color: var(--text-on-signal)", rule)
        # A signal focus ring on a signal fill would vanish: focus must not fill.
        self.assertNotRegex(self.css, r"\.hud-btn--buy:focus(-visible)?\s*[,{][^}]*background")

    def test_reduced_motion_fallback(self):
        # Motion is opt-in: every transition/transform lives in a no-preference block.
        base = re.findall(r"^\.hud-btn--buy[^{]*\{([^}]*)\}", self.css, re.M)
        self.assertTrue(base)
        for block in base:
            self.assertNotRegex(block, r"transition|transform")
        blocks = re.findall(r"@media \(prefers-reduced-motion: no-preference\) \{(.*?)\n\}", self.css, re.S)
        self.assertTrue(any(".hud-btn--buy" in b and "transition:" in b and "transform:" in b for b in blocks))

    def test_styleguide_demo(self):
        demo = re.search(r'id="buy-buttons"(.*?)</div>', self.styleguide, re.S).group(1)
        self.assertEqual(demo.count('class="hud-btn hud-btn--buy'), 2, "N-card + single-card; empty renders nothing")
        self.assertIn("hud-btn--buy hud-btn--sm", demo)

    def test_script_served_and_loaded_everywhere(self):
        self.assertEqual(self.js.status_code, 200)
        self.assertIn('src="/static/js/tcgplayer_cart.js', self.styleguide)


if __name__ == "__main__":
    unittest.main()

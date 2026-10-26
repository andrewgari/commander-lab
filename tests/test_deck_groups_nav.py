"""NAV: the sidebar "Commander Decks" item opens a flyout of four deck groups,
each linking to /decks?status=<value>, and /decks honours that filter.

Covers:
- the flyout markup in base.html (trigger wiring, four entries, labels,
  hrefs, current-group marking) on every page;
- the ?status route/template contract for each status plus no param and an
  unknown value;
- the nav item stays active on /decks, /decks?status=… and /deck/*;
- hud.js menu behaviour (open/close, keyboard, outside click) in node against
  a small DOM stub.
"""
import json
import re
import subprocess
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

import src.server.domain.decks.registry as registry
from src.server.app.main import DECK_GROUPS, app

ROOT = Path(__file__).resolve().parent.parent
HUD_JS = ROOT / "static" / "js" / "hud.js"

EXPECTED = [
    ("Physical Decks", "physical"),
    ("Virtual Decks", "digital"),
    ("WIP Decks", "testing"),
    ("Retired Decks", "retired"),
]


def _nav(html):
    return html.split('<nav class="sidebar"', 1)[1].split("</nav>", 1)[0]


def _trigger(html):
    m = re.search(r'<a href="/decks"[^>]*class="nav-item[^"]*"[^>]*>', _nav(html))
    return m.group(0) if m else None


def _menu(html):
    m = re.search(r'<div class="hud-menu" id="nav-menu-decks"[^>]*>(.*?)</div>', _nav(html), re.S)
    return m


def _entries(html):
    return re.findall(r'<a class="hud-menu-item" href="([^"]+)"([^>]*)>.*?</span>([^<]+)</a>', _menu(html).group(1), re.S)


def _status_filter(html):
    return json.loads(re.search(r"const STATUS_FILTER = (.*?);", html).group(1))


class TestDeckGroupsConfig(unittest.TestCase):
    def test_groups_map_onto_existing_statuses(self):
        self.assertEqual([(g["label"], g["status"]) for g in DECK_GROUPS], EXPECTED)
        self.assertEqual({g["status"] for g in DECK_GROUPS}, registry.VALID_STATUSES)


class TestFlyoutMarkup(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_trigger_is_wired_to_menu(self):
        trig = _trigger(self.client.get("/").text)
        self.assertIsNotNone(trig)
        self.assertIn("data-hud-menu", trig)
        self.assertIn('aria-controls="nav-menu-decks"', trig)
        self.assertIn('aria-expanded="false"', trig)
        # Stays a link (no role override): without JS it navigates to /decks.
        self.assertNotIn("role=", trig)

    def test_menu_starts_hidden_with_four_entries(self):
        html = self.client.get("/").text
        m = _menu(html)
        self.assertIsNotNone(m)
        self.assertIn("hidden", m.group(0).split(">", 1)[0])
        entries = _entries(html)
        self.assertEqual([(label.strip(), href) for href, _, label in entries],
                         [(label, f"/decks?status={s}") for label, s in EXPECTED])

    def test_menu_is_on_every_page(self):
        for path in ["/", "/decks", "/inventory", "/tags", "/styleguide", "/deck/TestDeck"]:
            with self.subTest(path=path):
                html = self.client.get(path).text
                self.assertEqual(len(_entries(html)), 4)
                self.assertEqual(_nav(html).count('id="nav-menu-decks"'), 1)

    def test_current_group_is_marked(self):
        for _, status in EXPECTED:
            with self.subTest(status=status):
                entries = _entries(self.client.get(f"/decks?status={status}").text)
                current = [href for href, attrs, _ in entries if 'aria-current="page"' in attrs]
                self.assertEqual(current, [f"/decks?status={status}"])
        for path in ["/decks", "/decks?status=bogus", "/inventory?status=physical"]:
            with self.subTest(path=path):
                entries = _entries(self.client.get(path).text)
                self.assertFalse(any("aria-current" in attrs for _, attrs, _ in entries))

    def test_nav_item_stays_active_on_deck_pages(self):
        for path in ["/decks", "/decks?status=retired", "/deck/Some%20Deck"]:
            with self.subTest(path=path):
                trig = _trigger(self.client.get(path).text)
                self.assertIn("nav-item active", trig)
                self.assertIn('aria-current="page"', trig)
        self.assertNotIn("active", _trigger(self.client.get("/inventory").text))

    def test_menu_classes_are_styled(self):
        css = self.client.get("/static/css/hud.css").text
        for cls in ["hud-menu", "hud-menu-list", "hud-menu-label", "hud-menu-item", "nav-caret"]:
            with self.subTest(cls=cls):
                self.assertRegex(css, r"\." + cls + r"[\s{,.:\[>]")

    def test_styleguide_documents_the_menu(self):
        html = self.client.get("/styleguide").text
        self.assertIn('id="sg-menu"', html)
        self.assertIn('aria-controls="sg-menu"', html)
        self.assertIn("hud.menu(", html)


class TestDecksStatusFilter(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_each_status_filters_the_list(self):
        for label, status in EXPECTED:
            with self.subTest(status=status):
                res = self.client.get(f"/decks?status={status}")
                self.assertEqual(res.status_code, 200)
                html = res.text
                self.assertEqual(_status_filter(html), status)
                ind = re.search(r'<div class="deck-filter" id="deckFilter" data-status="([a-z]+)">(.*?)</div>', html, re.S)
                self.assertIsNotNone(ind, "active filter indicator missing")
                self.assertEqual(ind.group(1), status)
                self.assertIn(label, ind.group(2))
                self.assertRegex(ind.group(2), r'<a [^>]*href="/decks"[^>]*>Clear filter')
                self.assertIn(f"<title>{label} | Commander Decks", html)
                # The "show all" toggle makes no sense inside one group.
                self.assertNotIn('id="showAllFolders"', html)

    def test_no_param_shows_everything(self):
        html = self.client.get("/decks").text
        self.assertIsNone(_status_filter(html))
        self.assertNotIn('id="deckFilter"', html)
        self.assertIn('id="showAllFolders"', html)

    def test_unknown_or_empty_status_falls_back_to_all(self):
        for q in ["bogus", "", "<script>"]:
            with self.subTest(q=q):
                res = self.client.get("/decks", params={"status": q})
                self.assertEqual(res.status_code, 200)
                self.assertIsNone(_status_filter(res.text))
                self.assertNotIn('id="deckFilter"', res.text)
                self.assertNotIn("<script>;", res.text)

    def test_status_is_case_insensitive(self):
        html = self.client.get("/decks", params={"status": " Physical "}).text
        self.assertEqual(_status_filter(html), "physical")
        current = [href for href, attrs, _ in _entries(html) if 'aria-current="page"' in attrs]
        self.assertEqual(current, ["/decks?status=physical"])

    def test_render_applies_filter(self):
        html = self.client.get("/decks?status=testing").text
        self.assertIn("if (STATUS_FILTER && status !== STATUS_FILTER) return;", html)

    def test_modal_status_control_uses_the_kit(self):
        # LOCK (t_e9ec412a) replaced the static 4-option select: the modal
        # renders HUD.deckStatusControl (Virtual/WIP/Retired, or a locked
        # Physical). Status values are unchanged.
        html = self.client.get("/decks").text
        self.assertIn("HUD.deckStatusControl(deck", html)
        self.assertIn("HUD.bindDeckStatus(", html)
        self.assertNotIn('<option value="physical">Physical</option>', html)


class TestMenuBehaviour(unittest.TestCase):
    """Run hud.js in node against a tiny DOM stub and drive the flyout."""

    HARNESS = r"""
const fs = require('fs');
function mkEl(tag, attrs) {
  const el = { tagName: tag, attrs: Object.assign({}, attrs || {}), listeners: {}, children: [], parent: null,
    hidden: false, style: {}, offsetWidth: 200, offsetHeight: 160, focused: false, clicked: 0,
    getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    setAttribute(k, v) { this.attrs[k] = String(v); },
    hasAttribute(k) { return k in this.attrs; },
    addEventListener(t, f) { (this.listeners[t] = this.listeners[t] || []).push(f); },
    dispatch(t, ev) { ev = Object.assign({ type: t, target: this, preventDefault() { this.defaultPrevented = true; } }, ev || {});
      let n = this; while (n) { (n.listeners[t] || []).forEach(f => f(ev)); n = n.parent; }
      (docListeners[t] || []).forEach(f => f(ev)); return ev; },
    contains(o) { while (o) { if (o === this) return true; o = o.parent; } return false; },
    querySelectorAll(sel) { const out = []; const walk = n => n.children.forEach(c => {
        if (sel === '.hud-menu-item' && c.attrs.class === 'hud-menu-item') out.push(c); walk(c); }); walk(this); return out; },
    getBoundingClientRect() { return { top: 120, right: 56, left: 0, bottom: 160 }; },
    focus() { document.activeElement = this; },
    click() { this.clicked++; },
    append(c) { c.parent = this; this.children.push(c); return c; },
  };
  return el;
}
const docListeners = {};
const body = mkEl('body');
const trigger = body.append(mkEl('a', { 'data-hud-menu': '', 'aria-controls': 'm', 'aria-expanded': 'false' }));
const panel = body.append(mkEl('div', { id: 'm', class: 'hud-menu' })); panel.hidden = true;
const items = [0, 1, 2, 3].map(i => panel.append(mkEl('a', { class: 'hud-menu-item', href: '/decks?status=' + i })));
const outside = body.append(mkEl('p'));
global.window = { innerWidth: 390, innerHeight: 800, addEventListener() {} };
global.document = { readyState: 'complete', activeElement: body,
  addEventListener(t, f) { (docListeners[t] = docListeners[t] || []).push(f); },
  getElementById(id) { return id === 'm' ? panel : null; },
  querySelectorAll(sel) { return sel === '[data-hud-menu]' ? [trigger] : []; } };
eval(fs.readFileSync(process.argv[1], 'utf8'));
const log = [];
const snap = (step) => log.push({ step, open: !panel.hidden, expanded: trigger.getAttribute('aria-expanded'),
  focus: document.activeElement === trigger ? 'trigger' : items.indexOf(document.activeElement), left: panel.style.left });
const docKey = (key) => (docListeners.keydown || []).forEach(f => f({ key, preventDefault() {} }));

let ev = trigger.dispatch('click', { detail: 1, button: 0 }); snap('mouse-open'); log.push({ prevented: !!ev.defaultPrevented });
outside.dispatch('click', { detail: 1 }); snap('outside-click');
trigger.dispatch('click', { detail: 1, button: 0 }); trigger.dispatch('click', { detail: 1, button: 0 }); snap('toggle-closed');
ev = trigger.dispatch('click', { detail: 1, button: 0, ctrlKey: true }); snap('ctrl-click'); log.push({ ctrlPrevented: !!ev.defaultPrevented });
trigger.dispatch('keydown', { key: 'Enter' }); snap('enter-open');
panel.dispatch('keydown', { key: 'ArrowDown', target: document.activeElement }); snap('down');
panel.dispatch('keydown', { key: 'End' }); snap('end');
panel.dispatch('keydown', { key: 'ArrowDown' }); snap('wrap');
panel.dispatch('keydown', { key: 'ArrowUp' }); snap('up');
docKey('Escape'); snap('escape');
trigger.dispatch('keydown', { key: ' ' }); snap('space-open');
panel.dispatch('keydown', { key: ' ' }); log.push({ spaceActivates: items[0].clicked });
items[0].dispatch('focusout', { relatedTarget: outside }); snap('tab-away');
trigger.dispatch('keydown', { key: 'ArrowUp' }); snap('up-open-last');
process.stdout.write(JSON.stringify(log));
"""

    @classmethod
    def setUpClass(cls):
        res = subprocess.run(["node", "-e", cls.HARNESS, str(HUD_JS)], capture_output=True, text=True, timeout=30)
        if res.returncode != 0:
            raise AssertionError(res.stderr)
        cls.log = json.loads(res.stdout)
        cls.steps = {e["step"]: e for e in cls.log if "step" in e}

    def test_mouse_click_opens_and_positions_without_navigating(self):
        s = self.steps["mouse-open"]
        self.assertTrue(s["open"])
        self.assertEqual(s["expanded"], "true")
        self.assertTrue(any(e.get("prevented") for e in self.log))
        self.assertTrue(s["left"].endswith("px"))
        # 56px rail + 200px panel fits inside a 390px viewport: beside the rail.
        self.assertEqual(s["left"], "60px")

    def test_outside_click_and_second_click_close(self):
        self.assertFalse(self.steps["outside-click"]["open"])
        self.assertEqual(self.steps["outside-click"]["expanded"], "false")
        self.assertFalse(self.steps["toggle-closed"]["open"])

    def test_modified_click_keeps_link_behaviour(self):
        self.assertFalse(self.steps["ctrl-click"]["open"])
        self.assertTrue(any(e.get("ctrlPrevented") is False for e in self.log))

    def test_keyboard_open_moves_focus_into_menu(self):
        self.assertTrue(self.steps["enter-open"]["open"])
        self.assertEqual(self.steps["enter-open"]["focus"], 0)
        self.assertTrue(self.steps["space-open"]["open"])
        self.assertEqual(self.steps["space-open"]["focus"], 0)
        self.assertEqual(self.steps["up-open-last"]["focus"], 3)

    def test_arrow_keys_move_and_wrap(self):
        self.assertEqual(self.steps["down"]["focus"], 1)
        self.assertEqual(self.steps["end"]["focus"], 3)
        self.assertEqual(self.steps["wrap"]["focus"], 0)
        self.assertEqual(self.steps["up"]["focus"], 3)

    def test_escape_closes_and_returns_focus(self):
        s = self.steps["escape"]
        self.assertFalse(s["open"])
        self.assertEqual(s["expanded"], "false")
        self.assertEqual(s["focus"], "trigger")

    def test_space_activates_item_and_tab_away_closes(self):
        self.assertTrue(any(e.get("spaceActivates") == 1 for e in self.log))
        self.assertFalse(self.steps["tab-away"]["open"])


if __name__ == "__main__":
    unittest.main()

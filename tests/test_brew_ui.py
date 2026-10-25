"""UI contract tests for the Brew console autocomplete fields and nav entry.

Pins the interaction fixes from t_65482df9:
- templates/base.html renders a `Brew` nav link immediately after `Reconstruct`.
- templates/brew.html keeps the commander/donor hooks brew.js drives and the
  keyboard-highlight / no-match styles.
- static/js/brew.js implements: debounced live commander search (not
  exact-text-only), focus re-search, donor no-match feedback, and
  ArrowDown/Enter/Escape keyboard handling in BOTH fields.

These read the templates/JS as text (CI has no browser); they assert specific
literals so deleting the behavior turns the suite red.

Run: python -m unittest tests.test_brew_ui
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


BASE = read("templates/base.html")
BREW_HTML = read("templates/brew.html")
BREW_DECK_HTML = read("templates/brew_deck.html")
BREW_JS = read("static/js/brew.js")


class TestBrewNavEntry(unittest.TestCase):
    def _tools_group(self):
        m = re.search(r'\{"label": "Tools", "links": \[(.*?)\]\}', BASE, re.S)
        self.assertIsNotNone(m, "Tools nav group not found in base.html")
        return m.group(1)

    def test_brew_link_present(self):
        self.assertIn('{"href": "/brew", "label": "Brew", "match": "/brew"', BASE)

    def test_brew_immediately_after_reconstruct(self):
        group = self._tools_group()
        recon = group.find('"href": "/reconstruct"')
        brew = group.find('"href": "/brew"')
        self.assertGreater(brew, recon, "Brew must come after Reconstruct")
        between = group[recon:brew]
        self.assertNotIn('"href":', between.split("},")[-1],
                         "another nav item sits between Reconstruct and Brew")

    def test_brew_uses_shared_nav_macro(self):
        # No bespoke markup: the nav_link macro renders every item in
        # nav_groups, so active-state styling is inherited by construction.
        self.assertIn("{{ nav_link(item) }}", BASE)
        self.assertIn('nav-item{% if active %} active{% endif %}', BASE)


class TestCommanderAutocomplete(unittest.TestCase):
    def test_hooks_present(self):
        for hook in ('id="brewCmdSearch"', 'id="brewCmdResults"',
                     'id="brewCmdError"', 'id="brewCmdSelected"'):
            self.assertIn(hook, BREW_HTML)

    def test_combobox_semantics(self):
        self.assertIn('role="combobox"', BREW_HTML)
        self.assertIn('aria-controls="brewCmdResults"', BREW_HTML)
        self.assertIn('role="listbox"', BREW_HTML)

    def test_debounced_live_search(self):
        # Suggestions must appear while typing, not on exact-match submit.
        self.assertIn('cmdInput.addEventListener("input"', BREW_JS)
        self.assertRegex(BREW_JS, r"debounce = setTimeout\(runSearch, \d+\)")
        self.assertIn('fetchJSON("/api/brew/commanders?q="', BREW_JS)

    def test_focus_reopens_search(self):
        self.assertIn('cmdInput.addEventListener("focus"', BREW_JS)

    def test_keyboard_support(self):
        m = re.search(r'cmdInput\.addEventListener\("keydown".*?\n\s*\}\);',
                      BREW_JS, re.S)
        self.assertIsNotNone(m, "commander keydown handler missing")
        body = m.group(0)
        self.assertIn('"ArrowDown"', body)
        self.assertIn('"Enter"', body)
        self.assertIn('"Escape"', body)

    def test_click_selects_and_updates_summary(self):
        self.assertIn('li.addEventListener("mousedown"', BREW_JS)
        self.assertIn("pickCommander(c)", BREW_JS)
        # Summary rail reflects the picked commander instead of the placeholder.
        self.assertIn('Pick a commander', BREW_JS)
        self.assertIn('$("brewKvCmd").textContent = state.commanders.map', BREW_JS)


class TestDonorAutocomplete(unittest.TestCase):
    def test_hooks_present(self):
        for hook in ('id="brewDonorFilter"', 'id="brewDonorList"', 'id="brewDonorChips"'):
            self.assertIn(hook, BREW_HTML)

    def test_no_match_feedback_not_silent(self):
        self.assertIn("No decks match", BREW_JS)
        self.assertIn("brew-donor-none", BREW_JS)
        self.assertIn(".brew-donor-list li.brew-donor-none", BREW_HTML)

    def test_keyboard_support(self):
        m = re.search(r'donorFilter\.addEventListener\("keydown".*?\n\s*\}\);',
                      BREW_JS, re.S)
        self.assertIsNotNone(m, "donor keydown handler missing")
        body = m.group(0)
        self.assertIn('"ArrowDown"', body)
        self.assertIn('"Enter"', body)
        self.assertIn('"Escape"', body)

    def test_keyboard_highlight_style_defined(self):
        self.assertIn("kb-focus", BREW_JS)
        self.assertIn(".brew-donor-list li.kb-focus", BREW_HTML)

    def test_enter_toggles_highlighted_donor(self):
        self.assertIn("toggleDonor(d.registry_id", BREW_JS)

    def test_click_adds_named_chip(self):
        self.assertIn('class="brew-donor-chips" id="brewDonorChips"', BREW_HTML)
        self.assertIn("renderDonorChips()", BREW_JS)
        self.assertIn("hud-chip-remove", BREW_JS)


class TestBrewAssetCacheBusting(unittest.TestCase):
    """brew.js must load with the shared ?v={{ version }} cache-buster.

    Regression pin for t_4f7ebd86: every other static asset in base.html is
    versioned, but brew.html loaded brew.js bare. With no Cache-Control header
    from the static mount, browsers heuristically cache the bare URL, so a
    deploy could ship a new brew.js while clients kept running the stale file
    (the live commander autocomplete never appeared). Deleting the version
    parameter turns this test red.
    """

    def test_brew_js_script_tag_is_versioned(self):
        m = re.search(r'<script src="\{\{ url_for\(\'static\', path=\'/js/brew\.js\'\) \}\}([^"]*)"></script>',
                      BREW_HTML)
        self.assertIsNotNone(m, "brew.js script tag not found in brew.html")
        self.assertIn("?v={{ version }}", m.group(1),
                      "brew.js must be loaded with ?v={{ version }} like base.html assets")

    def test_brew_deck_js_script_tag_is_versioned(self):
        # brew_deck.html loads the same brew.js; it got the url_for form but
        # missed the ?v= query param, reintroducing the stale-cache risk the
        # brew.html fix (PR #324 pattern) closed.
        m = re.search(r'<script src="\{\{ url_for\(\'static\', path=\'/js/brew\.js\'\) \}\}([^"]*)"></script>',
                      BREW_DECK_HTML)
        self.assertIsNotNone(m, "brew.js script tag not found in brew_deck.html")
        self.assertIn("?v={{ version }}", m.group(1),
                      "brew.js on the brew deck page must be loaded with ?v={{ version }} like base.html assets")


class TestMobileLayout(unittest.TestCase):
    """390px mobile layout contract (t_d684b75a, live finding on v0.4.226).

    At 390x844 the stepper's side-scroll left steps 03/04 clipped past the
    viewport edge (right edge x=578), donor metadata spans ran to x=568, and
    the mobile Generate button was 36px tall. These pin the brew.html style
    rules that keep every element inside the viewport at phone widths; the
    fix was verified by real-browser measurement (max right edge <= 390).
    """

    def _mobile_block(self):
        m = re.search(r"@media \(max-width: 900px\) \{(.*?)\n    \}\n</style>",
                      BREW_HTML, re.S)
        self.assertIsNotNone(m, "mobile media block not found in brew.html")
        return m.group(1)

    def test_stepper_wraps_instead_of_side_scrolling(self):
        mobile = self._mobile_block()
        self.assertIn(".hud-doc-index { overflow-x: visible; }", mobile)
        self.assertRegex(mobile, r"\.hud-doc-index ol \{[^}]*flex-wrap: wrap")

    def test_donor_meta_wraps_to_own_line(self):
        mobile = self._mobile_block()
        self.assertRegex(mobile, r"\.brew-donor-list li > label \{[^}]*flex-wrap: wrap")
        self.assertRegex(mobile, r"\.brew-donor-meta \{[^}]*flex-basis: 100%")
        self.assertRegex(mobile, r"\.brew-donor-meta \{[^}]*white-space: normal")

    def test_donor_text_never_overflows_any_width(self):
        # Outside the media query too: name/meta are flex items that must be
        # allowed to shrink below their min-content width and ellipsize.
        for cls in (".brew-donor-name", ".brew-donor-meta"):
            m = re.search(re.escape(cls) + r" \{([^}]*)\}", BREW_HTML)
            self.assertIsNotNone(m, cls + " rule missing")
            body = m.group(1)
            self.assertIn("min-width: 0", body)
            self.assertIn("text-overflow: ellipsis", body)

    def test_mobile_generate_meets_touch_target(self):
        mobile = self._mobile_block()
        m = re.search(r"\.brew-mobilebar \.hud-btn \{([^}]*)\}", mobile)
        self.assertIsNotNone(m, "mobilebar button rule missing")
        self.assertIn("min-height: 44px", m.group(1))


class TestSubmitPathUnchanged(unittest.TestCase):
    def test_generate_payload_shape(self):
        for key in ("commanders:", "donor_deck_ids:", "build_mode:",
                    "power_level:", "themes:", "notes:"):
            self.assertIn(key, BREW_JS)
        self.assertIn('postJSON("/api/brew/start", payload)', BREW_JS)

    def test_generate_blocked_without_commander(self):
        self.assertIn("b.disabled = !ok", BREW_JS)


if __name__ == "__main__":
    unittest.main()

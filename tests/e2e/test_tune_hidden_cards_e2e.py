"""
Playwright E2E tests for the Tune page hidden-recommendations toggle (PR #128).

Verifies the 'N better buys hidden' note on deck recommendations:
- The note is a keyboard-accessible button (Tab + Enter/Space).
- aria-expanded and aria-controls toggle correctly.
- Expanding displays the list of hidden cards (score, price, reason).
- Listed card count matches the toggle label, and truncation is indicated when capped.
- Multiple recommendation toggles operate independently.
- Collapsing hides the list again.
- Narrow / mobile viewport renders without clipping or overflow.

Prerequisites:
- App running at base_url (defaults to http://localhost:8000 or --base-url)
- A deck with recommendations producing hidden cards (e.g. Holy Hellfire)

Run with:
    pytest tests/e2e/test_tune_hidden_cards_e2e.py --base-url=http://localhost:8000
"""
import os
import re
import unittest

try:
    import pytest  # noqa: F401
    from playwright.sync_api import Page, expect  # noqa: F401
except ImportError as exc:  # pytest/playwright are E2E-only deps (not in requirements.txt)
    raise unittest.SkipTest(
        "pytest/playwright not installed; install them to run the E2E tests"
    ) from exc

DECK_PATH = os.environ.get("TUNE_E2E_PATH", "/deck/Holy%20Hellfire/tune")
TOGGLE_SEL = "[data-hidden-toggle]"


def _wait_for_tune_loaded(page: Page, base_url: str):
    page.goto(f"{base_url}{DECK_PATH}")
    page.wait_for_selector("#swaps .hud-swap", timeout=15000)


def _list_for(page: Page, toggle):
    ctrl_id = toggle.get_attribute("aria-controls")
    return page.locator(f"#{ctrl_id}")


class TestHiddenCardsToggleRendering:
    """Verifies that the hidden note renders as an accessible toggle."""

    def test_toggle_is_button_with_aria_attributes(self, page: Page, base_url: str):
        _wait_for_tune_loaded(page, base_url)
        toggles = page.locator(TOGGLE_SEL)
        if toggles.count() == 0:
            pytest.skip("No recommendations with hidden cards in this deck")

        first = toggles.first
        expect(first).to_have_js_property("tagName", "BUTTON")
        expect(first).to_have_attribute("aria-expanded", "false")
        expect(first).to_have_attribute("aria-controls", re.compile(r"^hid\d+-"))

        # Associated list starts hidden
        hidden_list = _list_for(page, first)
        expect(hidden_list).to_be_hidden()


class TestHiddenCardsToggleInteraction:
    """Verifies expand, collapse, and count matching."""

    def test_click_expands_and_collapses_list(self, page: Page, base_url: str):
        _wait_for_tune_loaded(page, base_url)
        toggles = page.locator(TOGGLE_SEL)
        if toggles.count() == 0:
            pytest.skip("No recommendations with hidden cards in this deck")

        toggle = toggles.first
        hidden_list = _list_for(page, toggle)

        # Click to expand
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "true")
        expect(hidden_list).to_be_visible()

        # Click to collapse
        toggle.click()
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(hidden_list).to_be_hidden()

    def test_keyboard_toggle_accessibility(self, page: Page, base_url: str):
        _wait_for_tune_loaded(page, base_url)
        toggles = page.locator(TOGGLE_SEL)
        if toggles.count() == 0:
            pytest.skip("No recommendations with hidden cards in this deck")

        toggle = toggles.first
        hidden_list = _list_for(page, toggle)

        # Focus the toggle directly and test Space/Enter keys
        toggle.focus()
        page.keyboard.press("Enter")
        expect(toggle).to_have_attribute("aria-expanded", "true")
        expect(hidden_list).to_be_visible()

        page.keyboard.press("Space")
        expect(toggle).to_have_attribute("aria-expanded", "false")
        expect(hidden_list).to_be_hidden()

    def test_card_count_and_reasons_in_expanded_list(self, page: Page, base_url: str):
        _wait_for_tune_loaded(page, base_url)
        toggles = page.locator(TOGGLE_SEL)
        if toggles.count() == 0:
            pytest.skip("No recommendations with hidden cards in this deck")

        toggle = toggles.first
        label = toggle.inner_text()
        m = re.search(r"(\d+)\s+better\s+buy", label, re.IGNORECASE)
        assert m, f"Label does not contain count: {label}"
        total_hidden = int(m.group(1))

        toggle.click()
        hidden_list = _list_for(page, toggle)
        items = hidden_list.locator(":scope > li:not(.tn-hidden-more)")
        listed_count = items.count()

        if total_hidden > listed_count:
            # Truncated view
            more = hidden_list.locator(".tn-hidden-more")
            expect(more).to_be_visible()
            expect(more).to_contain_text(f"Showing the top {listed_count} of {total_hidden}")
        else:
            assert listed_count == total_hidden

        # Check reason is displayed
        reasons = items.locator(".tn-hidden-why")
        if items.count() > 0:
            expect(reasons.first).to_contain_text("over hard cap")

    def test_multiple_toggles_independent(self, page: Page, base_url: str):
        _wait_for_tune_loaded(page, base_url)
        toggles = page.locator(TOGGLE_SEL)
        if toggles.count() < 2:
            pytest.skip("Need at least 2 hidden-card recommendations to test independence")

        first = toggles.nth(0)
        second = toggles.nth(1)

        first.click()
        expect(first).to_have_attribute("aria-expanded", "true")
        expect(second).to_have_attribute("aria-expanded", "false")

        second.click()
        expect(first).to_have_attribute("aria-expanded", "true")
        expect(second).to_have_attribute("aria-expanded", "true")

        first.click()
        expect(first).to_have_attribute("aria-expanded", "false")
        expect(second).to_have_attribute("aria-expanded", "true")


class TestHiddenCardsResponsive:
    """Verifies layout on narrow mobile viewport."""

    def test_mobile_layout_no_overflow(self, page: Page, base_url: str):
        page.set_viewport_size({"width": 375, "height": 812})
        _wait_for_tune_loaded(page, base_url)
        toggles = page.locator(TOGGLE_SEL)
        if toggles.count() == 0:
            pytest.skip("No recommendations with hidden cards in this deck")

        toggle = toggles.first
        toggle.click()

        scroll_w, client_w = page.evaluate(
            "[document.documentElement.scrollWidth, document.documentElement.clientWidth]"
        )
        assert scroll_w <= client_w, f"Page overflow: {scroll_w} > {client_w}"


class TestZeroHiddenCards:
    """Verifies clean rendering when 0 cards are hidden."""

    def test_no_broken_elements_when_zero_hidden(self, page: Page, base_url: str):
        _wait_for_tune_loaded(page, base_url)
        toggles = page.locator(TOGGLE_SEL)
        if toggles.count() > 0:
            pytest.skip("Deck has hidden cards; skipping zero-hidden check")

        # Expect no toggle buttons and no hidden card lists rendered
        expect(page.locator(TOGGLE_SEL)).to_have_count(0)
        expect(page.locator(".tn-hidden-list")).to_have_count(0)

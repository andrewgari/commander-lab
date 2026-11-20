"""/brew commander picker, real-input regression (t_0fb7eb17).

(a) typing 'spid' key by key shows a visibly rendered dropdown;
(b) a real pointer click on a suggestion enables Generate and clears the hint;
(c) ArrowDown + Enter does the same;
(d) the page loads brew.js with a cache-bust token derived from the file's
    content (a frozen app-version token let browsers keep a stale brew.js
    across deploys).

Reuses the in-process app + fake Scryfall from the shared-autocomplete suite.
Run: python -m pytest tests/e2e/test_brew_autocomplete_e2e.py -v
"""
import hashlib
import os
import re

import pytest

# Optional E2E deps: skip the module (don't break collection) when absent.
pytest.importorskip("playwright.sync_api")
pytest.importorskip("fakeredis")
pytest.importorskip("uvicorn")

from playwright.sync_api import expect  # noqa: E402
from tests.e2e.test_card_autocomplete_inputs_e2e import live_url, base_url  # noqa: F401

pytestmark = pytest.mark.e2e
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _open(page, base_url):
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{base_url}/brew", wait_until="domcontentloaded")
    page.wait_for_function("window.CardAutocomplete !== undefined")
    page.wait_for_timeout(300)
    inp = page.locator("#brewCmdSearch")
    inp.click()
    inp.press_sequentially("spid", delay=40)
    lst = page.locator("#brewCmdResults")
    expect(lst.locator("[role=option]").first).to_be_visible(timeout=8000)
    return inp, lst


def test_typing_shows_visible_dropdown(page, base_url):
    inp, lst = _open(page, base_url)
    assert lst.locator("[role=option]").count() >= 1
    box = lst.bounding_box()
    assert box and box["width"] > 0 and box["height"] > 0, box


def test_pointer_click_selects_and_enables_generate(page, base_url):
    inp, lst = _open(page, base_url)
    expect(page.locator("#brewGenerate")).to_be_disabled()
    expect(page.locator("#brewGenerateHint")).not_to_have_text("")
    lst.locator("[role=option]").first.click()
    expect(page.locator("#brewGenerate")).to_be_enabled()
    expect(page.locator("#brewGenerateHint")).to_have_text("")


def test_keyboard_selects_and_enables_generate(page, base_url):
    inp, lst = _open(page, base_url)
    inp.press("ArrowDown")
    inp.press("Enter")
    expect(page.locator("#brewGenerate")).to_be_enabled()
    expect(page.locator("#brewGenerateHint")).to_have_text("")


def test_immediate_enter_selects_kaalia_and_enables_generate(page, base_url):
    page.route("**/api/cards/autocomplete?**", lambda route: route.fulfill(json={
        "results": [{"name": "Kaalia of the Vast", "color_identity": ["W", "B", "R"]}],
        "source": "scryfall", "degraded": False,
    }))
    page.goto(f"{base_url}/brew", wait_until="domcontentloaded")
    page.wait_for_function("window.CardAutocomplete !== undefined")
    inp = page.locator("#brewCmdSearch")
    expect(inp).to_have_attribute("aria-controls", "brewCmdResults")
    inp.fill("Kaalia of the Vast")
    inp.press("Enter")
    expect(page.locator("#brewCmdNames")).to_have_text("Kaalia of the Vast")
    expect(page.locator("#brewGenerate")).to_be_enabled()
    expect(page.locator("#brewGenerateMobile")).to_be_enabled()


def test_pending_enter_shows_degraded_notice_before_confirmation(page, base_url):
    page.route("**/api/cards/autocomplete?**", lambda route: route.fulfill(json={
        "results": [{"name": "Kaalia of the Vast", "color_identity": ["W", "B", "R"]}],
        "source": "local", "degraded": True,
    }))
    page.goto(f"{base_url}/brew", wait_until="domcontentloaded")
    inp = page.locator("#brewCmdSearch")
    expect(inp).to_have_attribute("aria-controls", "brewCmdResults")
    inp.fill("Kaalia of the Vast")
    inp.evaluate("el => { window.enterRepeats = []; el.addEventListener('keydown', e => { if (e.key === 'Enter') window.enterRepeats.push(e.repeat); }); }")
    page.keyboard.down("Enter")
    notice = page.locator("#brewCmdResults .hud-autocomplete-state--degraded")
    expect(notice).to_be_visible()
    expect(page.locator("#brewGenerate")).to_be_disabled()
    page.keyboard.down("Enter")  # held-key auto-repeat, not a fresh confirmation
    assert page.evaluate("window.enterRepeats") == [False, True]
    expect(notice).to_be_visible()
    expect(page.locator("#brewGenerate")).to_be_disabled()
    page.keyboard.up("Enter")
    inp.press("ArrowDown")
    expect(notice).to_be_visible()
    inp.press("Enter")
    expect(page.locator("#brewCmdNames")).to_have_text("Kaalia of the Vast")
    expect(page.locator("#brewGenerate")).to_be_enabled()


def test_brew_js_is_loaded_with_content_hash(page, base_url):
    urls = []
    page.on("request", lambda r: urls.append(r.url) if r.url.split("?")[0].endswith("/static/js/brew.js") else None)
    page.goto(f"{base_url}/brew", wait_until="domcontentloaded")
    page.wait_for_function("window.CardAutocomplete !== undefined")
    with open(os.path.join(ROOT, "src/web/static/js/brew.js"), "rb") as f:
        want = hashlib.sha256(f.read()).hexdigest()[:12]
    assert urls, "brew.js was not requested"
    assert re.search(rf"[?&]v={want}$", urls[0]), (urls[0], want)

"""QA round 3 (t_3de61ab7): browser checks for the shared CardAutocomplete on
brew, reconstruct, deck-manage and journal.

Covers: arrows+Enter select, Enter with NO arrows commits the top result
(brew Generate must enable), error row distinct from empty row, outside-click
dismiss, blur does not leave the dropdown stuck open, and stale-response race
(an older slow response must never overwrite a newer query's results).

Reuses the live_url/base_url fixtures and fake Scryfall from
test_card_autocomplete_inputs_e2e.py.

Run: python -m pytest tests/e2e/test_card_autocomplete_qa3_e2e.py -v
"""
import pytest

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover
    pytest.skip("playwright not installed", allow_module_level=True)

from tests.e2e.test_card_autocomplete_inputs_e2e import (  # noqa: F401  (fixtures)
    live_url,
    base_url,
    DECK_PATH,
)

pytestmark = pytest.mark.e2e

RECON_PREP = ("document.getElementById('screenPreferences').style.display='block';"
              "document.getElementById('partnerContainer_0').style.display='block'")
PAGES = {
    # name: (path, selector, prep_js, list_id_expected)
    "brew": ("/brew", "#brewCmdSearch", None, "brewCmdResults"),
    "reconstruct": ("/reconstruct", "#pref_cmdr_0",
                    "document.getElementById('screenPreferences').style.display='block'", None),
    "deck-manage": (f"{DECK_PATH}/manage", "#addCardInput", None, None),
    "journal": (f"{DECK_PATH}/journal", "#petInput", None, None),
}
PAGE_IDS = list(PAGES)


def _open(page, base_url, name):
    path, selector, prep, _ = PAGES[name]
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_viewport_size({"width": 1440, "height": 900})
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    page.wait_for_function("window.CardAutocomplete !== undefined")
    if prep:
        page.evaluate(prep)
    page.wait_for_timeout(300)
    inp = page.locator(selector).first
    expect(inp).to_have_attribute("role", "combobox")
    lst = page.locator(f"#{inp.get_attribute('aria-controls')}")
    return inp, lst, errors


@pytest.mark.parametrize("name", PAGE_IDS)
def test_arrows_then_enter_selects(page, base_url, name):
    inp, lst, errors = _open(page, base_url, name)
    inp.click()
    inp.press_sequentially("atraxa", delay=20)
    opts = lst.locator("[role=option]")
    expect(opts.first).to_be_visible(timeout=8000)
    assert opts.count() >= 2, lst.inner_text()
    inp.press("ArrowDown")
    inp.press("ArrowDown")
    expect(opts.nth(1)).to_have_attribute("aria-selected", "true")
    second = opts.nth(1).locator(".hud-autocomplete-name").inner_text()
    inp.press("Enter")
    expect(lst).to_be_hidden()
    assert not errors, errors
    if name == "reconstruct":
        assert inp.input_value() == second, (inp.input_value(), second)


@pytest.mark.parametrize("name", PAGE_IDS)
def test_enter_without_arrows_commits_top(page, base_url, name):
    inp, lst, errors = _open(page, base_url, name)
    inp.click()
    inp.press_sequentially("atraxa", delay=20)
    opts = lst.locator("[role=option]")
    expect(opts.first).to_be_visible(timeout=8000)
    top = opts.first.locator(".hud-autocomplete-name").inner_text()
    inp.press("Enter")
    expect(lst).to_be_hidden()
    assert not errors, errors
    if name == "brew":
        # The reported bug: Generate must enable with no arrow key pressed.
        expect(page.locator("#brewGenerate")).to_be_enabled()
        assert top in page.locator("#brewCmdNames").inner_text()
    if name == "reconstruct":
        assert inp.input_value() == top, (inp.input_value(), top)


@pytest.mark.parametrize("name", PAGE_IDS)
def test_error_state_distinct_from_no_matches(page, base_url, name):
    inp, lst, errors = _open(page, base_url, name)
    page.route("**/api/cards/autocomplete*", lambda route: route.abort())
    inp.click()
    inp.press_sequentially("atraxa", delay=20)
    err = lst.locator(".hud-autocomplete-state--error")
    expect(err).to_be_visible(timeout=8000)
    assert lst.locator(".hud-autocomplete-state--empty").count() == 0
    page.unroute("**/api/cards/autocomplete*")
    inp.fill("")
    inp.press_sequentially("zzqxjv", delay=20)
    expect(lst.locator(".hud-autocomplete-state--empty")).to_be_visible(timeout=8000)
    assert lst.locator(".hud-autocomplete-state--error").count() == 0
    assert not errors, errors


@pytest.mark.parametrize("name", PAGE_IDS)
def test_outside_click_dismisses(page, base_url, name):
    inp, lst, errors = _open(page, base_url, name)
    inp.click()
    inp.press_sequentially("atraxa", delay=20)
    expect(lst).to_be_visible(timeout=8000)
    page.locator("h1, h2, h3").first.click()
    expect(lst).to_be_hidden()
    assert inp.get_attribute("aria-expanded") == "false"


@pytest.mark.parametrize("name", PAGE_IDS)
def test_tab_blur_does_not_leave_dropdown_open(page, base_url, name):
    """Keyboard blur (Tab) fires no mousedown, so the component's document
    mousedown handler never runs. The dropdown must still not stay open."""
    inp, lst, errors = _open(page, base_url, name)
    inp.click()
    inp.press_sequentially("atraxa", delay=20)
    expect(lst).to_be_visible(timeout=8000)
    inp.press("Tab")
    page.wait_for_timeout(400)
    expect(lst).to_be_hidden()


@pytest.mark.parametrize("name", PAGE_IDS)
def test_stale_response_does_not_overwrite_newer(page, base_url, name):
    """Query A ('spider') response is held; query B ('biscuit') is answered
    first. When A's stale response is finally released it must NOT replace B."""
    inp, lst, errors = _open(page, base_url, name)
    held = []

    def hold_first_spider(route):
        if "q=spider" in route.request.url and not held:
            held.append(route)
        else:
            route.continue_()

    page.route("**/api/cards/autocomplete*", hold_first_spider)
    inp.click()
    inp.press_sequentially("spider", delay=10)
    page.wait_for_timeout(500)
    assert held, "spider request was never issued"
    inp.fill("")
    inp.press_sequentially("biscuit", delay=10)
    expect(lst.locator("[role=option]").first).to_contain_text("Biscuit", timeout=8000)
    # Now let the stale spider response land.
    held[0].continue_()
    page.wait_for_timeout(800)
    texts = lst.inner_text()
    assert "Biscuit" in texts, texts
    assert "Spider-Man" not in texts and "Ancient Spider" not in texts, texts
    page.unroute("**/api/cards/autocomplete*")
    assert not errors, errors


@pytest.mark.parametrize("name", [n for n in PAGE_IDS if n != "journal"])
def test_fast_type_delete_settles_closed_on_empty(page, base_url, name):
    """Rapid type then delete to below minChars must close the list and not
    re-open it when a pending debounce or in-flight request resolves.
    Journal is excluded: it attaches with minChars=0 by design (empty query
    browses the pet pool), so an empty input is not a 'closed' state there."""
    inp, lst, errors = _open(page, base_url, name)
    inp.click()
    inp.press_sequentially("atraxa", delay=5)
    for _ in range(6):
        inp.press("Backspace")
    page.wait_for_timeout(900)
    expect(lst).to_be_hidden()
    assert inp.get_attribute("aria-expanded") == "false"
    assert not errors, errors

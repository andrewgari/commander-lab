"""One parametrized browser test for EVERY card-name input on the shared
CardAutocomplete component (t_0c9c5f55). Typing a query must show a visible
dropdown whose top row is the expected card, for exact names AND misspellings
("spidr", "buscuit", "atraxa"), and the row must be selectable.

The app runs in-process on a free port with fakeredis; Scryfall (server side)
is replaced by a deterministic fake, so nothing leaves the machine.

Run: python -m pytest tests/e2e/test_card_autocomplete_inputs_e2e.py -v
"""
import json
import os
import socket
import sys
import threading
import time
import unittest
from unittest.mock import Mock, patch

try:
    import pytest
    from playwright.sync_api import expect
    import fakeredis
    import uvicorn
except ImportError as exc:  # E2E-only deps
    raise unittest.SkipTest("pytest/playwright/fakeredis not installed") from exc

os.environ.setdefault("INTERACTIONS_DISABLED", "1")
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import src.server.app.main as app_module  # noqa: E402
import src.server.domain.inventory.card_search as card_search  # noqa: E402

pytestmark = pytest.mark.e2e

DECK = {"id": 9100001, "name": "AC Test Deck", "color": "G", "commanders": ["Atraxa, Grand Unifier"],
        "status": "physical", "registry_id": "archidekt:9100001", "source": "archidekt",
        "source_id": "9100001", "cards": [{"name": "Atraxa, Grand Unifier", "quantity": 1}]}

# name -> (is_legendary). The fake Scryfall below serves these.
CARDS = {
    "Spider-Man, Web Slinger": True, "Ancient Spider": False, "Spider-Bot": False,
    "Biscuit, Tavern Cook": True, "Biscuit Brigade": False,
    "Atraxa, Grand Unifier": True, "Atraxa, Praetors' Voice": True, "Atraxa's Fall": False,
}
# query -> expected top row (typo queries resolve through the catalog fallback)
QUERIES = [("spider", "Spider"), ("spidr", "Spider"), ("buscuit", "Biscuit"), ("atraxa", "Atraxa")]


def _card_obj(name):
    legendary = CARDS[name]
    return {"id": name.lower().replace(" ", "-").replace(",", ""), "name": name,
            "type_line": "Legendary Creature — Test" if legendary else "Creature — Test",
            "legalities": {"commander": "legal"}, "colors": [], "color_identity": [],
            "keywords": [], "mana_cost": "", "image_uris": {}}


def _fake_get(url, params=None, **kwargs):
    q = ((params or {}).get("q") or "")
    if url.endswith("/cards/autocomplete"):
        ql = q.casefold()
        data = [n for n in CARDS if n.casefold().startswith(ql) or ql in n.casefold().split(" ")[0]]
        return Mock(status_code=200, json=lambda: {"data": sorted(data)})
    if url.endswith("/catalog/card-names"):
        return Mock(status_code=200, json=lambda: {"data": list(CARDS)})
    if url.endswith("/cards/search"):
        if "is:commander" in q and '!"' in q:  # commander scope, exact-name rescue
            hits = [n for n, leg in CARDS.items() if leg and f'!"{n}"' in q]
        elif "is:commander" in q:  # commander scope
            term = q.split(" is:commander")[0].casefold().replace("name:", "").strip('" ')
            hits = [n for n, leg in CARDS.items() if leg and term in n.casefold()]
        else:  # enrichment: !"Name" or !"Name"
            hits = [n for n in CARDS if f'!"{n}"' in q]
        if not hits:
            return Mock(status_code=404, json=lambda: {})
        return Mock(status_code=200, json=lambda: {"data": [_card_obj(n) for n in hits], "has_more": False})
    return Mock(status_code=404, json=lambda: {})


@pytest.fixture(scope="session")
def live_url():
    r = fakeredis.FakeStrictRedis(decode_responses=True)
    r.set("decks", json.dumps([DECK]))
    for n in CARDS:
        r.set(f"card_meta:{n}", json.dumps({"type": "Creature", "color": "G", "cmc": 2}))
    prev = app_module.r
    app_module.r = r
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app_module.app, host="127.0.0.1", port=port,
                                           log_level="warning", lifespan="off"))
    t = threading.Thread(target=server.run, daemon=True)
    with patch.object(card_search.httpx, "get", side_effect=_fake_get):
        t.start()
        deadline = time.time() + 15
        while not server.started:
            assert time.time() < deadline and t.is_alive(), "uvicorn did not start"
            time.sleep(0.05)
        yield f"http://127.0.0.1:{port}"
        server.should_exit = True
        t.join(timeout=10)
    app_module.r = prev


@pytest.fixture(scope="session")
def base_url(live_url):
    return live_url


DECK_PATH = "/deck/AC%20Test%20Deck"
# (id, path, selector, prepare-js, commander_scope)
INPUTS = [
    ("brew-commander", "/brew", "#brewCmdSearch", None, True),
    ("reconstruct-commander", "/reconstruct", "#pref_cmdr_0",
     "document.getElementById('screenPreferences').style.display='block'", True),
    ("reconstruct-partner", "/reconstruct", "#pref_partner_cmdr_0",
     "document.getElementById('screenPreferences').style.display='block';"
     "document.getElementById('partnerContainer_0').style.display='block'", True),
    ("deck-manage-add-card", f"{DECK_PATH}/manage", "#addCardInput", None, False),
    ("journal-pet-card", f"{DECK_PATH}/journal", "#petInput", None, False),
    ("staple-themes-card", "/staple-themes", "#stCard", None, False),
    ("tune-overlay-add-card", f"{DECK_PATH}/tune?view=overlay", ".tnov-add-input", None, False),
]


@pytest.mark.parametrize("query,expect_top", QUERIES, ids=[q for q, _ in QUERIES])
@pytest.mark.parametrize("name,path,selector,prep,cmdr", INPUTS, ids=[i[0] for i in INPUTS])
def test_card_name_input_uses_shared_autocomplete(page, base_url, name, path, selector, prep, cmdr, query, expect_top):
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{base_url}{path}", wait_until="domcontentloaded")
    page.wait_for_function("window.CardAutocomplete !== undefined")
    if prep:
        page.evaluate(prep)
    page.wait_for_timeout(300)
    inp = page.locator(selector).first
    # The input is wired to the shared component (it sets role=combobox + aria-controls).
    expect(inp).to_have_attribute("role", "combobox")
    list_id = inp.get_attribute("aria-controls")
    assert list_id, f"{name}: not attached to CardAutocomplete"
    lst = page.locator(f"#{list_id}")
    assert "hud-autocomplete" in (lst.get_attribute("class") or ""), name
    inp.click()
    inp.press_sequentially(query, delay=20)
    expect(lst).to_be_visible(timeout=8000)
    first = lst.locator("[role=option]").first
    expect(first).to_be_visible(timeout=8000)
    assert expect_top in first.inner_text(), (name, query, lst.inner_text())
    # Selectable by real click.
    first.click()
    page.wait_for_timeout(300)
    assert not errors, errors
    if name == "brew-commander":
        expect(page.locator("#brewGenerate")).to_be_enabled()
        assert expect_top in page.locator("#brewCmdNames").inner_text()


@pytest.mark.parametrize("search_phase", ["debounce", "loading"])
def test_tune_free_text_enter_bubbles_during_pending_search_and_ignores_ime(page, search_phase):
    # Mount the real legacy scripts and Tune event delegation without a running
    # server. The controlled fetch keeps the in-flight case deterministic.
    page.set_content("""
      <form id="host">
      <input id="view-recs" name="tuneView" value="recs" type="radio">
      <input id="view-overlay" name="tuneView" value="overlay" type="radio">
      <div id="tuneRecsView"></div><div id="tuneOverlayView"></div>
      <div id="tuneOvColumns"></div><div id="tuneOvState"></div>
      <div id="tuneOvDrift"></div><div id="tuneOvError"></div><div id="tuneOvCount"></div>
      <button id="tuneOvRebase"></button><button id="tuneOvSaveDraft"></button>
      <button id="tuneOvAddCol"></button><input id="tuneOvNewTag">
      </form>
    """)
    page.evaluate("""() => {
        window.submissions = 0;
        document.getElementById('host').addEventListener('submit', e => { e.preventDefault(); window.submissions++; });
        window.HUD = {esc: s => String(s), cardName: s => String(s)};
        history.replaceState = () => {};
        const state = {cards: [], columns: [{id: 'uncategorized', label: 'Uncategorized'}],
                       card_tags: {}, tag_flags: {}, draft: null};
        window.searchCalls = 0;
        window.fetch = (url, options = {}) => {
            if (url.startsWith('/api/cards/autocomplete')) {
                window.searchCalls++;
                window.searchResponse = new Promise(resolve => { window.finishSearch = resolve; });
                return window.searchResponse;
            }
            if (options.method === 'POST') state.cards = JSON.parse(options.body).cards;
            return Promise.resolve(new Response(JSON.stringify(state), {status: 200}));
        };
    }""")
    page.add_script_tag(path=os.path.join(ROOT, "src/web/static/js/card_autocomplete.js"))
    page.add_script_tag(path=os.path.join(ROOT, "src/web/static/js/tune_overlay.js"))
    page.evaluate("""() => {
        delete window.TuneOverlay.adds;
        window.TuneOverlay.init({deckId: 'test'});
        document.getElementById('view-overlay').dispatchEvent(new Event('change'));
    }""")
    inp = page.locator(".tnov-add-input").first
    expect(inp).to_have_attribute("role", "combobox")
    if search_phase == "debounce":
        # Dispatch both keys in the same task, before the debounce can fire.
        events = inp.evaluate("""el => {
            el.value = 'Custom Free Text';
            el.dispatchEvent(new Event('input', {bubbles: true}));
            const ime = el.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'Enter', bubbles: true, cancelable: true, isComposing: true
            }));
            const afterIme = el.value;
            const enter = el.dispatchEvent(new KeyboardEvent('keydown', {
                key: 'Enter', bubbles: true, cancelable: true
            }));
            return {ime, afterIme, enter};
        }""")
        assert events == {"ime": True, "afterIme": "Custom Free Text", "enter": False}
    else:
        inp.fill("Custom Free Text")
        inp.evaluate("""el => el.dispatchEvent(new KeyboardEvent('keydown', {
            key: 'Enter', bubbles: true, cancelable: true, isComposing: true
        }))""")
        expect(inp).to_have_value("Custom Free Text")
        assert page.locator('[data-card="Custom Free Text"]').count() == 0
        page.wait_for_function("window.searchCalls === 1")
        inp.press("Enter")
    expect(page.locator('[data-card="Custom Free Text"]')).to_have_count(1)
    assert page.evaluate("window.submissions") == 0
    if search_phase == "loading":
        page.evaluate("""async () => {
            window.finishSearch(new Response(JSON.stringify({results: [{name: 'Wrong Card'}], degraded: false})));
            await window.searchResponse;
            await new Promise(resolve => setTimeout(resolve, 0));
        }""")
    else:
        assert page.evaluate("window.searchCalls") == 0
    assert page.locator('[data-card="Wrong Card"]').count() == 0


@pytest.mark.parametrize("search_phase", ["debounce", "loading"])
@pytest.mark.parametrize("external_owner", [False, True])
def test_pending_shared_search_does_not_implicitly_submit_form(page, search_phase, external_owner):
    # Exercise native implicit submission, including inputs associated via form=.
    if external_owner:
        page.set_content('<form id="host"><button type="submit">Submit</button></form>'
                         '<input id="card" form="host">')
    else:
        page.set_content('<form id="host"><input id="card"><button type="submit">Submit</button></form>')
    page.evaluate("""() => {
        window.submissions = 0; window.selected = [];
        document.getElementById('host').addEventListener('submit', e => { e.preventDefault(); window.submissions++; });
        window.fetch = () => new Promise(resolve => { window.finishSearch = resolve; });
    }""")
    page.add_script_tag(path=os.path.join(ROOT, "src/web/static/js/card_autocomplete.js"))
    page.evaluate("""phase => {
        window.CardAutocomplete.attach(document.getElementById('card'), {
            debounceMs: phase === 'debounce' ? 10000 : 0,
            onSelect: card => window.selected.push(card.name)
        });
    }""", search_phase)
    inp = page.locator("#card")
    inp.fill("Kaalia")
    if search_phase == "loading":
        page.wait_for_function("typeof window.finishSearch === 'function'")
    inp.press("Enter")
    assert page.evaluate("window.submissions") == 0
    assert page.evaluate("window.selected") == []
    if search_phase == "loading":
        page.evaluate("""() => window.finishSearch(new Response(JSON.stringify({results: [{name: 'Kaalia of the Vast'}]})))""")
        expect(page.locator("[role=option]")).to_have_count(1)
        inp.press("Enter")
        assert page.evaluate("window.selected") == ["Kaalia of the Vast"]
        assert page.evaluate("window.submissions") == 0
    else:
        inp.press("Escape")
        inp.press("Enter")
        assert page.evaluate("window.submissions") == 1


# ---------------------------------------------------------------------------
# Brew commander picker UX contract (t_d30383bb): one case per behavior.
# ---------------------------------------------------------------------------
def _open_brew(page, base_url, width=1440, height=900):
    page.set_viewport_size({"width": width, "height": height})
    page.goto(f"{base_url}/brew", wait_until="domcontentloaded")
    page.wait_for_function("window.CardAutocomplete !== undefined")
    page.wait_for_timeout(300)
    inp = page.locator("#brewCmdSearch")
    expect(inp).to_have_attribute("aria-controls", "brewCmdResults")
    return inp, page.locator("#brewCmdResults")


def test_brew_keyboard_arrows_and_enter_select(page, base_url):
    inp, lst = _open_brew(page, base_url)
    inp.click()
    inp.press_sequentially("atraxa", delay=20)
    opts = lst.locator("[role=option]")
    expect(opts.first).to_be_visible(timeout=8000)
    assert opts.count() >= 2
    inp.press("ArrowDown")
    inp.press("ArrowDown")
    active = inp.get_attribute("aria-activedescendant")
    assert active and page.locator(f"#{active}").get_attribute("aria-selected") == "true"
    expect(opts.nth(1)).to_have_attribute("aria-selected", "true")
    second = opts.nth(1).locator(".hud-autocomplete-name").inner_text()
    inp.press("ArrowUp")
    expect(opts.nth(0)).to_have_attribute("aria-selected", "true")
    inp.press("ArrowDown")
    inp.press("Enter")
    assert second in page.locator("#brewCmdNames").inner_text()
    expect(lst).to_be_hidden()
    expect(page.locator("#brewGenerate")).to_be_enabled()


def test_brew_escape_dismisses_even_during_debounce(page, base_url):
    inp, lst = _open_brew(page, base_url)
    inp.click()
    inp.press_sequentially("atraxa", delay=20)
    expect(lst.locator("[role=option]").first).to_be_visible(timeout=8000)
    inp.press("Escape")
    expect(lst).to_be_hidden()
    assert inp.get_attribute("aria-expanded") == "false"
    # Esc pressed inside the debounce window must not be undone by the pending search.
    inp.press("Backspace")
    inp.press("Escape")
    page.wait_for_timeout(900)
    expect(lst).to_be_hidden()


def test_brew_outside_click_dismisses(page, base_url):
    inp, lst = _open_brew(page, base_url)
    inp.click()
    inp.press_sequentially("atraxa", delay=20)
    expect(lst).to_be_visible(timeout=8000)
    page.locator(".brew-summary .hud-panel-head h3").click()
    expect(lst).to_be_hidden()
    assert inp.get_attribute("aria-expanded") == "false"


def test_brew_loading_empty_and_failure_states(page, base_url):
    inp, lst = _open_brew(page, base_url)
    # loading: hold the request until we have seen the loading row
    held = []
    page.route("**/api/cards/autocomplete*", lambda route: held.append(route))
    inp.click()
    inp.press_sequentially("atraxa", delay=20)
    expect(lst.locator(".hud-autocomplete-state--loading")).to_have_text("Searching commanders…", timeout=5000)
    while not held:
        page.wait_for_timeout(50)
    held[0].continue_()
    page.unroute("**/api/cards/autocomplete*")
    expect(lst.locator("[role=option]").first).to_be_visible(timeout=8000)
    # empty: gibberish
    inp.fill("")
    inp.press_sequentially("zzqxjv", delay=20)
    expect(lst.locator(".hud-autocomplete-state--empty")).to_have_text(
        "No commanders match — check spelling", timeout=8000)
    # failure: distinguishable from empty
    page.route("**/api/cards/autocomplete*", lambda route: route.abort())
    inp.fill("")
    inp.press_sequentially("atraxa", delay=20)
    expect(lst.locator(".hud-autocomplete-state--error")).to_have_text(
        "Commander search failed. Try again.", timeout=8000)
    assert lst.locator(".hud-autocomplete-state--empty").count() == 0


def test_brew_generate_disabled_shows_reason_until_commander_picked(page, base_url):
    inp, lst = _open_brew(page, base_url)
    expect(page.locator("#brewGenerate")).to_be_disabled()
    expect(page.locator("#brewGenerateHint")).to_have_text("Pick a commander to brew")
    expect(page.locator("#brewGenerateHint")).to_be_visible()
    inp.click()
    inp.press_sequentially("atraxa", delay=20)
    expect(lst.locator("[role=option]").first).to_be_visible(timeout=8000)
    inp.press("Enter")
    expect(page.locator("#brewGenerate")).to_be_enabled()
    expect(page.locator("#brewGenerateHint")).to_have_text("")


def test_brew_mobile_390_no_overflow_and_reason_visible(page, base_url):
    inp, lst = _open_brew(page, base_url, width=390, height=844)
    expect(page.locator("#brewGenerateMobile")).to_be_visible()
    expect(page.locator("#brewGenerateMobile")).to_be_disabled()
    expect(page.locator("#brewMobileName")).to_have_text("Pick a commander to brew")
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    inp.click()
    inp.press_sequentially("atraxa", delay=20)
    expect(lst.locator("[role=option]").first).to_be_visible(timeout=8000)
    box = lst.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390 + 0.5, box
    assert page.evaluate("document.documentElement.scrollWidth") <= 390
    inp.press("ArrowDown")
    inp.press("Enter")
    expect(page.locator("#brewGenerateMobile")).to_be_enabled()
    assert page.evaluate("document.documentElement.scrollWidth") <= 390


def _local_rows(names):
    return {"results": [{"name": n, "uid": "", "oracle_id": "", "image": "",
                         "type_line": "Legendary Creature", "color_identity": [],
                         "colors": [], "keywords": [], "mana_cost": ""} for n in names],
            "source": "local", "degraded": True}


def test_brew_local_degraded_rows_are_all_shown_scroll_and_selectable(page, base_url):
    """Scryfall rate-limited: local rows still render (more than 8), the list
    scrolls instead of growing the page, and both click and ArrowDown+Enter pick."""
    names = [f"Spider Fixture {i:02d}" for i in range(15)]
    page.route("**/api/cards/autocomplete*", lambda route: route.fulfill(
        status=200, content_type="application/json", body=json.dumps(_local_rows(names))))
    inp, lst = _open_brew(page, base_url)
    inp.click()
    inp.press_sequentially("spider", delay=20)
    options = lst.locator("[role=option]")
    expect(options.first).to_be_visible(timeout=8000)
    assert options.count() == 15
    assert page.evaluate("el => el.scrollHeight > el.clientHeight", lst.element_handle())
    assert not page.evaluate("document.documentElement.scrollWidth > document.documentElement.clientWidth")
    options.nth(3).click()
    expect(page.locator("#brewGenerate")).to_be_enabled()
    assert "Spider Fixture 03" in page.locator("#brewCmdNames").inner_text()
    page.locator("#brewCmdRemove").click()
    inp.click()
    inp.press_sequentially("spider", delay=20)
    expect(options.first).to_be_visible(timeout=8000)
    inp.press("ArrowDown")
    inp.press("Enter")
    expect(page.locator("#brewGenerate")).to_be_enabled()

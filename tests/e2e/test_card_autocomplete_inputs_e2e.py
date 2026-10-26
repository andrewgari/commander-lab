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

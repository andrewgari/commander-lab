"""Browser regression tests for inventory.html audit findings:
1. Inventory search / deck filter race condition (stale responses dropped).
2. Keyboard accessibility for expanding card details and removing deck filter pills.
3. Unknown/missing card.status renders 'unavailable' + reason (AGENT_RULES.md #4).
"""
import shutil
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

playwright = pytest.importorskip("playwright.sync_api")
ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "src/web"


def render_inventory():
    env = Environment(loader=FileSystemLoader(WEB / "templates"), autoescape=select_autoescape(["html"]))
    request = SimpleNamespace(
        url=SimpleNamespace(path="/inventory"),
        query_params={},
        state=SimpleNamespace(app_version="test"),
    )
    return env.get_template("inventory.html").render(
        request=request,
        version="test",
        providers=["archidekt", "moxfield"],
        status_filter=None,
        status_label=None,
        deck_groups=[],
    )


@pytest.fixture
def browser():
    with playwright.sync_playwright() as p:
        try:
            b = p.chromium.launch(headless=True, executable_path=shutil.which("chromium"))
        except Exception as exc:
            pytest.skip(f"Chromium unavailable: {exc}")
        yield b
        b.close()


def _card(name, **overrides):
    base = {
        "name": name,
        "deck": "Alpha Deck",
        "type": "Artifact",
        "super_types": [],
        "sub_types": [],
        "keywords": [],
        "oracle_text": "",
        "color": "C",
        "set": "c21",
        "set_name": "Commander 2021",
        "categories": ["Ramp"],
        "primary_tag": "Ramp",
        "uid": "0c3e2a5b-4f4f-4a53-9c3f-5d1a6c1d9e10",
        "price": 1.5,
        "is_commander": False,
        "cmc": 1,
        "status": "have",
        "is_core": False,
    }
    base.update(overrides)
    return base


def _install_static_and_decks(page, decks=None):
    decks = decks or [
        {"name": "Alpha Deck", "color": "W", "commanders": ["Heliod"], "commander_roles": []},
        {"name": "Beta Deck", "color": "U", "commanders": ["Urza"], "commander_roles": []},
    ]

    def handle_common(r):
        parsed = urlparse(r.request.url)
        path = parsed.path
        if path == "/inventory":
            r.fulfill(body=render_inventory(), content_type="text/html")
            return True
        if path.startswith("/static/"):
            f = WEB / path.lstrip("/")
            ctype = "application/javascript" if path.endswith(".js") else "text/css"
            r.fulfill(body=f.read_text() if f.is_file() else "", content_type=ctype)
            return True
        if path == "/api/decks":
            r.fulfill(json={"decks": decks})
            return True
        return False

    return handle_common


def test_inventory_search_race_condition_drops_stale_response(browser):
    page = browser.new_page()
    common = _install_static_and_decks(page)
    inventory_requests = []

    def route(r):
        if common(r):
            return
        parsed = urlparse(r.request.url)
        if parsed.path == "/api/inventory":
            qs = parse_qs(parsed.query)
            q = qs.get("query", [""])[0]
            if q == "":
                r.fulfill(json={"inventory": [_card("Initial Card")]})
            else:
                inventory_requests.append((q, r))
            return
        r.abort()

    page.route("**/*", route)
    page.goto("http://test.local/inventory")
    page.wait_for_function("document.querySelectorAll('#inventoryList tr.clickable-row').length === 1")

    def _wait_for_query(expected_q: str, timeout_ms: int = 3000) -> None:
        elapsed = 0
        while not any(q == expected_q for q, _ in inventory_requests) and elapsed < timeout_ms:
            page.wait_for_timeout(20)
            elapsed += 20

    # Fire slow older query "sol", then newer query "ring".
    page.fill("#searchInput", "sol")
    page.dispatch_event("#searchInput", "keyup")
    _wait_for_query("sol")
    page.fill("#searchInput", "ring")
    page.dispatch_event("#searchInput", "keyup")
    _wait_for_query("ring")

    # Resolve newer "ring" first, then stale "sol" (if not already aborted).
    by_q = {q: req for q, req in inventory_requests}
    assert "ring" in by_q
    by_q["ring"].fulfill(json={"inventory": [_card("Newer Ring Result")]})
    page.wait_for_function(
        "document.querySelector('#inventoryList tr.clickable-row')?.innerText.includes('Newer Ring Result')"
    )

    if "sol" in by_q:
        try:
            by_q["sol"].fulfill(json={"inventory": [_card("Stale Sol Result")]})
        except Exception:
            pass  # already aborted by AbortController

    page.wait_for_timeout(100)
    rows_text = page.locator("#inventoryList tr.clickable-row").all_inner_texts()
    assert len(rows_text) == 1
    assert "Newer Ring Result" in rows_text[0]
    assert "Stale Sol Result" not in rows_text[0]
    page.close()


def test_keyboard_accessibility_row_details_and_deck_filter_pills(browser):
    page = browser.new_page()
    common = _install_static_and_decks(page)

    def route(r):
        if common(r):
            return
        parsed = urlparse(r.request.url)
        if parsed.path == "/api/inventory":
            qs = parse_qs(parsed.query)
            decks = qs.get("deck", [])
            if "Alpha Deck" in decks:
                r.fulfill(json={"inventory": [_card("Alpha Sol Ring", deck="Alpha Deck")]})
            else:
                r.fulfill(json={"inventory": [_card("Sol Ring", deck="Alpha Deck")]})
            return
        r.abort()

    page.route("**/*", route)
    page.goto("http://test.local/inventory")
    page.wait_for_function("document.querySelectorAll('#inventoryList tr.clickable-row').length === 1")

    # 1. Expand/collapse card details with keyboard (Enter and Space).
    row = page.locator("#inventoryList tr.clickable-row").first
    assert row.get_attribute("tabindex") == "0"
    assert row.get_attribute("aria-expanded") == "false"
    row.focus()
    page.keyboard.press("Enter")
    assert row.get_attribute("aria-expanded") == "true"
    assert page.locator("#details-0").evaluate("el => el.style.display") == "table-row"

    page.keyboard.press("Space")
    assert row.get_attribute("aria-expanded") == "false"
    assert page.locator("#details-0").evaluate("el => el.style.display") == "none"

    # 2. Add a deck filter pill and remove it via keyboard.
    page.fill("#deckInput", "Alpha Deck")
    page.wait_for_selector("#deckPills .pill-close")
    close_btn = page.locator("#deckPills .pill-close").first
    assert close_btn.evaluate("el => el.tagName") == "BUTTON"
    assert "Alpha Deck" in (close_btn.get_attribute("aria-label") or "")

    close_btn.focus()
    page.keyboard.press("Enter")
    page.wait_for_function("document.querySelectorAll('#deckPills .pill').length === 0")
    page.close()


def test_unknown_or_missing_card_status_renders_unavailable_with_reason(browser):
    page = browser.new_page()
    common = _install_static_and_decks(page)
    cards = [
        _card("Known Have", status="have"),
        _card("Missing Status", status=None),
        _card("Unknown Status", status="mystery"),
    ]

    def route(r):
        if common(r):
            return
        if urlparse(r.request.url).path == "/api/inventory":
            r.fulfill(json={"inventory": cards})
            return
        r.abort()

    page.route("**/*", route)
    page.goto("http://test.local/inventory")
    page.wait_for_function("document.querySelectorAll('#inventoryList tr.clickable-row').length === 3")

    statuses = page.locator("[data-physical-status]").all_inner_texts()
    assert len(statuses) == 3
    assert statuses[0].lower() == "have"
    assert "unavailable" in statuses[1].lower()
    assert "missing" in statuses[1].lower()
    assert "have" not in statuses[1].lower()
    assert "unavailable" in statuses[2].lower()
    assert "mystery" in statuses[2].lower()
    assert "have" not in statuses[2].lower()
    page.close()

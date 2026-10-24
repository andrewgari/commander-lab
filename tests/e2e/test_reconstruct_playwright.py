"""Browser E2E for the Reconstruct flow (templates/reconstruct.html + deck.html).

Drives the real UI in Chromium against the real FastAPI app (served by uvicorn
in a background thread of this process) and a real Redis database
(RECONSTRUCT_E2E_REDIS_URL, default redis://127.0.0.1:6411/15, flushed per test).

Determinism:
  * The LLM is a ScriptedLLM (see test_reconstruct_e2e.py) installed on the
    in-process builder, so the same scripted replies produce the same drafts.
  * Scryfall (commander legality checks and autocomplete) is stubbed at the
    browser network layer. Nothing leaves the machine.

Scenarios (from t_e99b6d03):
  1. Start from a deck page with a second deck; both sources retired like a
     normal decommission.
  2. Preferences validation for 1 and 2 source decks.
  3. Draft results: source tags, $0 for pool/owned, total = sum of buy prices.
  4. Incomplete/unpriced notice and COMMANDER_MISMATCH error state.
  5. Accept creates the deck(s) and they are reachable.
  6. Abandon leaves sources retired and creates no deck.

Run:
    RECONSTRUCT_E2E_REDIS_URL=redis://127.0.0.1:6411/15 \\
        python -m pytest tests/e2e/test_reconstruct_playwright.py -v

Skipped (never run against a production DB) when Redis is unreachable.
"""
from __future__ import annotations

import json
import os
import re
import socket
import sys
import threading
import time
import unittest
from typing import Any, Dict, Iterable, List, Optional, Sequence
from urllib.parse import quote

try:
    import pytest
    from playwright.sync_api import Page, Route, expect
except ImportError as exc:  # pytest/playwright are E2E-only deps (not in requirements.txt)
    raise unittest.SkipTest(
        "pytest/playwright not installed; install them to run the E2E tests"
    ) from exc

os.environ.setdefault("INTERACTIONS_DISABLED", "1")

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# Shared world, scripted LLM, plans and price oracle live in the REST suite so
# both suites exercise the same deterministic data. Importing the module does
# not collect its tests.
from tests.e2e import test_reconstruct_e2e as base  # noqa: E402

import app as app_module  # noqa: E402
import deck_validator as dv  # noqa: E402
import reconstruct  # noqa: E402
import uvicorn  # noqa: E402
from reconstruct_builder import ReconstructBuilder  # noqa: E402

import redis as redis_lib  # noqa: E402

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not base._REDIS_OK, reason=f"Redis not reachable at {base.REDIS_URL}"),
]

UNPRICED = "Unpriced Relic"
SCRYFALL_LEGENDS = {base.COMMANDER_BLUE, base.COMMANDER_RED, base.PARTNER_A, base.PARTNER_B}
DECK_A, DECK_B = "Source Alpha", "Source Beta"
OTHER_DECK, CONTROL_DECK = base.OTHER_DECK_NAME, base.CONTROL_DECK_NAME
ART_VALUES = {"fullart": "full-art", "oldborder": "old border",
              "matchframes": "match frames", "showcase": "showcase"}
BASIC_NAMES = {"Island", "Mountain", "Wastes"}


# ---------------------------------------------------------------------------
# Fixed world: the REST catalog plus one card that has no price.
# ---------------------------------------------------------------------------

def _build_catalog() -> dv.CardCatalog:
    cat = base._build_catalog()
    cat.add(dv.CardInfo(name=UNPRICED, color_identity=frozenset(), types=("Artifact",),
                        commander_legality="legal", oracle_text=""))
    return cat


CATALOG = _build_catalog()


def _price(name: str) -> Optional[float]:
    if name == UNPRICED:
        return None
    return base._price_for(name)


def _price_lookup(names: Iterable[str]) -> Dict[str, Optional[float]]:
    return {n: _price(n) for n in names}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def redis_client():
    client = redis_lib.from_url(base.REDIS_URL, decode_responses=True)
    assert client.connection_pool.connection_kwargs.get("db") == base.E2E_DB, \
        "refusing to flush a Redis DB other than the e2e DB"
    return client


@pytest.fixture
def r(redis_client):
    redis_client.flushdb()
    yield redis_client
    redis_client.flushdb()


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="session")
def live_url(redis_client):
    """Serve the real app on a free port, backed by the e2e Redis DB."""
    previous_r = app_module.r
    app_module.r = redis_client
    port = _free_port()
    config = uvicorn.Config(app_module.app, host="127.0.0.1", port=port,
                            log_level="warning", lifespan="off")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not server.started:
        if time.time() > deadline or not thread.is_alive():
            raise RuntimeError("uvicorn did not start")
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}"
    server.should_exit = True
    thread.join(timeout=10)
    app_module.r = previous_r


@pytest.fixture(scope="session")
def base_url(live_url) -> str:
    """Override the conftest base_url: these tests own their server."""
    return live_url


@pytest.fixture(autouse=True)
def scripted(r):
    """Install a ScriptedLLM on the in-process builder. Returns install(replies).

    Deny-by-default: until a test calls install(...) the script is empty, so an
    unexpected LLM call fails loudly instead of reaching a real provider.
    """
    original = reconstruct.get_builder()

    def install(replies: Sequence[str]) -> base.ScriptedLLM:
        llm = base.ScriptedLLM(replies)
        reconstruct.set_builder(ReconstructBuilder(
            client=llm,
            catalog=CATALOG,
            price_lookup=_price_lookup,
            owned_instances=None,  # ownership is read from Redis, as in production
        ))
        return llm

    install([])
    yield install
    reconstruct.set_builder(original)


@pytest.fixture
def world(r):
    """Seed the shared world: two physical sources, one other active deck, one control."""
    return base.seed_world(r)


@pytest.fixture(autouse=True)
def _browser_network(page: Page, live_url: str):
    """Only the app is reachable; Scryfall is stubbed; everything else is blocked."""

    def handle(route: Route):
        url = route.request.url
        if url.startswith(live_url):
            return route.continue_()
        if url.startswith("https://api.scryfall.com/"):
            return route.fulfill(**_scryfall_stub(url))
        return route.abort()

    page.route("**/*", handle)
    yield


def _scryfall_stub(url: str) -> Dict[str, Any]:
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    if parsed.path.startswith("/cards/autocomplete"):
        return {"status": 200, "content_type": "application/json", "body": json.dumps({"data": []})}
    if parsed.path.startswith("/cards/named"):
        name = parse_qs(parsed.query).get("exact", [""])[0]
        if name in SCRYFALL_LEGENDS:
            card = {"name": name, "type_line": "Legendary Creature — Human", "oracle_text": ""}
            return {"status": 200, "content_type": "application/json", "body": json.dumps(card)}
        body = json.dumps({"object": "error", "status": 404, "code": "not_found"})
        return {"status": 404, "content_type": "application/json", "body": body}
    return {"status": 404, "content_type": "application/json", "body": "{}"}


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------

def _api(page: Page, base_url: str, path: str) -> Any:
    res = page.request.get(f"{base_url}{path}")
    assert res.ok, f"GET {path} -> {res.status}"
    return res.json()


def _decks_by_name(page: Page, base_url: str) -> Dict[str, dict]:
    return {d["name"]: d for d in _api(page, base_url, "/api/decks")["decks"]}


def _deck_url(base_url: str, name: str) -> str:
    return f"{base_url}/deck/{quote(name)}"


def _start_from_deck_page(page: Page, base_url: str, deck: str, second: Optional[str] = None) -> str:
    """Open the deck page, run Reconstruct from it, return the new session id."""
    page.goto(_deck_url(base_url, deck), wait_until="domcontentloaded")
    expect(page.locator("#reconstructBtn")).to_be_visible()
    page.locator("#reconstructBtn").click()
    expect(page.locator("#reconstructModal")).to_have_attribute("aria-hidden", "false")
    if second:
        page.locator("#reconstructSecondDeckSelect").select_option(
            label=f"{second} ({_commander_label(second)})")
    with page.expect_navigation(url=re.compile(r".*/reconstruct/[^/]+$")):
        page.locator("#reconstructConfirmBtn").click()
    expect(page.locator("#screenPool")).to_be_visible()
    return page.url.rsplit("/", 1)[-1]


def _commander_label(deck: str) -> str:
    return {DECK_A: base.COMMANDER_BLUE, DECK_B: base.COMMANDER_RED}[deck]


def _go_to_preferences(page: Page) -> None:
    page.locator("#btnProceedToPrefs").click()
    expect(page.locator("#screenPreferences")).to_be_visible()


def _fill_prefs(page: Page, idx: int, *, name: str = "", commander: str = "",
                partner: Optional[str] = None, themes: str = "",
                strategy: Optional[str] = None, arts: Iterable[str] = (),
                notes: str = "") -> None:
    if name:
        page.fill(f"#pref_name_{idx}", name)
    if commander:
        page.fill(f"#pref_cmdr_{idx}", commander)
    if partner is not None:
        page.locator(f"#btnTogglePartner_{idx}").click()
        page.fill(f"#pref_partner_cmdr_{idx}", partner)
    if themes:
        page.fill(f"#pref_themes_{idx}", themes)
    if strategy:
        page.select_option(f"#pref_strategy_{idx}", strategy)
    for art in arts:
        page.check(f"#pref_art_{art}_{idx}")
    if notes:
        page.fill(f"#pref_notes_{idx}", notes)


def _submit_drafts(page: Page) -> None:
    page.locator("#btnDraftWithAgent").click()


def _wait_for_results(page: Page) -> None:
    expect(page.locator("#screenResults")).to_be_visible(timeout=30000)


def _draft_cards(page: Page) -> List[Any]:
    # Each draft is a direct child <div> of the results container.
    return page.locator("#draftsListContainer > div").all()


def _rows(draft_el) -> List[Dict[str, Any]]:
    out = []
    for tr in draft_el.locator("table.recon-table tbody tr").all():
        cells = tr.locator("td")
        out.append({
            "qty": cells.nth(0).inner_text().strip(),
            "name": cells.nth(1).inner_text().strip(),
            "tag": cells.nth(2).inner_text().strip(),
            "cost": _money(cells.nth(3).inner_text()),
        })
    return out


def _money(text: str) -> float:
    m = re.search(r"\$(\d+(?:\.\d{1,2})?)", text)
    assert m, f"no money amount in {text!r}"
    return float(m.group(1))


def _header_cost(draft_el) -> float:
    # text_content(): the label is CSS-uppercased, so inner_text() would not match its source text.
    m = re.search(r"New Card Cost\s*\$(\d+\.\d{2})", draft_el.text_content(), re.IGNORECASE)
    assert m, "draft header has no New Card Cost"
    return float(m.group(1))


def _tag_kind(tag: str) -> str:
    if tag.startswith("Retired Pool"):
        return "pool"
    if tag.startswith("Owned"):
        return "owned"
    if tag.startswith("New"):
        return "buy"
    raise AssertionError(f"unknown source tag {tag!r}")


def _expected_kind(name: str) -> str:
    if name in base.OWNED:
        return "owned"
    if name in base.ALPHA or name in base.BETA or name in (base.COMMANDER_BLUE, base.COMMANDER_RED):
        return "pool"
    return "buy"


# ---------------------------------------------------------------------------
# Scenario 1: start from a deck page, retire sources
# ---------------------------------------------------------------------------

class TestStartAndRetire:
    def test_two_source_start_retires_both_like_decommission(self, page: Page, base_url: str, r, world):
        # Control: a normal decommission, performed through the same API the deck page uses.
        ctrl = page.request.post(f"{base_url}/api/decks/{world['control']}/decommission", data={})
        assert ctrl.ok, ctrl.text()

        sid = _start_from_deck_page(page, base_url, DECK_A, second=DECK_B)
        assert sid

        decks = _decks_by_name(page, base_url)
        for name in (DECK_A, DECK_B, CONTROL_DECK):
            assert decks[name]["status"] == "retired", name
        assert decks[DECK_A]["tags"] == decks[CONTROL_DECK]["tags"]
        assert base.registry.DEFAULT_DECOMMISSION_TAG not in (decks[DECK_B].get("tags") or [])

        # Same UI indicators on the deck page: status chip text and no Reconstruct/Decommission actions.
        chips = {}
        for name in (DECK_A, DECK_B, CONTROL_DECK):
            page.goto(_deck_url(base_url, name), wait_until="domcontentloaded")
            expect(page.locator("#deckStatus")).not_to_have_text("", timeout=10000)
            chips[name] = page.locator("#deckStatus").inner_text().strip()
            expect(page.locator("#reconstructBtn")).to_be_hidden()
            expect(page.locator("#decommissionBtn")).to_be_hidden()
        assert chips[DECK_A] == chips[DECK_B] == chips[CONTROL_DECK], chips
        assert chips[DECK_A].lower().startswith("retired"), chips

        # The session pool lists both retired decks.
        page.goto(f"{base_url}/reconstruct/{sid}", wait_until="domcontentloaded")
        expect(page.locator("#poolHeaderDecks")).to_contain_text(DECK_A)
        expect(page.locator("#poolHeaderDecks")).to_contain_text(DECK_B)


# ---------------------------------------------------------------------------
# Scenario 2: preferences form validation
# ---------------------------------------------------------------------------

class TestPreferencesValidation:
    def test_single_source_invalid_inputs_show_errors(self, page: Page, base_url: str, r, world):
        _start_from_deck_page(page, base_url, DECK_A)
        _go_to_preferences(page)

        # Partner set with no primary commander.
        page.locator("#btnTogglePartner_0").click()
        page.fill("#pref_partner_cmdr_0", base.PARTNER_B)
        _submit_drafts(page)
        expect(page.locator("#pref_cmdr_0")).to_have_class(re.compile(r"is-invalid"))
        expect(page.locator(".pref-field-error").first).to_contain_text(
            "Pick a primary commander before adding a partner")
        expect(page.locator("#prefsError")).to_contain_text("Please fix the highlighted fields")

        # Primary and partner the same card.
        page.fill("#pref_cmdr_0", base.COMMANDER_BLUE)
        page.fill("#pref_partner_cmdr_0", base.COMMANDER_BLUE)
        _submit_drafts(page)
        expect(page.locator(".pref-field-error")).to_contain_text(
            ["Partner must be a different card than the primary commander."])

        # Not a legal commander (Scryfall 404).
        page.fill("#pref_cmdr_0", "Lightning Bolt")
        page.fill("#pref_partner_cmdr_0", "")
        _submit_drafts(page)
        expect(page.locator(".pref-field-error")).to_contain_text(["Invalid commander selected."])
        expect(page.locator("#screenBuilding")).to_be_hidden()

        # Name longer than 100 characters.
        page.fill("#pref_cmdr_0", base.COMMANDER_BLUE)
        page.fill("#pref_name_0", "x" * 101)
        _submit_drafts(page)
        expect(page.locator(".pref-field-error")).to_contain_text(
            ["Deck name must be 100 characters or fewer."])

    def test_missing_preferences_asks_for_confirmation_and_cancel_stays(self, page: Page, base_url: str, r, world,
                                                                        scripted):
        llm = scripted([])
        _start_from_deck_page(page, base_url, DECK_A)
        _go_to_preferences(page)

        dialogs: List[str] = []

        def on_dialog(dialog):
            dialogs.append(dialog.message)
            dialog.dismiss()

        page.on("dialog", on_dialog)
        _submit_drafts(page)
        page.wait_for_timeout(300)
        assert dialogs and "random" in dialogs[0].lower(), dialogs
        expect(page.locator("#screenPreferences")).to_be_visible()
        assert llm.calls == []

    def test_two_source_duplicate_commander_across_decks_is_rejected(self, page: Page, base_url: str, r, world):
        _start_from_deck_page(page, base_url, DECK_A, second=DECK_B)
        _go_to_preferences(page)
        page.locator("#buildTwoDecksToggle").check()
        _fill_prefs(page, 0, commander=base.COMMANDER_BLUE)
        _fill_prefs(page, 1, commander=base.COMMANDER_BLUE)
        _submit_drafts(page)
        expect(page.locator(".pref-field-error").first).to_contain_text(
            "This commander is already used by another target deck.")
        expect(page.locator("#screenBuilding")).to_be_hidden()

    def test_two_source_partner_pair_validates_without_error(self, page: Page, base_url: str, r, world, scripted):
        scripted([base.deck_reply([
            {"name": "Partner Storm", "commanders": [base.PARTNER_A, base.PARTNER_B],
             "cards": base.plan_partner_pair()},
        ])])
        _start_from_deck_page(page, base_url, DECK_A, second=DECK_B)
        _go_to_preferences(page)
        # One target deck, built from both sources, with a partner pair.
        _fill_prefs(page, 0, commander=base.PARTNER_A, partner=base.PARTNER_B, themes="partners")
        _submit_drafts(page)
        # Valid partner pair: no validation errors, and the build completes.
        expect(page.locator(".pref-field-error")).to_have_count(0)
        _wait_for_results(page)
        assert len(_draft_cards(page)) == 1



# ---------------------------------------------------------------------------
# Drafting helpers (shared by scenarios 3-6)
# ---------------------------------------------------------------------------

def _single_replies() -> List[str]:
    return [base.deck_reply([{"name": "Blue Phoenix", "commanders": [base.COMMANDER_BLUE],
                              "cards": base.plan_blue_single_full()}])]


def _two_replies() -> List[str]:
    return [base.deck_reply([
        {"name": "Blue Phoenix", "commanders": [base.COMMANDER_BLUE], "cards": base.plan_blue_single_full()},
        {"name": "Red Hammer", "commanders": [base.COMMANDER_RED], "cards": base.plan_red_second()},
    ])]


def _draft(page: Page, base_url: str, scripted, replies: List[str], *, two: bool) -> str:
    """Run the full flow to the results screen. Returns the session id."""
    scripted(replies)
    sid = _start_from_deck_page(page, base_url, DECK_A, second=DECK_B if two else None)
    _go_to_preferences(page)
    if two:
        page.locator("#buildTwoDecksToggle").check()
        _fill_prefs(page, 0, commander=base.COMMANDER_BLUE, themes="blue artifacts")
        _fill_prefs(page, 1, commander=base.COMMANDER_RED, themes="red sorceries")
    else:
        _fill_prefs(page, 0, commander=base.COMMANDER_BLUE, themes="blue artifacts")
    _submit_drafts(page)
    _wait_for_results(page)
    return sid


def _session_status(page: Page, base_url: str, sid: str) -> str:
    return _api(page, base_url, f"/api/reconstruct/{sid}")["session"]["status"]


# ---------------------------------------------------------------------------
# Scenario 3: draft results (source tags, costs, totals)
# ---------------------------------------------------------------------------

class TestDraftResults:
    def test_every_card_has_source_tag_and_pool_or_owned_costs_nothing(self, page: Page, base_url: str,
                                                                       r, world, scripted):
        sid = _draft(page, base_url, scripted, _single_replies(), two=False)
        drafts = _draft_cards(page)
        assert len(drafts) == 1
        rows = _rows(drafts[0])
        assert sum(int(row["qty"].rstrip("x")) for row in rows) == 100

        buy_sum = 0.0
        for row in rows:
            kind = _tag_kind(row["tag"])  # raises on a missing or unknown tag
            if row["name"] in BASIC_NAMES:
                continue
            assert kind == _expected_kind(row["name"]), row
            if kind == "buy":
                assert row["cost"] == base.PRICES[row["name"]], row
                buy_sum += row["cost"]
            else:
                assert row["cost"] == 0.0, row

        assert _header_cost(drafts[0]) == round(buy_sum, 2)
        api_total = _api(page, base_url, f"/api/reconstruct/{sid}/drafts")["drafts"][0]["total_new_cost"]
        assert round(buy_sum, 2) == round(api_total, 2)
        expect(page.locator("#resultsGrandTotalCost")).to_have_text(f"${buy_sum:.2f}")

    def test_two_deck_totals_equal_sum_of_buy_prices(self, page: Page, base_url: str, r, world, scripted):
        _draft(page, base_url, scripted, _two_replies(), two=True)
        drafts = _draft_cards(page)
        assert len(drafts) == 2
        per_draft = []
        for draft in drafts:
            rows = _rows(draft)
            buy_rows = [row for row in rows if _tag_kind(row["tag"]) == "buy"]
            buy_sum = round(sum(row["cost"] for row in buy_rows), 2)
            assert _header_cost(draft) == buy_sum
            tier_pill = re.search(r"New to Buy: (\d+) cards? \(\$(\d+\.\d{2})\)", draft.text_content())
            assert tier_pill, draft.text_content()
            assert int(tier_pill.group(1)) == len(buy_rows)
            assert float(tier_pill.group(2)) == buy_sum
            per_draft.append(buy_sum)
        expect(page.locator("#resultsGrandTotalCost")).to_have_text(f"${round(sum(per_draft), 2):.2f}")


# ---------------------------------------------------------------------------
# Scenario 4: incomplete / unpriced notice, COMMANDER_MISMATCH error
# ---------------------------------------------------------------------------

class TestDraftNoticeAndErrors:
    def test_unpriced_card_shows_incomplete_pricing_notice(self, page: Page, base_url: str, r, world, scripted):
        cards = base.plan_blue_single() + [(UNPRICED, 1)]
        replies = [base.deck_reply([{"name": "Priceless Phoenix", "commanders": [base.COMMANDER_BLUE],
                                     "cards": base._pad_basics(cards, "U")}])]
        _draft(page, base_url, scripted, replies, two=False)
        drafts = _draft_cards(page)
        notice = drafts[0].locator(".draft-incomplete-notice")
        expect(notice).to_be_visible()
        expect(notice).to_contain_text("Incomplete pricing")
        expect(notice).to_contain_text(f"Unpriced: {UNPRICED}")
        row = [x for x in _rows(drafts[0]) if x["name"] == UNPRICED]
        assert row and _tag_kind(row[0]["tag"]) == "buy"

    def test_complete_draft_has_no_incomplete_notice(self, page: Page, base_url: str, r, world, scripted):
        _draft(page, base_url, scripted, _single_replies(), two=False)
        expect(page.locator("#draftsListContainer .draft-incomplete-notice")).to_have_count(0)

    def test_commander_mismatch_shows_error_state_not_results(self, page: Page, base_url: str, r, world, scripted):
        wrong = base.deck_reply([{"name": "Wrong Commander", "commanders": [base.COMMANDER_RED],
                                  "cards": base.plan_red_second()}])
        # Same bad reply on every retry (max_retries=3 -> 4 calls).
        scripted([wrong] * 4)
        sid = _start_from_deck_page(page, base_url, DECK_A)
        _go_to_preferences(page)
        _fill_prefs(page, 0, commander=base.COMMANDER_BLUE)
        _submit_drafts(page)

        err = page.locator("#buildingError")
        expect(err).to_be_visible(timeout=30000)
        expect(err).to_have_attribute("data-code", "COMMANDER_MISMATCH")
        expect(page.locator("#buildingErrorMessage")).to_contain_text("commander differs")
        expect(page.locator("#screenResults")).to_be_hidden()
        assert _session_status(page, base_url, sid) == "failed"


# ---------------------------------------------------------------------------
# Scenario 5: accept creates and links the new deck(s)
# ---------------------------------------------------------------------------

class TestAccept:
    def test_accept_single_creates_reachable_physical_deck(self, page: Page, base_url: str, r, world, scripted):
        _draft(page, base_url, scripted, _single_replies(), two=False)
        page.locator("#btnAcceptSave").click()
        expect(page.locator("#acceptModal")).to_be_visible(timeout=15000)
        links = page.locator("#acceptCreatedDecksList a")
        expect(links).to_have_count(1)
        expect(links.first).to_contain_text("Blue Phoenix")
        expect(page.locator("#btnAcceptSave")).to_be_disabled()

        links.first.click()
        expect(page).to_have_url(re.compile(r"/deck/Blue%20Phoenix$"))
        expect(page.locator("#reconstructBtn")).to_be_visible()  # physical deck actions are offered

        decks = _decks_by_name(page, base_url)
        assert decks["Blue Phoenix"]["status"] == "physical"
        assert decks[DECK_A]["status"] == "retired"

        page.goto(f"{base_url}/decks", wait_until="domcontentloaded")
        expect(page.get_by_text("Blue Phoenix").first).to_be_visible(timeout=10000)

    def test_accept_two_decks_creates_both_reachable(self, page: Page, base_url: str, r, world, scripted):
        _draft(page, base_url, scripted, _two_replies(), two=True)
        page.locator("#btnAcceptSave").click()
        expect(page.locator("#acceptModal")).to_be_visible(timeout=15000)
        links = page.locator("#acceptCreatedDecksList a")
        expect(links).to_have_count(2)
        hrefs = [links.nth(i).get_attribute("href") for i in range(2)]
        assert hrefs == [f"/deck/{quote('Blue Phoenix')}", f"/deck/{quote('Red Hammer')}"], hrefs

        decks = _decks_by_name(page, base_url)
        for name in ("Blue Phoenix", "Red Hammer"):
            assert decks[name]["status"] == "physical", name
            page.goto(f"{base_url}/deck/{quote(name)}", wait_until="domcontentloaded")
            expect(page.locator("#reconstructBtn")).to_be_visible()
        assert decks[DECK_A]["status"] == decks[DECK_B]["status"] == "retired"


# ---------------------------------------------------------------------------
# Scenario 6: abandon keeps sources retired and creates nothing
# ---------------------------------------------------------------------------

class TestAbandon:
    def test_abandon_leaves_sources_retired_and_creates_no_deck(self, page: Page, base_url: str, r, world,
                                                                 scripted):
        sid = _draft(page, base_url, scripted, _two_replies(), two=True)
        names_before = set(_decks_by_name(page, base_url))

        page.locator("#btnAbandonDraft").click()
        expect(page.locator("#abandonModal")).to_be_visible()
        page.fill("#abandonReason", "Not this time")
        page.locator("#confirmAbandonBtn").click()

        expect(page.locator("#sessionStatusBadge")).to_have_text("Status: ABANDONED")
        expect(page.locator("#globalErrorMessage")).to_contain_text("has been abandoned")
        expect(page.locator("#draftsActionBar")).to_be_hidden()
        assert _session_status(page, base_url, sid) == "abandoned"

        decks = _decks_by_name(page, base_url)
        assert set(decks) == names_before, set(decks) ^ names_before
        assert "Blue Phoenix" not in decks and "Red Hammer" not in decks
        assert decks[DECK_A]["status"] == decks[DECK_B]["status"] == "retired"


# ---------------------------------------------------------------------------
# Scenario 2 (continued): every preference field is saved as entered
# ---------------------------------------------------------------------------

def _saved_prefs(page: Page, base_url: str, sid: str) -> List[dict]:
    return _api(page, base_url, f"/api/reconstruct/{sid}")["session"]["preferences"]


class TestPreferencePersistence:
    def test_all_fields_saved_for_one_source_deck(self, page: Page, base_url: str, r, world, scripted):
        scripted(_single_replies())
        sid = _start_from_deck_page(page, base_url, DECK_A)
        _go_to_preferences(page)
        _fill_prefs(page, 0, name="Phoenix Build", commander=base.COMMANDER_BLUE,
                    themes="blue artifacts, tempo", strategy="Midrange",
                    arts=["fullart", "showcase"], notes="Flavor: sea-glass sails")
        _submit_drafts(page)
        _wait_for_results(page)

        prefs = _saved_prefs(page, base_url, sid)
        assert len(prefs) == 1
        p = prefs[0]
        assert p["name"] == "Phoenix Build"
        assert p["commanders"] == [base.COMMANDER_BLUE]
        assert p["themes"] == ["blue artifacts", "tempo"]
        assert p["strategies"] == ["Midrange"]
        assert sorted(p["art_style"]) == ["full-art", "showcase"]
        assert p["notes"] == "Flavor: sea-glass sails"

    def test_all_fields_saved_for_two_source_decks_with_partner(self, page: Page, base_url: str, r, world,
                                                               scripted):
        # Deck 1 takes the partner pair from both sources; deck 2 takes the rest of Beta
        # so no source copy is allocated twice.
        red_rest = ([(base.COMMANDER_RED, 1)] + [(n, 1) for n in base.BETA[40:]]
                    + [(n, 1) for n in base.OWNED[20:30]])
        scripted([base.deck_reply([
            {"name": "Partner Storm", "commanders": [base.PARTNER_A, base.PARTNER_B],
             "cards": base.plan_partner_pair()},
            {"name": "Red Rest", "commanders": [base.COMMANDER_RED],
             "cards": base._pad_basics(red_rest, "R")},
        ])])
        sid = _start_from_deck_page(page, base_url, DECK_A, second=DECK_B)
        _go_to_preferences(page)
        page.locator("#buildTwoDecksToggle").check()
        _fill_prefs(page, 0, name="Partner Storm", commander=base.PARTNER_A, partner=base.PARTNER_B,
                    themes="partners", strategy="Control", arts=["oldborder"], notes="Pair up")
        _fill_prefs(page, 1, name="Red Rest", commander=base.COMMANDER_RED,
                    themes="red sorceries", strategy="Aggro", arts=["matchframes", "fullart"],
                    notes="Fire and paper")
        _submit_drafts(page)
        _wait_for_results(page)

        prefs = _saved_prefs(page, base_url, sid)
        assert len(prefs) == 2
        a, b = prefs
        assert sorted(a["commanders"]) == sorted([base.PARTNER_A, base.PARTNER_B])
        assert a["themes"] == ["partners"] and a["strategies"] == ["Control"]
        assert a["art_style"] == ["old border"] and a["notes"] == "Pair up"
        assert b["commanders"] == [base.COMMANDER_RED]
        assert b["themes"] == ["red sorceries"] and b["strategies"] == ["Aggro"]
        assert sorted(b["art_style"]) == ["full-art", "match frames"] and b["notes"] == "Fire and paper"
        assert len(_draft_cards(page)) == 2

"""Live end-to-end checks for bulk deck decommission on /decks.

Drives a real browser against a running app backed by a real Redis. Each
scenario reseeds Redis, so scenarios are independent.

Run (server must already be up, Redis reachable):
    REDIS_URL=redis://localhost:6379/0 uvicorn app:app --port 8572 &
    E2E_BASE_URL=http://localhost:8572 REDIS_URL=redis://localhost:6379/0 \
        python tests/e2e_bulk_decommission_live.py

Exits 0 when all scenarios pass, 1 otherwise. Skipped (exit 0) when
E2E_BASE_URL is unset so the default unit suite is unaffected.
"""
import json
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.getenv("E2E_BASE_URL")
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

DECK_NAMES = {1: "Alpha Deck", 2: "Beta Deck", 3: "Gamma Deck", 4: "Delta Deck"}


def seed(r):
    import src.server.domain.inventory.instances as instances

    r.flushdb()

    def deck(i, status, name):
        return {"id": i, "name": name, "registry_id": f"archidekt:{i}", "source": "archidekt",
                "source_id": str(i), "status": status, "tags": ["removed", "keep"],
                "commanders": [f"Cmdr{i}"], "commander_uids": [], "color": "G",
                "cards": [{"name": "Sol Ring", "quantity": 1}]}

    decks = [deck(i, "physical", n) for i, n in DECK_NAMES.items()]
    decks += [deck(5, "digital", "Digi Deck"), deck(6, "retired", "Old Deck")]
    r.set("decks", json.dumps(decks))
    for i in DECK_NAMES:
        for c in ("Sol Ring", "Arcane Signet"):
            instances.create_instance(r, c, ownership_status="in_deck",
                                      deck_id=f"archidekt:{i}", deck_name=DECK_NAMES[i])


def deck_status(r, i):
    decks = json.loads(r.get("decks"))
    return next(d["status"] for d in decks if d["id"] == i)


def in_deck_count(r, i):
    import src.server.domain.inventory.instances as instances
    return len([x for x in instances.list_instances(r, deck_id=f"archidekt:{i}")
                if x.get("ownership_status") == "in_deck"])


def open_decks(page):
    page.goto(f"{BASE}/decks", wait_until="domcontentloaded")
    page.wait_for_selector("input.deck-pick, .deck-card", timeout=10000)
    page.wait_for_load_state("domcontentloaded")


def enter_select(page):
    page.click("#selectBtn")
    page.wait_for_selector("#bulkBar:not([hidden])")


def pick(page, ids):
    for i in ids:
        page.click(f'input.deck-pick[data-id="{i}"]')


def open_confirm(page):
    page.click("#bulkGo")
    page.wait_for_selector("#dlgConfirm")


def confirm_with_count(page, n):
    # Type-to-confirm is only rendered for 5+ decks (decks.html: needType = n >= 5).
    if page.locator("#bulkType").count():
        page.fill("#bulkType", str(n))
    page.click("#dlgConfirm")


def wait_done(page):
    page.wait_for_function(
        "() => !document.getElementById('bulkDecommissionModal') || "
        "document.getElementById('bulkDecommissionModal').style.display !== 'flex' || "
        "!!document.querySelector('#bulkDialogPanel .bulk-deck-list .is-failed, #bulkDialogPanel .bulk-deck-list .is-done')",
        timeout=10000,
    )
    page.wait_for_timeout(400)


SCENARIOS = {}


def scenario(fn):
    SCENARIOS[fn.__name__] = fn
    return fn


@scenario
def one_deck_via_bulk_flow(page, r, reqs):
    open_decks(page)
    enter_select(page)
    pick(page, [1])
    assert page.text_content("#bulkCount").strip() == "1 selected"
    open_confirm(page)
    confirm_with_count(page, 1)
    page.wait_for_selector("#bulkDecommissionModal[style*='none']", state="attached", timeout=10000)
    page.wait_for_selector(".hud-toast--ok", timeout=5000)
    assert deck_status(r, 1) == "retired", deck_status(r, 1)
    assert in_deck_count(r, 1) == 0
    assert page.locator('input.deck-pick[data-id="1"]').count() == 0, "retired deck still selectable"
    assert deck_status(r, 2) == "physical"
    assert in_deck_count(r, 2) == 2
    assert [q["deck_ids"] for q in reqs] == [["1"]], reqs


@scenario
def several_decks_at_once(page, r, reqs):
    open_decks(page)
    enter_select(page)
    pick(page, [1, 3])
    open_confirm(page)
    confirm_with_count(page, 2)
    page.wait_for_selector(".hud-toast--ok", timeout=5000)
    assert deck_status(r, 1) == "retired" and deck_status(r, 3) == "retired"
    assert deck_status(r, 2) == "physical" and deck_status(r, 4) == "physical"
    assert in_deck_count(r, 1) == 0 and in_deck_count(r, 3) == 0
    assert in_deck_count(r, 2) == 2
    assert len(reqs) == 1 and sorted(reqs[0]["deck_ids"]) == ["1", "3"], reqs


@scenario
def select_all_then_decommission(page, r, reqs):
    open_decks(page)
    enter_select(page)
    page.check("#bulkAll")
    assert page.text_content("#bulkCount").strip() == "4 selected"
    assert page.text_content("#bulkVisibleCount").strip() == "4"
    open_confirm(page)
    confirm_with_count(page, 4)
    page.wait_for_selector(".hud-toast--ok", timeout=5000)
    for i in DECK_NAMES:
        assert deck_status(r, i) == "retired", (i, deck_status(r, i))
        assert in_deck_count(r, i) == 0
    # Non-physical decks must be untouched.
    assert deck_status(r, 5) == "digital" and deck_status(r, 6) == "retired"
    assert len(reqs) == 1 and sorted(reqs[0]["deck_ids"]) == ["1", "2", "3", "4"]


@scenario
def cancel_at_confirmation_changes_nothing(page, r, reqs):
    open_decks(page)
    enter_select(page)
    pick(page, [1, 2])
    open_confirm(page)
    page.click("#dlgCancel")
    page.wait_for_selector("#bulkDecommissionModal", state="hidden", timeout=5000)
    assert page.text_content("#bulkCount").strip() == "2 selected", "selection lost on cancel"
    # Also verify Escape and the overlay dismiss the dialog without writing.
    open_confirm(page)
    page.keyboard.press("Escape")
    page.wait_for_selector("#bulkDecommissionModal", state="hidden", timeout=5000)
    assert reqs == [], reqs
    assert all(deck_status(r, i) == "physical" for i in (1, 2, 3, 4))
    assert in_deck_count(r, 1) == 2 and in_deck_count(r, 2) == 2


@scenario
def already_decommissioned_deck_in_selection(page, r, reqs):
    """A deck retired in another tab after this page loaded is still selected here.

    registry._apply_decommission checks `decommissioned_at` BEFORE the physical
    check, so an already-decommissioned deck is a no-op success (not `failed`).
    Expected: whole request succeeds, deck 2 stays retired, its cards are not
    touched again, and deck 1 is retired normally.
    """
    open_decks(page)
    enter_select(page)
    pick(page, [1, 2])
    decks = json.loads(r.get("decks"))
    for d in decks:
        if d["id"] == 2:
            d["status"] = "retired"
            d["decommissioned_at"] = "2026-10-16T00:00:00"
    r.set("decks", json.dumps(decks))
    open_confirm(page)
    confirm_with_count(page, 2)
    page.wait_for_selector(".hud-toast--ok", timeout=5000)
    assert deck_status(r, 1) == "retired"
    assert deck_status(r, 2) == "retired"
    assert in_deck_count(r, 1) == 0
    assert in_deck_count(r, 2) == 2, "already-retired deck's cards must not be unbound by this request"
    assert len(reqs) == 1 and sorted(reqs[0]["deck_ids"]) == ["1", "2"], reqs


@scenario
def partial_failure_keeps_failed_selected(page, r, reqs):
    """Simulate a partial failure: deck 1 succeeds, deck 2 fails at the API layer."""
    open_decks(page)
    enter_select(page)
    pick(page, [1, 2])

    def route_bulk(route):
        # Forward deck 1 to backend
        res = route.fetch(post_data=json.dumps({"deck_ids": ["archidekt:1"]}))
        res_json = res.json()
        # Add deck 2 as failed in response
        res_json["failed"] = [{"deck_id": "2", "reason": "Simulated card lock failure", "code": "locked"}]
        res_json["requested"] = 2
        route.fulfill(response=res, json=res_json)

    page.route("**/api/decks/decommission", route_bulk)
    open_confirm(page)
    confirm_with_count(page, 2)
    page.wait_for_selector("#bulkDialogPanel.is-result-partial", timeout=10000)
    assert deck_status(r, 1) == "retired"
    assert deck_status(r, 2) == "physical"
    failed_rows = page.locator("#bulkDialogPanel .bulk-deck-row.is-failed").count()
    assert failed_rows == 1, failed_rows
    page.click("#bulkDialogPanel button.btn:has-text('Close'), #bulkDialogPanel .modal-close")
    page.wait_for_timeout(300)
    # Check that failed deck 2 stays selected and has .is-failed class on its card
    assert page.locator('input.deck-pick[data-id="2"]').is_checked(), "failed deck should stay selected"
    assert page.locator('.deck-card.is-failed').count() == 1, "failed deck card should have is-failed style"
    assert page.locator('#bulkFailedNote').is_visible()
    page.unroute("**/api/decks/decommission")


@scenario
def double_click_confirm_single_request(page, r, reqs):
    open_decks(page)
    enter_select(page)
    pick(page, [1, 2])
    open_confirm(page)
    page.dblclick("#dlgConfirm")
    page.wait_for_timeout(1500)
    assert len(reqs) == 1, f"expected 1 request, got {len(reqs)}: {reqs}"
    errors = page.evaluate("() => window.__errors || []")
    assert not errors, errors
    assert deck_status(r, 1) == "retired" and deck_status(r, 2) == "retired"


@scenario
def selection_survives_filter_toggle(page, r, reqs):
    open_decks(page)
    enter_select(page)
    pick(page, [1])
    page.check("#showAllFolders")
    page.wait_for_timeout(200)
    assert page.text_content("#bulkCount").strip() == "1 selected", "selection lost on filter toggle"
    assert page.locator('input.deck-pick[data-id="1"]').is_checked()
    page.uncheck("#showAllFolders")
    page.wait_for_timeout(200)
    assert page.text_content("#bulkCount").strip() == "1 selected"
    # Select-all counts only visible physical decks, never the hidden digital/retired ones.
    page.check("#bulkAll")
    assert page.text_content("#bulkCount").strip() == "4 selected"


@scenario
def single_deck_regression(page, r, reqs):
    """Single-deck decommission on /deck/{name} still works end to end."""
    page.goto(f"{BASE}/deck/{DECK_NAMES[1].replace(' ', '%20')}")
    page.wait_for_load_state("domcontentloaded")
    page.click("#decommissionBtn")
    page.wait_for_selector("#decommissionModal", state="visible", timeout=5000)
    page.click("#decommissionConfirmBtn")
    page.wait_for_selector("#decommissionModal", state="hidden", timeout=10000)
    assert deck_status(r, 1) == "retired"
    assert in_deck_count(r, 1) == 0
    assert in_deck_count(r, 2) == 2


def main():
    if not BASE:
        print("SKIP: set E2E_BASE_URL to run live E2E checks")
        return 0
    import redis
    from playwright.sync_api import sync_playwright

    r = redis.from_url(REDIS_URL, decode_responses=True)
    results = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        for name, fn in SCENARIOS.items():
            seed(r)
            ctx = browser.new_context(viewport={"width": 1280, "height": 900})
            page = ctx.new_page()
            reqs, errs = [], []
            page.on("pageerror", lambda e: errs.append(str(e)))
            page.on("console", lambda m: errs.append(m.text) if m.type == "error" and "Failed to load resource" not in m.text else None)

            def on_req(req):
                if req.method == "POST" and "/api/decks/decommission" in req.url:
                    reqs.append(json.loads(req.post_data or "{}"))
            page.on("request", on_req)
            try:
                fn(page, r, reqs)
                if errs:
                    raise AssertionError(f"page errors: {errs}")
                results.append((name, "PASS", ""))
            except Exception as e:
                tb = traceback.format_exc().strip().splitlines()[-1]
                results.append((name, "FAIL", tb))
            finally:
                ctx.close()
        browser.close()
    r.flushdb()
    for name, status, detail in results:
        print(f"{status}  {name}" + (f"  -- {detail}" if detail else ""))
    failed = [x for x in results if x[1] != "PASS"]
    print(f"\n{len(results) - len(failed)}/{len(results)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

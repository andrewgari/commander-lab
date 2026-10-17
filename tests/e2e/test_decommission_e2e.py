"""End-to-end tests for the physical-deck Decommission feature.

Runs the real app under uvicorn against a real Redis and drives the deck page
with Playwright.

Prerequisites
  * Redis reachable at E2E_REDIS_URL (default redis://localhost:6399). Uses DB 7.
    e.g. `docker run -d -p 6399:6379 redis:alpine`
  * pip install pytest pytest-playwright playwright && playwright install chromium
  * Run: python -m pytest tests/e2e/test_decommission_e2e.py -v

Skipped (not failed) when Playwright or Redis is unavailable, so unit CI is unaffected.
"""
from __future__ import annotations

import json
import re
import os
import socket
import subprocess
import sys
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

try:
    import pytest
    import redis as redis_lib
    from playwright.sync_api import expect, sync_playwright
except ImportError as exc:  # E2E-only deps
    raise unittest.SkipTest(f"playwright/pytest not installed: {exc}") from exc

ROOT = Path(__file__).resolve().parents[2]
REDIS_URL = os.getenv("E2E_REDIS_URL", "redis://localhost:6399")
REDIS_DB = 7

PHYS_ID = "archidekt:9301"       # physical deck under test
OTHER_ID = "archidekt:9302"      # physical deck that must be unaffected
DIGITAL_ID = "archidekt:9303"    # non-physical deck
PHYS_NAME = "E2E Physical Krenko"
OTHER_NAME = "E2E Other Physical"
DIGITAL_NAME = "E2E Digital Only"

PHYS_CARDS = [{"name": "Krenko, Mob Boss", "quantity": 1}, {"name": "Sol Ring", "quantity": 2}]
OTHER_CARDS = [{"name": "Sol Ring", "quantity": 1}, {"name": "Arcane Signet", "quantity": 1}]
DIGITAL_CARDS = [{"name": "Lightning Bolt", "quantity": 1}]


def _redis():
    return redis_lib.Redis.from_url(f"{REDIS_URL}/{REDIS_DB}", decode_responses=True)


def _redis_ok() -> bool:
    try:
        return _redis().ping()
    except Exception:  # noqa: BLE001
        return False


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _http(method: str, url: str, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else (b"" if method == "POST" else None)
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _seed(r) -> None:
    """Reset DB 7 to a known state: three digital decks plus one loose owned Sol Ring."""
    r.flushdb()
    decks = [
        {"id": 9301, "name": PHYS_NAME, "source": "archidekt", "source_id": "9301",
         "registry_id": PHYS_ID, "status": "digital", "cards": PHYS_CARDS,
         "folder": "Physical", "parent_folder_id": 588380, "tags": ["removed", "keeper"]},
        {"id": 9302, "name": OTHER_NAME, "source": "archidekt", "source_id": "9302",
         "registry_id": OTHER_ID, "status": "digital", "cards": OTHER_CARDS,
         "folder": "Physical", "parent_folder_id": 588380, "tags": []},
        {"id": 9303, "name": DIGITAL_NAME, "source": "archidekt", "source_id": "9303",
         "registry_id": DIGITAL_ID, "status": "digital", "cards": DIGITAL_CARDS,
         "folder": "Digital", "parent_folder_id": 111, "tags": []},
    ]
    r.set("decks", json.dumps(decks))
    # One loose owned Sol Ring (not in any deck) so the overall-count check is meaningful.
    r.set("instance:e2e-loose-sol-ring", json.dumps({
        "id": "e2e-loose-sol-ring", "card_name": "Sol Ring", "ownership_status": "in_collection",
        "deck_id": None, "deck_name": "", "history": [], "set": "", "set_name": "",
        "collector_number": "", "finish": "nonfoil", "condition": "NM", "language": "EN",
        "considered_for_deck": None, "price_paid": 0.0, "source": "e2e", "date_ordered": "",
        "date_acquired": "", "archidekt_uid": "", "notes": "", "updated_at": "",
    }))


def _owned_summary(r) -> dict:
    """{ownership_status: count} over every instance in the store."""
    counts: dict[str, int] = {}
    for key in r.scan_iter("instance:*"):
        rec = json.loads(r.get(key))
        counts[rec["ownership_status"]] = counts.get(rec["ownership_status"], 0) + 1
    return counts


def _instances_for_deck(r, deck_id: int | str) -> list[dict]:
    out = []
    for key in r.scan_iter("instance:*"):
        rec = json.loads(r.get(key))
        if str(rec.get("deck_id")) == str(deck_id) and rec["ownership_status"] == "in_deck":
            out.append(rec)
    return out


@pytest.fixture(scope="module")
def server():
    if not _redis_ok():
        pytest.skip(f"Redis not reachable at {REDIS_URL}")
    port = _free_port()
    env = dict(os.environ, REDIS_URL=f"{REDIS_URL}/{REDIS_DB}", PYTHONPATH=str(ROOT))
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(ROOT), env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(f"{base}/api/decks", timeout=1)
            break
        except Exception:  # noqa: BLE001
            time.sleep(0.1)
    else:
        proc.kill()
        pytest.fail("uvicorn did not start")
    yield base
    proc.terminate()
    proc.wait(timeout=10)


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture
def r(server):
    client = _redis()
    _seed(client)
    # Physical is set only by the Archidekt folder rule (parent_folder_id 588380, see
    # docs/PHYSICAL_FOLDER_LOCK.md), never via the status endpoint. Run the same
    # registry transition the folder sync uses so the decklists bind in_deck.
    sys.path.insert(0, str(ROOT))
    import registry
    for rid in (PHYS_ID, OTHER_ID):
        registry.set_status(client, rid, "physical", origin="archidekt_folder")
    return client


@pytest.fixture
def page(browser, server, r):
    ctx = browser.new_context(viewport={"width": 1400, "height": 900})
    pg = ctx.new_page()
    yield pg
    ctx.close()


def _open_deck(page, server, name):
    import urllib.parse
    page.goto(f"{server}/deck/{urllib.parse.quote(name)}")
    expect(page.locator("#deckStatus")).not_to_have_text("-", timeout=10000)


def _deck_record(r, rid):
    for d in json.loads(r.get("decks")):
        if d["registry_id"] == rid:
            return d
    raise AssertionError(f"deck {rid} missing")


# ---------------------------------------------------------------------------
# Case 1: physical deck shows the Decommission button
# ---------------------------------------------------------------------------
def test_1_physical_deck_shows_decommission_button(page, server, r):
    _open_deck(page, server, PHYS_NAME)
    expect(page.locator("#deckStatus")).to_have_text(re.compile(r"^\s*PHYSICAL\s*$", re.I))
    expect(page.locator("#decommissionBtn")).to_be_visible()


# ---------------------------------------------------------------------------
# Case 2: non-physical deck hides the button, and the API rejects the request
# ---------------------------------------------------------------------------
def test_2_non_physical_deck_hides_button_and_api_rejects(page, server, r):
    _open_deck(page, server, DIGITAL_NAME)
    expect(page.locator("#deckStatus")).to_have_text(re.compile(r"^\s*DIGITAL\s*$", re.I))
    expect(page.locator("#decommissionBtn")).to_be_hidden()

    status, body = _http("POST", f"{server}/api/decks/{DIGITAL_ID}/decommission")
    assert status == 400, (status, body)
    assert body["success"] is False
    assert "only physical" in body["error"]
    assert _deck_record(r, DIGITAL_ID)["status"] == "digital"


# ---------------------------------------------------------------------------
# Case 3: cancelling the confirmation makes no changes
# ---------------------------------------------------------------------------
def test_3_cancel_makes_no_changes(page, server, r):
    before_deck = _deck_record(r, PHYS_ID)
    before_counts = _owned_summary(r)
    before_bound = sorted(i["id"] for i in _instances_for_deck(r, 9301))

    _open_deck(page, server, PHYS_NAME)
    page.locator("#decommissionBtn").click()
    expect(page.locator("#decommissionModal")).to_be_visible()
    page.locator("#decommissionCancelBtn").click()
    expect(page.locator("#decommissionModal")).to_be_hidden()

    after = _deck_record(r, PHYS_ID)
    assert after["status"] == "physical"
    assert after["tags"] == before_deck["tags"]
    assert _owned_summary(r) == before_counts
    assert sorted(i["id"] for i in _instances_for_deck(r, 9301)) == before_bound


# ---------------------------------------------------------------------------
# Case 4: confirming retires the deck, drops the tag, returns cards to the pool
# ---------------------------------------------------------------------------
def test_4_confirm_retires_untags_and_unassigns(page, server, r):
    bound_before = {i["id"] for i in _instances_for_deck(r, 9301)}
    assert len(bound_before) == 3, "Krenko + 2x Sol Ring should be bound in_deck"
    total_before = sum(_owned_summary(r).values())
    owned_before = _owned_summary(r)

    _open_deck(page, server, PHYS_NAME)
    page.locator("#decommissionBtn").click()
    page.locator("#decommissionConfirmBtn").click()
    expect(page.locator("#decommissionModal")).to_be_hidden()
    expect(page.locator("#deckStatus")).to_have_text(re.compile(r"^\s*RETIRED\s*$", re.I))
    expect(page.locator("#decommissionBtn")).to_be_hidden()

    rec = _deck_record(r, PHYS_ID)
    assert rec["status"] == "retired"
    assert "removed" not in rec["tags"], rec["tags"]
    assert rec["tags"] == ["keeper"]  # other tags are preserved

    # Retired listing on the decks page: the "Retired" group (sidebar filter /decks?status=retired).
    page.goto(f"{server}/decks?status=retired")
    expect(page.get_by_role("link", name=PHYS_NAME).first).to_be_visible(timeout=10000)
    page.goto(f"{server}/decks?status=physical")
    expect(page.get_by_role("link", name=OTHER_NAME).first).to_be_visible(timeout=10000)
    expect(page.get_by_role("link", name=PHYS_NAME)).to_have_count(0)
    page.goto(f"{server}/decks?status=retired")
    expect(page.get_by_role("link", name=PHYS_NAME).first).to_be_visible()

    # Every card previously bound is now owned, unassigned
    for iid in bound_before:
        inst = json.loads(r.get(f"instance:{iid}"))
        assert inst["ownership_status"] == "in_collection", inst
        assert inst["deck_id"] is None
    assert _instances_for_deck(r, 9301) == []

    # Overall owned count unchanged (nothing deleted, nothing created)
    assert sum(_owned_summary(r).values()) == total_before
    after = _owned_summary(r)
    assert after.get("in_collection", 0) == owned_before.get("in_collection", 0) + 3
    assert after.get("in_deck", 0) == owned_before.get("in_deck", 0) - 3


# ---------------------------------------------------------------------------
# Case 5: decommissioning an already-retired deck is handled gracefully
# ---------------------------------------------------------------------------
def test_5_already_retired_is_graceful(page, server, r):
    # Retire the deck once via the UI, then exercise the already-retired paths.
    _open_deck(page, server, PHYS_NAME)
    page.locator("#decommissionBtn").click()
    page.locator("#decommissionConfirmBtn").click()
    expect(page.locator("#deckStatus")).to_have_text(re.compile(r"^\s*RETIRED\s*$", re.I))

    # Via the API: second call succeeds as a no-op.
    status, body = _http("POST", f"{server}/api/decks/{PHYS_ID}/decommission")
    assert status == 200, (status, body)
    assert body["already_decommissioned"] is True
    assert body["unassigned_count"] == 0
    assert body["status"] == "retired"

    # Via the UI: the button is hidden for a retired deck, so it can't be re-triggered.
    _open_deck(page, server, PHYS_NAME)
    expect(page.locator("#decommissionBtn")).to_be_hidden()


# ---------------------------------------------------------------------------
# Case 6: other decks and their card assignments are unaffected
# ---------------------------------------------------------------------------
def test_6_other_decks_unaffected(page, server, r):
    other_before = sorted(i["id"] for i in _instances_for_deck(r, 9302))
    assert len(other_before) == 2

    _open_deck(page, server, PHYS_NAME)
    page.locator("#decommissionBtn").click()
    page.locator("#decommissionConfirmBtn").click()
    expect(page.locator("#deckStatus")).to_have_text(re.compile(r"^\s*RETIRED\s*$", re.I))

    assert sorted(i["id"] for i in _instances_for_deck(r, 9302)) == other_before
    other = _deck_record(r, OTHER_ID)
    assert other["status"] == "physical"
    assert _deck_record(r, DIGITAL_ID)["status"] == "digital"
    _open_deck(page, server, OTHER_NAME)
    expect(page.locator("#decommissionBtn")).to_be_visible()


# ---------------------------------------------------------------------------
# Former known bug, now closed by the physical-folder lock: a physical deck can
# no longer be retired through the status dropdown/endpoint (that path used to
# leave its cards bound in_deck). The only way out is Decommission.
# ---------------------------------------------------------------------------
def test_status_endpoint_cannot_retire_physical_deck(server, r):
    status, body = _http("POST", f"{server}/api/decks/{PHYS_ID}/status", {"status": "retired"})
    assert status >= 400 and body["success"] is False, (status, body)
    assert _deck_record(r, PHYS_ID)["status"] == "physical"
    assert len(_instances_for_deck(r, 9301)) == 3, "cards must stay bound after a rejected status change"

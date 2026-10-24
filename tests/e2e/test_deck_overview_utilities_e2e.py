"""Playwright E2E for the deck Overview "Deck utilities" section (PR #288, DV-02).

- #footerLinks / #syncControls / #deckStatusField each render once, inside
  #deckUtilities, none inside .deck-hero-side, #deckUtilities is the last child
  of .ov-summary (live DOM).
- No horizontal overflow at 1440px and 390px; screenshots saved to
  $DV02_SHOT_DIR (default: ./test-artifacts/dv02).
- The Update button ([data-sync-op]) still opens the sync confirm modal for a
  deck with metadata/registry_id.

Prerequisites: app running at --base-url; REDIS_URL points at the same Redis.
Run:
    REDIS_URL=redis://localhost:6379/0 python -m pytest \
        tests/e2e/test_deck_overview_utilities_e2e.py --base-url=http://localhost:8000 -v
"""
import json
import os
import unittest
import uuid
from pathlib import Path
from urllib.parse import quote

try:
    import pytest
    import redis
    from playwright.sync_api import expect
except ImportError as exc:  # E2E-only deps; keep unittest discovery green
    raise unittest.SkipTest("pytest/playwright not installed") from exc

DECKS_KEY = "decks"
SHOT_DIR = Path(os.environ.get("DV02_SHOT_DIR", "test-artifacts/dv02"))


@pytest.fixture(scope="session")
def redis_client():
    return redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


@pytest.fixture
def seeded_deck(redis_client):
    """A synced deck (registry_id set) so the sync controls render."""
    uid = uuid.uuid4().hex[:8]
    name = f"DV02 Utilities {uid}"
    source_id = 880000 + int(uid, 16) % 90000
    deck = {
        "id": source_id, "name": name, "color": "WBR", "commanders": ["Kaalia of the Vast"],
        "commander_uids": ["ef099f25-2b0b-4028-8490-fbc859e35f6c"], "status": "physical",
        "registry_id": f"archidekt:{source_id}", "source": "archidekt", "source_id": str(source_id),
        # sync_status only reports providers present in deck["links"] as linked
        "links": {"archidekt": {"identifier": str(source_id)}},
        "cards": [{"name": "Kaalia of the Vast", "quantity": 1}, {"name": "Plains", "quantity": 5}],
    }
    decks = json.loads(redis_client.get(DECKS_KEY) or "[]")
    decks.append(deck)
    redis_client.set(DECKS_KEY, json.dumps(decks))
    yield deck
    remaining = [d for d in json.loads(redis_client.get(DECKS_KEY) or "[]") if d.get("name") != name]
    redis_client.set(DECKS_KEY, json.dumps(remaining))


def _open(page, base_url, deck, width):
    page.set_viewport_size({"width": width, "height": 900})
    page.goto(f"{base_url}/deck/{quote(deck['name'])}")
    page.wait_for_load_state("networkidle")
    expect(page.locator("#deckUtilities")).to_be_attached()


def test_utilities_placement_in_live_dom(page, base_url, seeded_deck):
    _open(page, base_url, seeded_deck, 1440)
    for ident in ("footerLinks", "syncControls", "deckStatusField"):
        assert page.locator(f"#{ident}").count() == 1, f"#{ident} must be exactly once"
        assert page.locator(f"#deckUtilities #{ident}").count() == 1, f"#{ident} not inside #deckUtilities"
        assert page.locator(f".deck-hero-side #{ident}").count() == 0, f"#{ident} still in hero side"
    assert page.locator(".deck-hero-side #commanderShowcase").count() == 1
    last_child_id = page.evaluate(
        "() => { const k = [...document.querySelector('.ov-summary').children]; "
        "return k[k.length - 1].id; }"
    )
    assert last_child_id == "deckUtilities", f"last child of .ov-summary is {last_child_id!r}"
    order = page.evaluate(
        "() => { const u = document.getElementById('deckUtilities');"
        " const b = document.getElementById('deckDescriptionPanel');"
        " return !!(b.compareDocumentPosition(u) & Node.DOCUMENT_POSITION_FOLLOWING); }"
    )
    assert order, "#deckUtilities must follow the Briefing/Description panel"


@pytest.mark.parametrize("width", [1440, 390])
def test_no_horizontal_overflow(page, base_url, seeded_deck, width):
    _open(page, base_url, seeded_deck, width)
    SHOT_DIR.mkdir(parents=True, exist_ok=True)
    page.locator("#deckUtilities").scroll_into_view_if_needed()
    overflow = page.evaluate(
        "() => ({sw: document.documentElement.scrollWidth, cw: document.documentElement.clientWidth})"
    )
    assert overflow["sw"] <= overflow["cw"], f"horizontal overflow at {width}px: {overflow}"
    page.screenshot(path=str(SHOT_DIR / f"deck-utilities-{width}.png"), full_page=True)


def test_update_button_opens_confirm_modal(page, base_url, seeded_deck):
    # Stub only the provider dry-run preview (POST /sync/pull dry_run=true) so the
    # test does not depend on archidekt.com being reachable from CI.
    def _dry_run(route):
        body = route.request.post_data_json or {}
        if body.get("dry_run") is True:
            route.fulfill(status=200, content_type="application/json", body=json.dumps({
                "success": True, "dry_run": True, "changes": {"added": [], "removed": [], "changed": []},
                "registry_id": seeded_deck["registry_id"], "sources": [], "conflicts": [], "warnings": [],
            }))
        else:
            route.continue_()

    page.route(f"**/api/decks/*/sync/pull", _dry_run)
    _open(page, base_url, seeded_deck, 1440)
    btn = page.locator("#syncControls [data-sync-op='update']").first
    expect(btn).to_be_visible(timeout=10000)
    btn.click()
    expect(page.locator("#syncConfirmModal")).to_be_visible(timeout=10000)

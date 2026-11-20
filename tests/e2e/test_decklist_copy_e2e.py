"""
Playwright E2E: the deck page "Copy decklist" control puts the right text on
the clipboard (src/web/static/js/decklist_copy.js -> /api/deck/{name}/decklist).

Prerequisites (same convention as tests/e2e/test_deck_genre_e2e.py):
- the app is running at --base-url (default http://localhost:8000);
- REDIS_URL points at the SAME Redis the app uses (decks are seeded there).

Run:
    REDIS_URL=redis://localhost:6379/0 python -m pytest tests/e2e/test_decklist_copy_e2e.py \
        --base-url=http://localhost:8000 -v
"""
import json
import os
import unittest
import uuid
from urllib.parse import quote

try:
    import pytest
    import redis
    from playwright.sync_api import Page, expect
except ImportError as exc:  # E2E-only deps; keep unittest discovery green
    raise unittest.SkipTest("pytest/playwright not installed; install them to run the E2E tests") from exc

DECKS_KEY = "decks"


@pytest.fixture(scope="session")
def redis_client():
    return redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


@pytest.fixture
def seeded_deck(redis_client):
    suffix = uuid.uuid4().hex[:8]
    source_id = 9_000_000 + int(suffix, 16) % 1_000_000
    deck = {
        "id": source_id, "name": f"E2E Copy {suffix}", "color": "WBR",
        "commanders": ["Kaalia of the Vast"], "status": "physical",
        "registry_id": f"archidekt:{source_id}", "source": "archidekt", "source_id": str(source_id),
        "cards": [{"name": "Kaalia of the Vast", "quantity": 1}, {"name": "Plains", "quantity": 5}],
    }
    decks = json.loads(redis_client.get(DECKS_KEY) or "[]")
    decks.append(deck)
    redis_client.set(DECKS_KEY, json.dumps(decks))
    yield deck
    rid = deck["registry_id"]
    decks = [d for d in json.loads(redis_client.get(DECKS_KEY) or "[]") if d.get("registry_id") != rid]
    redis_client.set(DECKS_KEY, json.dumps(decks))


def _open(page: Page, base_url: str, deck: dict):
    page.context.grant_permissions(["clipboard-read", "clipboard-write"])
    page.goto(f"{base_url}/deck/{quote(deck['name'])}")
    page.wait_for_load_state("networkidle")
    expect(page.locator("#copyDeckBtn")).to_be_visible()


def _clipboard(page: Page) -> str:
    return page.evaluate("navigator.clipboard.readText()")


def test_copy_button_copies_plain_commander_first(page: Page, base_url: str, seeded_deck):
    _open(page, base_url, seeded_deck)
    page.locator("#copyDeckBtn").click()
    expect(page.locator("#copyDeckMsg")).to_have_text("Copied!")
    assert _clipboard(page) == "1 Kaalia of the Vast\n5 Plains"


def test_format_picker_changes_copied_text_and_persists(page: Page, base_url: str, seeded_deck):
    _open(page, base_url, seeded_deck)
    page.locator("#copyDeckOptBtn").click()
    page.locator("#copyDeckFormat").select_option("moxfield")
    page.locator("#copyDeckBtn").click()
    expect(page.locator("#copyDeckMsg")).to_have_text("Copied!")
    assert _clipboard(page) == "1 Kaalia of the Vast *CMDR*\n5 Plains"

    page.reload()
    page.wait_for_load_state("networkidle")
    expect(page.locator("#copyDeckFormat")).to_have_value("moxfield")

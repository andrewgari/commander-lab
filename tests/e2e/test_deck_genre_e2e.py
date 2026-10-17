"""
Playwright E2E tests for the Deck Genre section of the deck Journal
(archetype + primary, themes, kindred types) — docs/DECK_GENRE_UX_SPEC.md.

Covered flows (each test works on its own freshly seeded deck):
- set archetypes (primary + secondaries), themes (seeded + custom), kindred
  via autocomplete (alias "elves" -> "Elf"), save, reload, values persist;
- edit: promote a secondary to primary, remove a theme, add a kindred; save,
  reload, edits persist and the header strip follows;
- clear: remove every genre value, save, reload, the cleared state persists;
- kindred validation: duplicates are prevented in the UI (disabled row, live
  announcement, no second chip) and deduplicated by the server; over-32-char
  kindred is capped by the input and rejected by the API with a 400;
- a deck with no journal renders the empty state.

Not covered: a separate "note" field on Deck Genre. The UX spec deliberately has
none ("Vision is the sentence, Genre is the tags"), so there is nothing to test.

Prerequisites (same convention as tests/e2e/test_linked_accounts_e2e.py):
- the app is running at --base-url (default http://localhost:8000);
- REDIS_URL points at the SAME Redis the app uses (the fixtures seed decks and
  clean up journal keys there). Defaults to redis://localhost:6379/0.

Run:
    REDIS_URL=redis://localhost:6379/0 python -m pytest tests/e2e/test_deck_genre_e2e.py \
        --base-url=http://localhost:8000 -v
"""
import json
import os
import re
import unittest
import uuid
from urllib.parse import quote

try:
    import pytest
    import redis
    from playwright.sync_api import Page, expect
except ImportError as exc:  # pytest/playwright are E2E-only deps (not in requirements.txt)
    # The CI unit-test job runs `python -m unittest discover -s tests`, which
    # imports this module; skip it rather than failing discovery when the
    # Playwright toolchain isn't installed.
    raise unittest.SkipTest(
        "pytest/playwright not installed; install them to run the E2E tests"
    ) from exc

DECKS_KEY = "decks"
JOURNAL_PREFIX = "journal:deck:"


# --------------------------------------------------------------------------- fixtures

@pytest.fixture(scope="session")
def redis_client():
    return redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


def _seed_deck(r, name: str, source_id: int) -> dict:
    deck = {
        "id": source_id, "name": name, "color": "WBR", "commanders": ["Kaalia of the Vast"],
        "commander_uids": ["ef099f25-2b0b-4028-8490-fbc859e35f6c"], "status": "physical",
        "registry_id": f"archidekt:{source_id}", "source": "archidekt", "source_id": str(source_id),
        "cards": [{"name": "Kaalia of the Vast", "quantity": 1}, {"name": "Plains", "quantity": 5}],
    }
    decks = json.loads(r.get(DECKS_KEY) or "[]")
    decks.append(deck)
    r.set(DECKS_KEY, json.dumps(decks))
    return deck


@pytest.fixture
def make_deck(redis_client):
    """Seed a unique deck per test; remove it and its journal on teardown."""
    created = []

    def _make(label: str = "E2E Genre") -> dict:
        suffix = uuid.uuid4().hex[:8]
        deck = _seed_deck(redis_client, f"{label} {suffix}", 9_000_000 + int(suffix, 16) % 1_000_000)
        created.append(deck)
        return deck

    yield _make

    for deck in created:
        rid = deck["registry_id"]
        decks = [d for d in json.loads(redis_client.get(DECKS_KEY) or "[]") if d.get("registry_id") != rid]
        redis_client.set(DECKS_KEY, json.dumps(decks))
        redis_client.delete(JOURNAL_PREFIX + rid)


# --------------------------------------------------------------------------- page object

class GenreJournal:
    """Thin wrapper over the Deck Genre fields on /deck/{name}/journal.

    Selectors: the field containers (#f-archetype, #f-theme, #f-kindred), the
    header strip (#deckGenre), the save bar (#saveBtn / #saveError) and the
    data-testid hooks added to static/js/deck_genre.js TagField.
    """

    KINDS = ("archetype", "theme", "kindred")

    def __init__(self, page: Page, base_url: str, deck_name: str):
        self.page = page
        self.base_url = base_url
        self.deck_name = deck_name

    def open(self):
        self.page.goto(f"{self.base_url}/deck/{quote(self.deck_name)}/journal")
        self.page.wait_for_load_state("networkidle")
        expect(self.page.locator("#f-archetype [data-testid=genre-archetype-input]")).to_be_visible()
        return self

    def reload(self):
        self.page.reload()
        self.page.wait_for_load_state("networkidle")
        expect(self.page.locator("#f-archetype [data-testid=genre-archetype-input]")).to_be_visible()
        return self

    def input(self, kind: str):
        return self.page.locator(f"[data-testid=genre-{kind}-input]")

    def chips(self, kind: str):
        return self.page.locator(f"#f-{kind} [data-testid=genre-{kind}-chip]")

    def chip_values(self, kind: str) -> list:
        return self.chips(kind).evaluate_all("els => els.map(e => e.getAttribute('data-value'))")

    def primary(self):
        """Value of the primary archetype chip (the one showing the filled star), or None."""
        starred = self.page.locator("#f-archetype [data-testid=genre-archetype-chip]:has(.hud-chip-star.is-on)")
        return starred.first.get_attribute("data-value") if starred.count() else None

    def count(self, kind: str):
        return self.page.locator(f"[data-testid=genre-{kind}-count]").inner_text().strip()

    def add(self, kind: str, text: str, pick_first: bool = True):
        """Type into the field and pick the best match with Enter (what a user does)."""
        box = self.input(kind)
        box.fill(text)
        if pick_first:
            box.press("Enter")
        return self

    def remove(self, kind: str, value: str):
        chip = self.page.locator(f"#f-{kind} [data-testid=genre-{kind}-chip][data-value='{value}']")
        chip.locator(f"[data-testid=genre-{kind}-remove]").click()
        return self

    def promote(self, value: str):
        self.page.locator(f"#f-archetype [data-testid=genre-archetype-chip][data-value='{value}'] "
                          "[data-testid=genre-archetype-promote]").click()
        return self

    def save(self):
        with self.page.expect_response(
                lambda r: r.request.method == "PUT" and r.url.endswith("/journal")) as info:
            self.page.locator("#saveBtn").click()
        assert info.value.status == 200, f"journal save failed: {info.value.status}"
        return self

    def strip_text(self) -> str:
        strip = self.page.locator("#deckGenre")
        return strip.inner_text() if strip.is_visible() else ""


@pytest.fixture
def genre(page: Page, base_url: str):
    def _open(deck_name: str) -> GenreJournal:
        return GenreJournal(page, base_url, deck_name).open()
    return _open


def _api_put(page: Page, base_url: str, registry_id: str, body: dict):
    return page.request.put(
        f"{base_url}/api/decks/{quote(registry_id, safe='')}/journal",
        data=json.dumps(body), headers={"Content-Type": "application/json"})


# --------------------------------------------------------------------------- set

class TestSetGenreValues:

    def test_set_archetypes_with_primary_persist_after_reload(self, make_deck, genre):
        deck = make_deck()
        g = genre(deck["name"])
        g.add("archetype", "tokens").add("archetype", "ramp")
        assert g.chip_values("archetype") == ["tokens", "ramp"]
        assert g.primary() == "tokens"  # first add becomes primary
        g.save()

        g.reload()
        assert g.chip_values("archetype") == ["tokens", "ramp"]
        assert g.primary() == "tokens"
        assert g.count("archetype") == "2 / 4"

    def test_set_themes_seeded_and_custom_persist_after_reload(self, make_deck, genre):
        deck = make_deck()
        g = genre(deck["name"])
        g.add("theme", "Budget")  # seeded label -> stored as seed id "budget"
        g.add("theme", "Space western")  # not in the seed list -> custom
        assert g.chip_values("theme") == ["budget", "Space western"]
        g.save()

        g.reload()
        assert g.chip_values("theme") == ["budget", "Space western"]
        expect(g.chips("theme").filter(has_text="Space western")).to_have_attribute("class", re.compile("hud-chip--custom"))

    def test_kindred_autocomplete_alias_persists_as_catalog_type(self, make_deck, genre, page):
        deck = make_deck()
        g = genre(deck["name"])
        g.input("kindred").fill("elves")
        # Alias normalization: the top row is the catalog type, with a 'from "elves"' note.
        first = page.locator("[data-testid=genre-kindred-option]").first
        expect(first).to_contain_text("Elf")
        expect(first).to_contain_text("from “elves”")
        g.input("kindred").press("Enter")
        assert g.chip_values("kindred") == ["Elf"]
        g.save()

        g.reload()
        assert g.chip_values("kindred") == ["Elf"]

    def test_header_strip_shows_saved_genre(self, make_deck, genre, page):
        deck = make_deck()
        g = genre(deck["name"])
        g.add("archetype", "tokens").add("theme", "budget").add("kindred", "goblin")
        g.save()
        expect(page.locator("#deckGenre")).to_be_visible()
        assert "Tokens" in g.strip_text() and "Goblin" in g.strip_text()


# --------------------------------------------------------------------------- edit

class TestEditGenreValues:

    def test_edit_promote_remove_and_add_persist_after_reload(self, make_deck, genre):
        deck = make_deck()
        g = genre(deck["name"])
        g.add("archetype", "tokens").add("archetype", "ramp")
        g.add("theme", "budget").add("theme", "meme")
        g.add("kindred", "Elf")
        g.save()

        # Edit: make ramp the primary, drop meme, add a second kindred.
        g.promote("ramp")
        g.remove("theme", "meme")
        g.add("kindred", "goblins")  # plural -> Goblin
        assert g.primary() == "ramp"
        assert g.chip_values("archetype") == ["ramp", "tokens"]
        g.save()

        g.reload()
        assert g.primary() == "ramp"
        assert g.chip_values("archetype") == ["ramp", "tokens"]
        assert g.chip_values("theme") == ["budget"]
        assert g.chip_values("kindred") == ["Elf", "Goblin"]

    def test_removing_the_primary_promotes_the_next_archetype(self, make_deck, genre):
        deck = make_deck()
        g = genre(deck["name"])
        g.add("archetype", "tokens").add("archetype", "ramp")
        g.save()

        g.remove("archetype", "tokens")
        assert g.primary() == "ramp"
        g.save()

        g.reload()
        assert g.chip_values("archetype") == ["ramp"]
        assert g.primary() == "ramp"


# --------------------------------------------------------------------------- clear

class TestClearGenreValues:

    def test_cleared_values_stay_cleared_after_reload(self, make_deck, genre, page):
        deck = make_deck()
        g = genre(deck["name"])
        g.add("archetype", "tokens").add("theme", "budget").add("kindred", "Elf")
        g.save()
        expect(page.locator("#deckGenre")).to_be_visible()

        g.remove("archetype", "tokens")
        g.remove("theme", "budget")
        g.remove("kindred", "Elf")
        assert g.chip_values("archetype") == [] and g.chip_values("theme") == [] and g.chip_values("kindred") == []
        g.save()

        g.reload()
        for kind in ("archetype", "theme", "kindred"):
            assert g.chip_values(kind) == [], kind
            assert g.count(kind).startswith("0 /"), kind
        expect(page.locator("#deckGenre")).to_be_hidden()


# --------------------------------------------------------------------------- kindred validation

class TestKindredValidation:

    def test_duplicate_kindred_is_prevented_in_the_ui(self, make_deck, genre, page):
        deck = make_deck()
        g = genre(deck["name"])
        g.add("kindred", "Elf")
        g.save()

        # Typing the same type again: the catalog row is disabled and says so.
        g.input("kindred").fill("elf")
        dup = page.locator("[data-testid=genre-kindred-option]").first
        expect(dup).to_have_attribute("aria-disabled", "true")
        expect(dup).to_contain_text("already added")
        g.input("kindred").press("Enter")  # disabled row must not be picked
        assert g.chip_values("kindred") == ["Elf"]
        assert g.count("kindred") == "1 / 5"

        # Case/whitespace variant of the same text is blocked too (no second chip).
        g.input("kindred").fill("ELF ")
        g.input("kindred").press("Enter")
        assert g.chip_values("kindred") == ["Elf"]

        g.save()
        g.reload()
        assert g.chip_values("kindred") == ["Elf"]

    def test_server_deduplicates_case_variant_kindred(self, make_deck, page, base_url):
        deck = make_deck()
        resp = _api_put(page, base_url, deck["registry_id"],
                        {"deck_genre": {"genres": [], "kindred": ["Elf", "elf", "ELVES"]}})
        assert resp.status == 200
        assert resp.json()["journal"]["deck_genre"]["kindred"] == ["Elf"]

    def test_kindred_input_is_capped_at_32_characters(self, make_deck, genre, page):
        deck = make_deck()
        g = genre(deck["name"])
        g.input("kindred").fill("x" * 40)
        assert len(g.input("kindred").input_value()) == 32

    def test_over_long_kindred_is_rejected_by_the_server(self, make_deck, page, base_url):
        deck = make_deck()
        too_long = "y" * 33
        resp = _api_put(page, base_url, deck["registry_id"],
                        {"deck_genre": {"genres": [], "kindred": [too_long]}})
        assert resp.status == 400
        body = resp.json()
        assert body["success"] is False
        assert "longer than 32 characters" in body["error"]

    def test_too_many_kindred_types_are_rejected_by_the_server(self, make_deck, page, base_url):
        deck = make_deck()
        resp = _api_put(page, base_url, deck["registry_id"],
                        {"deck_genre": {"genres": [], "kindred": ["A", "B", "C", "D", "E", "F"]}})
        assert resp.status == 400
        assert "at most 5" in resp.json()["error"]


# --------------------------------------------------------------------------- empty state

class TestEmptyState:

    def test_deck_without_genre_data_renders_empty_state(self, make_deck, genre, page):
        deck = make_deck()  # no journal saved
        g = genre(deck["name"])
        for kind, cap in (("archetype", 4), ("theme", 5), ("kindred", 5)):
            assert g.chip_values(kind) == [], kind
            assert g.count(kind) == f"0 / {cap}", kind
        expect(page.locator("#f-archetype [data-testid=genre-archetype-input]")).to_have_attribute(
            "placeholder", re.compile(r"^Search \d+ archetypes…$"))
        expect(page.locator("#deckGenre")).to_be_hidden()
        expect(page.locator("#gn-read")).to_be_hidden()
        assert g.strip_text() == ""

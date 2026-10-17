"""
Playwright E2E tests for Auto Tag on the deck page (static/js/auto_tag.js,
templates/deck.html, POST /api/decks/{id}/auto-tag).

Covered flows (each test seeds its own deck, so nothing depends on real data):
- open a deck, click Auto Tag: card chips and deck tags appear, chips are capped;
- a manual (yours) deck tag and a kept card tag survive a re-run of Auto Tag;
- filtering the decklist by a tag shows only the matching tiles, and clears.

Known-bug test is a strict xfail so it fails loudly once fixed:
- numeric and registry deck ids address different tag stores (API split, D3).

The seeded deck carries type_line / oracle_text on each card, so the engine
needs no card-metadata keys in Redis and the expectations are deterministic.
The decklist tiles come from in_deck instance records, which are seeded too.

Prerequisites (same convention as tests/e2e/test_deck_genre_e2e.py):
- the app is running at --base-url (default http://localhost:8000);
- REDIS_URL points at the SAME Redis the app uses.

Run:
    REDIS_URL=redis://localhost:6379/0 python -m pytest tests/e2e/test_auto_tag_e2e.py \\
        --base-url=http://127.0.0.1:8000 -v
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
except ImportError as exc:  # pytest/playwright are E2E-only deps (not in requirements.txt)
    raise unittest.SkipTest(
        "pytest/playwright not installed; install them to run the E2E tests"
    ) from exc

DECKS_KEY = "decks"
SEED_ID_BASE = 9_200_000
MAX_CHIPS_PER_TILE = 3

# name, quantity, type_line, oracle_text. The expected tags below were checked
# against the live engine (autotag-v1) with this exact seed.
SEED_CARDS = [
    ("Sol Ring", 1, "Artifact", "{T}: Add {C}{C}."),
    ("Arcane Signet", 1, "Artifact", "{1}, {T}: Add one mana of any color in your commander's color identity."),
    ("Cultivate", 1, "Sorcery", "Search your library for up to two basic land cards, reveal them, put one onto the battlefield tapped and the other into your hand, then shuffle."),
    ("Lightning Bolt", 1, "Instant", "Lightning Bolt deals 3 damage to any target."),
    ("Swords to Plowshares", 1, "Instant", "Exile target creature. Its controller gains life equal to its power."),
    ("Counterspell", 1, "Instant", "Counter target spell."),
    ("Brainstorm", 1, "Instant", "Draw three cards, then put two cards from your hand on top of your library in any order."),
    ("Rhystic Study", 1, "Enchantment", "Whenever an opponent casts a spell, you may draw a card unless that player pays {1}."),
    ("Wrath of God", 1, "Sorcery", "Destroy all creatures. They can't be regenerated."),
    ("Demonic Tutor", 1, "Sorcery", "Search your library for a card, put that card into your hand, then shuffle."),
    ("Opt", 1, "Instant", "Scry 1. Draw a card."),
    ("Mystic Remora", 1, "Enchantment", "Cumulative upkeep {1}. Whenever an opponent casts a noncreature spell, you may draw a card unless that player pays the cumulative upkeep cost."),
    ("Plains", 5, "Basic Land — Plains", "({T}: Add {W}.)"),
]
SEED_COMMANDER = {
    "name": "Vivi Ornitier", "quantity": 1, "type_line": "Legendary Creature — Wizard",
    "oracle_text": "{0}: Until end of turn, you may ... (test fixture)",
}
EXPECTED_RAMP = {"Sol Ring", "Arcane Signet", "Cultivate"}
EXPECTED_REMOVAL = {"Lightning Bolt", "Swords to Plowshares"}


# --------------------------------------------------------------------------- fixtures

@pytest.fixture(scope="session")
def redis_client():
    return redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)


def _instance_prefix(source_id: int) -> str:
    return f"e2e-at-{source_id}-"


def _seed_deck(r, name: str, source_id: int) -> dict:
    cards = [
        {"name": n, "quantity": q, "type_line": tl, "oracle_text": ot}
        for (n, q, tl, ot) in SEED_CARDS
    ]
    deck = {
        "id": source_id, "name": name, "color": "WU", "commanders": [SEED_COMMANDER["name"]],
        "commander_uids": [f"e2e-{source_id}"], "status": "physical",
        "registry_id": f"archidekt:{source_id}", "source": "archidekt", "source_id": str(source_id),
        "cards": cards, "commander_cards": [SEED_COMMANDER],
    }
    decks = json.loads(r.get(DECKS_KEY) or "[]")
    decks.append(deck)
    r.set(DECKS_KEY, json.dumps(decks))

    # The decklist is built from instance records (/api/inventory), not from
    # deck["cards"], so every copy also needs an in_deck instance.
    n = 0
    for (card_name, qty, _tl, _ot) in SEED_CARDS:
        for _ in range(qty):
            n += 1
            iid = f"{_instance_prefix(source_id)}{n}"
            r.set(f"instance:{iid}", json.dumps({
                "id": iid, "card_name": card_name, "set": "", "set_name": "", "collector_number": "",
                "finish": "nonfoil", "condition": "NM", "language": "EN",
                "ownership_status": "in_deck", "deck_id": deck["registry_id"], "deck_name": name,
                "considered_for_deck": None, "price_paid": 0.0, "source": "e2e", "date_ordered": "",
                "date_acquired": "", "archidekt_uid": "", "notes": "", "updated_at": "",
                "history": [{"status": "in_deck", "at": "e2e"}],
            }))
    return deck


def _purge_deck_keys(r, rid: str, source_id: int):
    for key in (f"deck_tags:{rid}", f"deck_card_tags:{rid}", f"deck_tags_version:{rid}"):
        r.delete(key)
    for key in r.scan_iter(match=f"deck_card_tags_version:{rid}:*"):
        r.delete(key)
    for key in r.scan_iter(match=f"deck_tags_version:{rid}*"):
        r.delete(key)
    for key in r.scan_iter(match=f"instance:{_instance_prefix(source_id)}*"):
        r.delete(key)


@pytest.fixture
def make_deck(redis_client):
    """Seed a unique deck per test; remove it, its instances and tag keys on teardown."""
    created = []

    def _make(label: str = "E2E AutoTag") -> dict:
        suffix = uuid.uuid4().hex[:8]
        deck = _seed_deck(redis_client, f"{label} {suffix}", SEED_ID_BASE + int(suffix, 16) % 90_000)
        created.append(deck)
        return deck

    yield _make

    for deck in created:
        rid = deck["registry_id"]
        decks = [d for d in json.loads(redis_client.get(DECKS_KEY) or "[]") if d.get("registry_id") != rid]
        redis_client.set(DECKS_KEY, json.dumps(decks))
        _purge_deck_keys(redis_client, rid, deck["id"])


# --------------------------------------------------------------------------- page object

class DeckAutoTag:
    """Thin wrapper over the Auto Tag strip, decklist tiles and card modal.

    Selectors come from templates/deck.html and static/js/auto_tag.js:
    #autoTag strip, [data-at-run] button, .at-status, .at-fchip / [data-at-filter]
    filter chips, figure[data-card-name] tiles inside .deck-card-grid, and the
    #atCardTags editor inside #cardModal. A chip's title is "<Label> · auto|yours".
    """

    def __init__(self, page: Page, base_url: str, deck: dict):
        self.page = page
        self.base_url = base_url
        self.deck = deck

    @property
    def path(self) -> str:
        return f"/deck/{quote(self.deck['name'])}"

    def open(self, query: str = ""):
        self.page.goto(f"{self.base_url}{self.path}{query}")
        self.page.wait_for_load_state("networkidle")
        expect(self.strip().get_by_role("button", name="Auto Tag")).to_be_visible()
        return self

    def strip(self):
        return self.page.locator("#autoTag")

    def run_auto_tag(self, rerun: bool = False):
        """Click Auto Tag. On a re-run the confirm dialog must appear and is confirmed."""
        self.strip().locator("[data-at-run]").first.click()
        if rerun:
            go = self.page.locator("[data-at-confirm-go]")
            expect(go).to_be_visible()
            go.click()
        expect(self.strip().locator(".at-status.is-ok")).to_be_visible(timeout=15_000)
        return self

    def deck_chip_labels(self, source: str | None = None) -> list[str]:
        """Labels of the deck-level chips, e.g. ['Spellslinger'] (title is '<Label> · <source>')."""
        sel = "[aria-labelledby=atDeckL] .at-chip" + (".at-chip--user" if source == "user" else "")
        titles = self.strip().locator(sel).evaluate_all("els => els.map(e => e.title)")
        return [t.rsplit(" · ", 1)[0] for t in titles]

    def tile(self, name: str):
        return self.page.locator(f".deck-card-grid figure[data-card-name={json.dumps(name)}]").first

    def tile_titles(self, name: str) -> list[str]:
        """Chip titles on a decklist tile, e.g. ['Ramp · auto']."""
        return self.tile(name).locator(".at-chip").evaluate_all("els => els.map(e => e.title)")

    def visible_tile_names(self) -> list[str]:
        return self.page.locator(".deck-card-grid figure[data-card-name]:visible").evaluate_all(
            "els => els.map(e => e.dataset.cardName)"
        )

    def all_tile_chip_counts(self) -> list[int]:
        return self.page.locator(".deck-card-grid figure[data-card-name]").evaluate_all(
            "els => els.map(e => e.querySelectorAll('.at-chip').length)"
        )

    def open_card_editor(self, name: str):
        self.tile(name).click()
        expect(self.page.locator("#atCardTags [data-at-chips]")).to_be_visible()
        return self.page.locator("#atCardTags")

    def close_modal(self):
        self.page.keyboard.press("Escape")
        expect(self.page.locator(".hud-modal.is-open")).to_have_count(0)


# --------------------------------------------------------------------------- tests

class TestRunAutoTag:
    def test_run_shows_card_chips_and_deck_tags(self, page: Page, base_url: str, make_deck):
        at = DeckAutoTag(page, base_url, make_deck("E2E AutoTag run")).open()
        assert at.deck_chip_labels() == [], "fresh deck should have no deck tags"

        at.run_auto_tag()

        # Deck-level tag from the engine, and the role counts strip.
        assert any("Spellslinger" in t for t in at.deck_chip_labels()), at.deck_chip_labels()
        expect(at.strip().locator(".at-counts")).to_contain_text("Ramp")

        # Card chips: the engine's tags are marked auto, not yours.
        for name in EXPECTED_RAMP:
            assert "Ramp · auto" in at.tile_titles(name), (name, at.tile_titles(name))
        for name in EXPECTED_REMOVAL:
            assert "Removal · auto" in at.tile_titles(name), (name, at.tile_titles(name))

        # Conservatism: no tile shows more chips than the per-tile maximum.
        counts = at.all_tile_chip_counts()
        assert counts and max(counts) <= MAX_CHIPS_PER_TILE, counts

    def test_basic_lands_stay_untagged(self, page: Page, base_url: str, make_deck):
        at = DeckAutoTag(page, base_url, make_deck("E2E AutoTag basics")).open()
        at.run_auto_tag()
        assert at.tile_titles("Plains") == []


class TestManualTagsSurviveRerun:
    def test_user_deck_tag_and_pinned_card_tag_persist(self, page: Page, base_url: str, make_deck):
        at = DeckAutoTag(page, base_url, make_deck("E2E AutoTag keep")).open()
        at.run_auto_tag()
        strip = at.strip()

        # Add a deck-level tag by hand. The engine does not produce Voltron for
        # this deck, so it can only be a user tag.
        strip.locator("[data-at-toggle]").click()
        strip.locator("[data-at-add='voltron']").click()
        expect(strip.locator("[aria-labelledby=atDeckL] .at-chip--user")).to_contain_text("Voltron")

        # Keep Sol Ring's auto Ramp as yours (◇ -> ◆) from the card editor.
        editor = at.open_card_editor("Sol Ring")
        editor.locator("[data-at-pin]").first.click()
        expect(editor.locator(".at-chip--user")).to_contain_text("Ramp")
        at.close_modal()

        # Re-run: the dialog must say manual tags are kept, then the run completes.
        at.run_auto_tag(rerun=True)

        assert "Voltron" in at.deck_chip_labels(source="user"), at.deck_chip_labels()
        assert "Ramp · yours" in at.tile_titles("Sol Ring"), at.tile_titles("Sol Ring")
        # Engine-owned tags on other cards are still auto after the re-run.
        assert "Removal · auto" in at.tile_titles("Lightning Bolt"), at.tile_titles("Lightning Bolt")

    def test_user_deck_tag_survives_page_reload(self, page: Page, base_url: str, make_deck):
        at = DeckAutoTag(page, base_url, make_deck("E2E AutoTag reload")).open()
        at.run_auto_tag()
        strip = at.strip()
        strip.locator("[data-at-toggle]").click()
        strip.locator("[data-at-add='lifegain']").click()
        expect(strip.locator("[aria-labelledby=atDeckL] .at-chip--user")).to_contain_text("Lifegain")

        page.reload()
        page.wait_for_load_state("networkidle")
        expect(at.strip().locator("[aria-labelledby=atDeckL] .at-chip--user")).to_contain_text("Lifegain")


class TestFilterByTag:
    def test_filter_shows_only_matching_tiles_and_clears(self, page: Page, base_url: str, make_deck):
        at = DeckAutoTag(page, base_url, make_deck("E2E AutoTag filter")).open()
        at.run_auto_tag()
        all_visible = len(at.visible_tile_names())

        page.locator(".at-filterbar [data-at-filter='ramp']").click()
        expect(page).to_have_url(f"{base_url}{at.path}?tag=ramp")
        shown = at.visible_tile_names()
        assert EXPECTED_RAMP <= set(shown), shown
        assert "Lightning Bolt" not in shown and "Plains" not in shown, shown
        assert len(shown) < all_visible

        page.locator("[data-at-clear]").click()
        assert len(at.visible_tile_names()) == all_visible
        expect(page).not_to_have_url(f"{base_url}{at.path}?tag=ramp")

    def test_untagged_filter_shows_only_untagged_tiles(self, page: Page, base_url: str, make_deck):
        at = DeckAutoTag(page, base_url, make_deck("E2E AutoTag untagged")).open()
        at.run_auto_tag()
        all_visible = len(at.visible_tile_names())
        page.locator(".at-filterbar .at-fchip", has_text="Untagged").first.click()
        shown = at.visible_tile_names()
        assert 0 < len(shown) < all_visible
        for name in shown:
            assert at.tile_titles(name) == [], name


class TestApiIdentity:
    @pytest.mark.xfail(strict=True, reason="auto-tag writes under the raw path id; numeric and registry ids split the tag store")
    def test_numeric_and_registry_ids_address_the_same_tags(self, base_url: str, make_deck):
        import urllib.request

        deck = make_deck("E2E AutoTag ids")

        def call(path, method="GET"):
            req = urllib.request.Request(base_url + path, method=method)
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())

        call(f"/api/decks/{deck['id']}/auto-tag", "POST")
        via_numeric = call(f"/api/decks/{deck['id']}/tags")["tags"]
        via_registry = call(f"/api/decks/{quote(deck['registry_id'], safe='')}/tags")["tags"]
        assert via_numeric == via_registry

"""Archidekt one-way importer (docs/ARCHITECTURE.md §8 Slice 1).

Imports ONE Archidekt deck into Postgres: card mirror rows (from Scryfall —
all card facts come from the DB or Scryfall, AGENT_RULES §3), the deck row,
its deck_cards (boards + categories preserved), and the first
profile_snapshots row computed from the imported deck + collection
(trigger="import").

One-way: this module never writes to Archidekt. Idempotent: re-importing the
same deck upserts cards, rebuilds deck_cards, and appends a fresh snapshot
(snapshots are history — appending on every import is the design).

CLI:
    python -m src.server.workers.importer <archidekt deck id or URL>
(DATABASE_URL required; use --no-snapshot to skip the profile snapshot.)
"""
from __future__ import annotations

import datetime
import sys
from typing import Callable, Optional

import requests

import src.server.domain.decks.deck_slots as deck_slots
from src.server.domain.decks.deck_slots import DeckSlot
from src.server.domain.inventory import have_buy as have_buy_math
from src.server.domain.profile import snapshot as profile_snapshot
from src.server.infra.db import repositories as repo
from src.server.infra.db.models import STRUCTURAL_CATEGORIES
from src.server.infra.db.session import session_scope
from src.server.infra.providers import archidekt

# board enum (docs/ARCHITECTURE.md §4.2) per DeckSlot; commander is a board of
# its own here even though a commander also stays in the 100-card main list on
# the Archidekt side.
_SLOT_TO_BOARD = {
    DeckSlot.MAINBOARD: "main",
    DeckSlot.MAYBEBOARD: "maybe",
    DeckSlot.CONSIDERING: "considering",
    DeckSlot.PLANAR_DECK: "maybe",
    DeckSlot.SCHEME_DECK: "maybe",
    DeckSlot.ATTRACTION_DECK: "maybe",
}

_SCRYFALL_COLLECTION_URL = "https://api.scryfall.com/cards/collection"
_SCRYFALL_BATCH = 75  # Scryfall /cards/collection cap


class ImportError_(ValueError):
    """Raised for unimportable decks (missing oracle ids, empty cardlist)."""


def _primary_category(categories: list) -> Optional[str]:
    """First non-structural Archidekt category — the deck_card.category.

    Structural categories (Commander/Maybeboard/…) are preserved as the
    board, never as the thematic label (AGENT_RULES §9)."""
    for c in categories or []:
        if c not in STRUCTURAL_CATEGORIES:
            return c
    return None


def parse_deck_payload(raw: dict) -> dict:
    """Map a raw Archidekt deck payload to importer-internal shapes.

    Returns {"name", "archidekt_id", "commanders", "color_identity",
             "deck_cards", "printings", "missing_oracle_ids"}.
    deck_cards rows: {"oracle_id", "name", "board", "qty", "category"}.
    """
    deck_cards = []
    printings = []
    commanders = []
    color_letters = set()
    missing = []
    color_name_to_letter = archidekt._COLOR_NAME_TO_LETTER

    for entry in raw.get("cards", []):
        card = entry.get("card") or {}
        oracle = card.get("oracleCard") or {}
        name = oracle.get("name")
        if not name:
            continue
        oracle_id = oracle.get("uid")
        if not oracle_id:
            missing.append(name)
            continue
        categories = entry.get("categories") or []
        qty = int(entry.get("quantity") or 1)
        slot, _remapped = deck_slots.classify_card_slot(categories, oracle.get("types"))
        board = _SLOT_TO_BOARD.get(slot, "main")
        if "Commander" in categories:
            commanders.append(name)
            # A commander lives on the commander board ONLY — the 100-card
            # deck is commander(1-2) + main(99/98). Keeping it out of `main`
            # is what makes have/buy require exactly one copy.
            deck_cards.append({"oracle_id": oracle_id, "name": name,
                               "board": "commander", "qty": qty,
                               "category": _primary_category(categories)})
        else:
            deck_cards.append({"oracle_id": oracle_id, "name": name, "board": board,
                               "qty": qty, "category": _primary_category(categories)})
        for color_name in oracle.get("colorIdentity") or []:
            letter = color_name_to_letter.get(color_name)
            if letter:
                color_letters.add(letter)
        printing_id = card.get("uid")
        if printing_id:
            printings.append({
                "scryfall_id": printing_id, "oracle_id": oracle_id,
                "set_code": (card.get("edition") or {}).get("editioncode"),
                "collector_number": str(card.get("collectorNumber") or "") or None,
            })

    color_identity = [c for c in "WUBRG" if c in color_letters]
    return {
        "name": raw.get("name", f"Archidekt Deck {raw.get('id')}"),
        "archidekt_id": str(raw.get("id")),
        "commanders": commanders,
        "color_identity": color_identity,
        "deck_cards": deck_cards,
        "printings": printings,
        "missing_oracle_ids": missing,
    }


def fetch_scryfall_cards(oracle_ids: list) -> dict:
    """oracle_id -> Scryfall card dict, batched via /cards/collection.

    Returns only found cards; callers surface the missing set explicitly
    (AGENT_RULES §4 — never silently zero)."""
    found = {}
    ids = list(dict.fromkeys(oracle_ids))
    for i in range(0, len(ids), _SCRYFALL_BATCH):
        batch = ids[i:i + _SCRYFALL_BATCH]
        res = requests.post(
            _SCRYFALL_COLLECTION_URL,
            json={"identifiers": [{"oracle_id": oid} for oid in batch]},
            headers={"User-Agent": "commander-lab/1.0", "Accept": "application/json"},
            timeout=30,
        )
        res.raise_for_status()
        payload = res.json()
        for card in payload.get("data", []):
            found[card["oracle_id"]] = card
    return found


def _card_row_from_scryfall(card: dict) -> dict:
    faces = card.get("card_faces") or []
    oracle_text = card.get("oracle_text")
    if oracle_text is None and faces:
        oracle_text = "\n//\n".join(f.get("oracle_text", "") for f in faces)
    return {
        "oracle_id": card["oracle_id"],
        "name": card.get("name"),
        "type_line": card.get("type_line"),
        "oracle_text": oracle_text,
        "colors": card.get("colors") or card.get("color_identity") or [],
        "cmc": card.get("cmc"),
        "keywords": card.get("keywords") or [],
        "legalities": card.get("legalities") or {},
        "image_uri": (card.get("image_uris") or {}).get("normal")
        or ((faces[0].get("image_uris") or {}).get("normal") if faces else None),
        "refreshed_at": datetime.datetime.now(datetime.timezone.utc),
    }


def import_deck(session, identifier: str, *,
                fetch_raw: Optional[Callable] = None,
                fetch_cards: Optional[Callable] = None,
                snapshot: bool = True,
                status: str = "virtual") -> dict:
    """Import one Archidekt deck into Postgres. Returns a summary dict.

    ``session`` is the caller's unit of work (session_scope commits). Card
    facts missing on Scryfall are imported with name/type only and reported
    under "unresolved_cards" — the deck still imports (unavailable, not 0).

    Fetchers default lazily so tests can monkeypatch the module attributes.
    """
    fetch_raw = fetch_raw or archidekt.fetch_deck_raw
    fetch_cards = fetch_cards or fetch_scryfall_cards
    raw = fetch_raw(identifier)
    parsed = parse_deck_payload(raw)
    if not parsed["deck_cards"]:
        raise ImportError_(f"no importable cards in Archidekt deck {identifier!r}")

    oracle_ids = [c["oracle_id"] for c in parsed["deck_cards"]]
    known = repo.get_cards(session, oracle_ids)
    need = [oid for oid in dict.fromkeys(oracle_ids) if oid not in known]
    unresolved = []
    if need:
        fetched = fetch_cards(need)
        for oid in need:
            card = fetched.get(oid)
            if card:
                repo.upsert_card(session, _card_row_from_scryfall(card))
            else:
                unresolved.append(oid)
    if unresolved:
        # Name/type-only rows so FK integrity holds; cmc stays NULL and the
        # profile curve reports these as unavailable (AGENT_RULES §4).
        names = {c["oracle_id"]: c["name"] for c in parsed["deck_cards"]}
        for oid in unresolved:
            repo.upsert_card(session, {"oracle_id": oid, "name": names.get(oid) or oid})
    for p in parsed["printings"]:
        repo.upsert_printing(session, p)

    deck = repo.upsert_imported_deck(
        session,
        archidekt_id=parsed["archidekt_id"],
        name=parsed["name"],
        status=status,
        color_identity=parsed["color_identity"],
        commanders=parsed["commanders"],
        source="archidekt",
        deck_cards=parsed["deck_cards"],
    )

    result = {
        "deck_id": deck.id,
        "archidekt_id": parsed["archidekt_id"],
        "name": deck.name,
        "fingerprint": deck.fingerprint,
        "deck_cards": len(parsed["deck_cards"]),
        "commanders": parsed["commanders"],
        "unresolved_cards": sorted(unresolved),
        "missing_oracle_ids": sorted(parsed["missing_oracle_ids"]),
        "snapshot_id": None,
    }

    if snapshot:
        snap = compute_import_snapshot(session, deck.id)
        result["snapshot_id"] = snap["id"]
    return result


def compute_import_snapshot(session, deck_id: int) -> dict:
    """Compute + append the profile snapshot for an imported deck
    (trigger="import", append-only per §4.2)."""
    deck = repo.get_deck(session, deck_id)
    deck_cards = repo.deck_cards_for(session, deck_id)
    instances = repo.instances_for_deck(session, deck_id) + repo.collection_instances(session)
    split = have_buy_math.compute_have_buy(deck_cards, instances, deck_id)
    metrics = profile_snapshot.compute_metrics(deck, deck_cards, split)
    return repo.append_snapshot(
        session,
        trigger="import",
        inputs=profile_snapshot.build_inputs(deck, "import"),
        metrics=metrics,
        summary_text=profile_snapshot.render_summary(deck, metrics),
    )


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(__doc__)
        return 2
    no_snapshot = "--no-snapshot" in argv
    identifiers = [a for a in argv if not a.startswith("--")]
    if not identifiers:
        print(__doc__)
        return 2
    with session_scope() as session:
        for identifier in identifiers:
            result = import_deck(session, identifier, snapshot=not no_snapshot)
            print(f"imported deck {result['deck_id']} ({result['name']}): "
                  f"{result['deck_cards']} deck_cards, "
                  f"snapshot={result['snapshot_id']}, "
                  f"unresolved={len(result['unresolved_cards'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

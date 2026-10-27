"""Archidekt one-way import orchestration (ARCHITECTURE.md §0: after import,
Commander Lab owns the deck).

Wires provider (HTTP) -> domain normalizer (pure) -> repositories (writes)
-> domain have/buy + profile snapshot. One transaction per import: cards,
printings, deck, deck_cards and the profile snapshot commit or roll back
together.

Idempotent: the deck is keyed by registry id (``archidekt:{id}``) and cards
are upserts; a re-import of an unchanged deck (same fingerprint) writes the
same rows and appends NO new profile snapshot.
"""
from __future__ import annotations

from typing import Callable, Optional

from sqlalchemy.orm import sessionmaker

from src.server.domain.decks import deck_import
from src.server.domain.decks import reconciliation_preview as reconcile
from src.server.domain.inventory import have_buy as have_buy_mod
from src.server.domain.profile import power as power_mod
from src.server.domain.profile import snapshot as snapshot_mod
from src.server.infra.db import repositories as repo
from src.server.infra.db.session import get_session_factory, session_scope
from src.server.infra.providers import archidekt as archidekt_provider


def import_archidekt_deck(
    identifier: str,
    session_factory: Optional[sessionmaker] = None,
    fetcher: Optional[Callable[[str], dict]] = None,
) -> dict:
    """Import one Archidekt deck (id or URL) into Postgres.

    Returns a summary dict (deck id/name/status, card counts, created/changed,
    fingerprint, snapshot id when one was appended, have/buy totals, parse
    flags). Raises deck_import.DeckImportError for unusable payloads and the
    fetcher's errors (requests.HTTPError) for upstream failures.
    """
    fetch = fetcher or archidekt_provider.fetch_deck_raw
    payload = fetch(identifier)
    normalized = deck_import.normalize_archidekt_deck(payload)

    factory = session_factory or get_session_factory()
    with session_scope(factory) as session:
        for card in normalized["cards"]:
            repo.upsert_card(session, card["card"])
            if card["printing"].get("scryfall_id"):
                repo.upsert_printing(session, card["printing"])

        existing = repo.get_deck(session, normalized["id"])
        created = existing is None
        changed = created or existing.get("fingerprint") != normalized["fingerprint"]

        if not created and normalized["status"] != "have":
            # Re-import of a known deck outside the physical folder: the
            # folder rule carries no information over the deck's lifecycle
            # state (pending/possible are user-driven, Slice 2 migration
            # states), so it never downgrades them. Folder membership CAN
            # still promote a deck to "have" (and a deck that left the
            # physical folder drops from "have" to "virtual").
            if existing.get("status") in ("pending", "possible"):
                normalized = {**normalized, "status": existing["status"]}

        repo.upsert_deck(session, normalized)
        rows_written = repo.replace_deck_cards(
            session, normalized["id"], normalized["cards"]
        )

        # Claim already-migrated collection instances the Redis registry had
        # assigned to this deck (migration may run before the deck import).
        for instance_id, deck_id in repo.rebind_instances(session, {normalized["id"]}):
            repo.insert_event(session, {
                "instance_id": instance_id,
                "kind": "assigned",
                "to_deck": deck_id,
            })

        deck_cards = repo.get_deck_cards(session, normalized["id"])
        instances = repo.list_instances(
            session, oracle_ids={c["oracle_id"] for c in deck_cards}
        )
        report = have_buy_mod.compute_have_buy(deck_cards, instances, normalized["id"])

        snapshot_id = None
        if changed:
            power = power_mod.estimate_power(deck_cards)
            snap = snapshot_mod.build_snapshot(
                normalized, deck_cards, report, repo.collection_stats(session),
                power=power,
            )
            snapshot_id = repo.append_snapshot(
                session, "import", snap["inputs"], snap["metrics"], snap["summary_text"]
            )

    return {
        "deck_id": normalized["id"],
        "archidekt_id": normalized["archidekt_id"],
        "name": normalized["name"],
        "status": normalized["status"],
        "commanders": normalized["commanders"],
        "color_identity": normalized["color_identity"],
        "rows": rows_written,
        "card_qty": sum(c["qty"] for c in normalized["cards"]),
        "distinct_cards": len(normalized["cards"]),
        "created": created,
        "changed": changed,
        "fingerprint": normalized["fingerprint"],
        "snapshot_id": snapshot_id,
        "have_buy": report["totals"],
        "parse_flags": normalized["parse_flags"],
    }


def preview_archidekt_deck(
    identifier: str,
    session_factory: Optional[sessionmaker] = None,
    fetcher: Optional[Callable[[str], dict]] = None,
) -> dict:
    """Read-only reconciliation preview for one Archidekt deck.

    Fetches the raw source payload and reads the locally matched deck (by
    stable provider identity), then builds the domain diff. Writes nothing:
    the session is used read-only and closed without commit, no upserts,
    no snapshot, no instance claims, no Archidekt writes. Re-running the
    preview with the same source and local state returns the same result.
    """
    fetch = fetcher or archidekt_provider.fetch_deck_raw
    payload = fetch(identifier)
    source_id = archidekt_provider.parse_identifier(identifier)

    factory = session_factory or get_session_factory()
    with factory() as session:  # read-only: no commit on close
        local_deck = repo.get_deck(session, reconcile.registry_identity(source_id))
        if local_deck is None:
            local_deck = repo.get_deck_by_archidekt_id(session, source_id)
        local_cards = (
            repo.get_deck_cards(session, local_deck["id"]) if local_deck else []
        )

    return reconcile.preview_deck_refresh(payload, local_deck, local_cards)


def deck_detail(session, deck_id: str) -> Optional[dict]:
    """Full read model for the deck page: deck + cards + have/buy + snapshot."""
    deck = repo.get_deck(session, deck_id)
    if deck is None:
        return None
    deck_cards = repo.get_deck_cards(session, deck_id)
    instances = repo.list_instances(
        session, oracle_ids={c["oracle_id"] for c in deck_cards}
    )
    report = have_buy_mod.compute_have_buy(deck_cards, instances, deck_id)
    line_by_card = {
        (l["oracle_id"], l["board"]): l for l in report["lines"]
    }
    boards: dict = {}
    for card in deck_cards:
        line = line_by_card.get((card["oracle_id"], card["board"]))
        boards.setdefault(card["board"], []).append({
            **card,
            "have": line["have"] if line else 0,
            "pending": line["pending"] if line else 0,
            "buy": line["buy"] if line else 0,
            "elsewhere": line["elsewhere"] if line else 0,
        })
    snapshot = repo.latest_snapshot(session)
    return {
        "deck": deck,
        "boards": boards,
        "have_buy": report,
        "snapshot": snapshot,
    }

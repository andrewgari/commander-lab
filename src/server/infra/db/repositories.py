"""Persistence boundary for the Slice-1 tables (boundary rule 4).

All writes to cards/printings/decks/deck_cards/card_instances/instance_events/
profile_snapshots go through these functions. Routers, workers and scripts
hand in a SQLAlchemy Session and plain dicts; they never write SQL.

Read helpers return plain dicts at the boundary so callers (domain math,
pydantic serialization) never see ORM objects.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from typing import Any, Iterable, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from src.server.infra.db.models import (
    CardInstanceRow,
    CardRow,
    DeckCardRow,
    DeckRow,
    InstanceEventRow,
    PrintingRow,
    ProfileSnapshotRow,
)

# ---------------------------------------------------------------------------
# cards / printings (Scryfall mirror)
# ---------------------------------------------------------------------------


def upsert_card(session: Session, card: dict) -> None:
    """Insert or refresh an oracle-level card row. ``card`` keys: oracle_id,
    name, type_line, oracle_text, colors, cmc, keywords, legalities, image_uri,
    etag, refreshed_at (missing keys leave existing values on conflict)."""
    oracle_id = card["oracle_id"]
    row = session.get(CardRow, oracle_id)
    values = {k: v for k, v in card.items() if k != "oracle_id" and v is not None}
    if row is None:
        session.add(CardRow(oracle_id=oracle_id, **values))
    else:
        for k, v in values.items():
            setattr(row, k, v)


def upsert_printing(session: Session, printing: dict) -> None:
    """Insert or refresh a printing row. Keys: scryfall_id, oracle_id,
    set_code, collector_number, rarity, prices."""
    scryfall_id = printing["scryfall_id"]
    row = session.get(PrintingRow, scryfall_id)
    values = {k: v for k, v in printing.items() if k != "scryfall_id" and v is not None}
    if row is None:
        session.add(PrintingRow(scryfall_id=scryfall_id, **values))
    else:
        for k, v in values.items():
            setattr(row, k, v)


def get_cards(session: Session, oracle_ids: Iterable[str]) -> dict:
    """oracle_id -> card row dict for every known id (missing ids omitted)."""
    ids = list(dict.fromkeys(oracle_ids))
    if not ids:
        return {}
    rows = session.scalars(select(CardRow).where(CardRow.oracle_id.in_(ids))).all()
    return {r.oracle_id: _card_dict(r) for r in rows}


def _card_dict(r: CardRow) -> dict:
    return {
        "oracle_id": r.oracle_id,
        "name": r.name,
        "type_line": r.type_line,
        "oracle_text": r.oracle_text,
        "colors": list(r.colors or []),
        "cmc": r.cmc,
        "keywords": list(r.keywords or []),
        "legalities": dict(r.legalities or {}),
        "image_uri": r.image_uri,
        "refreshed_at": r.refreshed_at.isoformat() if r.refreshed_at else None,
    }


# ---------------------------------------------------------------------------
# decks / deck_cards
# ---------------------------------------------------------------------------


def deck_fingerprint(deck_cards: list) -> str:
    """Content hash over (oracle_id, board, qty, category) — used to skip
    no-op re-imports and to detect drift. Deterministic."""
    items = sorted(
        (c["oracle_id"], c["board"], int(c["qty"]), c.get("category") or "")
        for c in deck_cards
    )
    return hashlib.sha256(json.dumps(items).encode()).hexdigest()[:16]


def upsert_imported_deck(
    session: Session,
    *,
    archidekt_id: Optional[str],
    name: str,
    status: str,
    color_identity: list,
    commanders: list,
    source: str,
    deck_cards: list,
) -> DeckRow:
    """Insert or update a deck and REPLACE its deck_cards in the same unit of
    work (the caller's session commits). Re-importing the same Archidekt deck
    is idempotent: cards are upserted, deck_cards are fully rebuilt.

    ``deck_cards`` rows: {"oracle_id", "board", "qty", "category"}."""
    deck = None
    if archidekt_id:
        deck = session.scalars(
            select(DeckRow).where(DeckRow.archidekt_id == str(archidekt_id))
        ).first()
    if deck is None:
        deck = DeckRow(name=name, archidekt_id=str(archidekt_id) if archidekt_id else None,
                       status=status, source=source)
        session.add(deck)
        session.flush()
    deck.name = name
    deck.status = status
    deck.color_identity = list(color_identity)
    deck.commanders = list(commanders)
    deck.source = source
    deck.fingerprint = deck_fingerprint(deck_cards)

    session.execute(delete(DeckCardRow).where(DeckCardRow.deck_id == deck.id))
    for c in deck_cards:
        session.add(DeckCardRow(
            deck_id=deck.id, oracle_id=c["oracle_id"], board=c["board"],
            qty=int(c["qty"]), category=c.get("category"),
        ))
    session.flush()
    return deck


def get_deck(session: Session, deck_id: int) -> Optional[dict]:
    row = session.get(DeckRow, deck_id)
    return _deck_dict(row) if row else None


def list_decks(session: Session, include_archived: bool = False) -> list:
    q = select(DeckRow).order_by(DeckRow.name)
    if not include_archived:
        q = q.where(DeckRow.archived_at.is_(None))
    return [_deck_dict(r) for r in session.scalars(q).all()]


def _deck_dict(r: DeckRow) -> dict:
    return {
        "id": r.id,
        "name": r.name,
        "archidekt_id": r.archidekt_id,
        "status": r.status,
        "color_identity": list(r.color_identity or []),
        "commanders": list(r.commanders or []),
        "source": r.source,
        "fingerprint": r.fingerprint,
        "archidekt_url": f"https://archidekt.com/decks/{r.archidekt_id}" if r.archidekt_id else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        "archived_at": r.archived_at.isoformat() if r.archived_at else None,
    }


def deck_cards_for(session: Session, deck_id: int) -> list:
    """Flat deck-card rows joined with card facts, ordered by board then name."""
    q = (
        select(DeckCardRow, CardRow)
        .join(CardRow, CardRow.oracle_id == DeckCardRow.oracle_id)
        .where(DeckCardRow.deck_id == deck_id)
        .order_by(DeckCardRow.board, CardRow.name)
    )
    out = []
    for dc, card in session.execute(q).all():
        out.append({
            "oracle_id": dc.oracle_id,
            "name": card.name,
            "board": dc.board,
            "qty": dc.qty,
            "category": dc.category,
            "type_line": card.type_line,
            "cmc": card.cmc,
            "colors": list(card.colors or []),
            "image_uri": card.image_uri,
        })
    return out


# ---------------------------------------------------------------------------
# card_instances / instance_events
# ---------------------------------------------------------------------------


def insert_instance(session: Session, instance: dict, *, event_ts=None) -> bool:
    """Insert one physical copy + its seeding provenance events.

    Idempotent on instance id (returns False when the id already exists).
    ``instance`` keys: id, oracle_id, status, deck_id?, finish, condition,
    printing_id?, price_paid?, acquired_at?, notes?.
    Seeding events: ``acquired`` always; ``assigned`` when status=in_deck.
    """
    if session.get(CardInstanceRow, instance["id"]) is not None:
        return False
    session.add(CardInstanceRow(
        id=instance["id"],
        oracle_id=instance["oracle_id"],
        status=instance["status"],
        deck_id=instance.get("deck_id"),
        finish=instance.get("finish") or "nonfoil",
        condition=instance.get("condition") or "NM",
        printing_id=instance.get("printing_id"),
        price_paid=instance.get("price_paid"),
        acquired_at=instance.get("acquired_at"),
        notes=instance.get("notes"),
    ))
    ts = event_ts or instance.get("acquired_at")
    # Only pass ts when we have one — an explicit None would override the
    # server_default now() and violate NOT NULL.
    event = {"instance_id": instance["id"]}
    if ts is not None:
        event["ts"] = ts
    session.add(InstanceEventRow(kind="acquired", **event))
    if instance["status"] == "in_deck" and instance.get("deck_id") is not None:
        session.add(InstanceEventRow(kind="assigned", to_deck=instance.get("deck_id"), **event))
    return True


def instances_for_deck(session: Session, deck_id: int) -> list:
    """Copies physically assigned to the deck (status in_deck)."""
    rows = session.scalars(
        select(CardInstanceRow).where(
            CardInstanceRow.deck_id == deck_id,
            CardInstanceRow.status == "in_deck",
        )
    ).all()
    return [_instance_dict(r) for r in rows]


def collection_instances(session: Session, statuses=("in_collection", "in_mail")) -> list:
    """Unassigned owned copies (the free pool have/buy draws from)."""
    rows = session.scalars(
        select(CardInstanceRow).where(CardInstanceRow.status.in_(tuple(statuses)))
    ).all()
    return [_instance_dict(r) for r in rows]


def list_instances(session: Session, status: Optional[str] = None,
                   oracle_id: Optional[str] = None) -> list:
    q = select(CardInstanceRow).order_by(CardInstanceRow.created_at, CardInstanceRow.id)
    if status:
        q = q.where(CardInstanceRow.status == status)
    if oracle_id:
        q = q.where(CardInstanceRow.oracle_id == oracle_id)
    return [_instance_dict(r) for r in session.scalars(q).all()]


def instance_counts(session: Session) -> dict:
    """status -> row count (reconciliation reports)."""
    rows = session.execute(
        select(CardInstanceRow.status, func.count()).group_by(CardInstanceRow.status)
    ).all()
    return {status: n for status, n in rows}


def _instance_dict(r: CardInstanceRow) -> dict:
    return {
        "id": r.id,
        "oracle_id": r.oracle_id,
        "status": r.status,
        "deck_id": r.deck_id,
        "finish": r.finish,
        "condition": r.condition,
        "printing_id": r.printing_id,
        "price_paid": r.price_paid,
        "acquired_at": r.acquired_at.isoformat() if r.acquired_at else None,
        "notes": r.notes,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }


# ---------------------------------------------------------------------------
# profile_snapshots (append-only — no update/delete helpers exist on purpose)
# ---------------------------------------------------------------------------


def append_snapshot(session: Session, *, trigger: str, inputs: dict,
                    metrics: dict, summary_text: str) -> dict:
    row = ProfileSnapshotRow(
        trigger=trigger, inputs=inputs, metrics=metrics, summary_text=summary_text
    )
    session.add(row)
    session.flush()
    return _snapshot_dict(row)


def latest_snapshot(session: Session) -> Optional[dict]:
    row = session.scalars(
        select(ProfileSnapshotRow).order_by(ProfileSnapshotRow.id.desc()).limit(1)
    ).first()
    return _snapshot_dict(row) if row else None


def list_snapshots(session: Session, limit: int = 50) -> list:
    rows = session.scalars(
        select(ProfileSnapshotRow).order_by(ProfileSnapshotRow.id.desc()).limit(limit)
    ).all()
    return [_snapshot_dict(r) for r in rows]


def _snapshot_dict(r: ProfileSnapshotRow) -> dict:
    return {
        "id": r.id,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "trigger": r.trigger,
        "inputs": dict(r.inputs or {}),
        "metrics": dict(r.metrics or {}),
        "summary_text": r.summary_text,
    }

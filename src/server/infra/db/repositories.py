"""Repositories for the Slice 1 core tables (ARCHITECTURE.md §4.2).

All persistence writes for cards/printings/decks/deck_cards/card_instances/
instance_events/profile_snapshots go through here (boundary rule 4: no raw
SQL outside infra/db). Rows are converted to plain dicts at the boundary so
callers never see ORM objects.
"""
from __future__ import annotations

import datetime
from typing import Any, Iterable, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from src.server.infra.db.orm_models import (
    CardInstanceRow,
    CardRow,
    DeckCardRow,
    DeckRow,
    GameImportRow,
    GameRow,
    GameTelemetryRow,
    InstanceEventRow,
    PrintingRow,
    ProfileOverrideRow,
    ProfileSnapshotRow,
)


def _utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


# ---------------------------------------------------------------------------
# cards / printings
# ---------------------------------------------------------------------------

def upsert_card(session: Session, card: dict) -> None:
    """Insert or refresh one oracle-card row from a normalized card dict."""
    existing = session.get(CardRow, card["oracle_id"])
    fields = {
        "name": card.get("name", ""),
        "type_line": card.get("type_line", ""),
        "oracle_text": card.get("oracle_text", ""),
        "mana_cost": card.get("mana_cost", ""),
        "colors": card.get("colors") or [],
        "color_identity": card.get("color_identity") or [],
        "cmc": float(card.get("cmc") or 0),
        "keywords": card.get("keywords") or [],
        "legalities": card.get("legalities") or {},
        "image_uri": card.get("image_uri", ""),
        "etag": card.get("etag"),
        "refreshed_at": _utcnow(),
    }
    if existing is None:
        session.add(CardRow(oracle_id=card["oracle_id"], **fields))
    else:
        for key, value in fields.items():
            setattr(existing, key, value)


def upsert_printing(session: Session, printing: dict) -> None:
    existing = session.get(PrintingRow, printing["scryfall_id"])
    fields = {
        "oracle_id": printing["oracle_id"],
        "set_code": printing.get("set_code", ""),
        "collector_number": printing.get("collector_number", ""),
        "rarity": printing.get("rarity", ""),
        "prices": printing.get("prices") or {},
    }
    if existing is None:
        session.add(PrintingRow(scryfall_id=printing["scryfall_id"], **fields))
    else:
        for key, value in fields.items():
            setattr(existing, key, value)


def get_cards(session: Session, oracle_ids: Iterable[str]) -> dict[str, dict]:
    rows = session.scalars(
        select(CardRow).where(CardRow.oracle_id.in_(list(oracle_ids)))
    ).all()
    return {
        row.oracle_id: {
            "oracle_id": row.oracle_id,
            "name": row.name,
            "type_line": row.type_line,
            "oracle_text": row.oracle_text,
            "mana_cost": row.mana_cost,
            "colors": row.colors,
            "color_identity": row.color_identity,
            "cmc": row.cmc,
            "keywords": row.keywords,
            "image_uri": row.image_uri,
        }
        for row in rows
    }


def known_oracle_ids(session: Session, oracle_ids: Iterable[str]) -> set[str]:
    return set(get_cards(session, oracle_ids).keys())


# ---------------------------------------------------------------------------
# decks / deck_cards
# ---------------------------------------------------------------------------

def _deck_dict(row: DeckRow) -> dict:
    return {
        "id": row.id,
        "name": row.name,
        "archidekt_id": row.archidekt_id,
        "status": row.status,
        "color_identity": row.color_identity,
        "commanders": row.commanders,
        "source": row.source,
        "fingerprint": row.fingerprint,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "archived_at": row.archived_at.isoformat() if row.archived_at else None,
    }


def upsert_deck(session: Session, deck: dict) -> DeckRow:
    """Insert or update a deck keyed by its registry id."""
    row = session.get(DeckRow, deck["id"])
    fields = {
        "name": deck["name"],
        "archidekt_id": deck.get("archidekt_id"),
        "status": deck.get("status", "virtual"),
        "color_identity": deck.get("color_identity") or [],
        "commanders": deck.get("commanders") or [],
        "source": deck.get("source", "manual"),
        "fingerprint": deck.get("fingerprint", ""),
        "updated_at": _utcnow(),
    }
    if row is None:
        row = DeckRow(id=deck["id"], **fields)
        session.add(row)
    else:
        for key, value in fields.items():
            setattr(row, key, value)
    return row


def get_deck(session: Session, deck_id: str) -> Optional[dict]:
    row = session.get(DeckRow, deck_id)
    return _deck_dict(row) if row else None


def get_deck_by_archidekt_id(session: Session, archidekt_id: str) -> Optional[dict]:
    row = session.scalars(
        select(DeckRow).where(DeckRow.archidekt_id == str(archidekt_id))
    ).first()
    return _deck_dict(row) if row else None


def list_decks(session: Session, include_archived: bool = False) -> list[dict]:
    stmt = select(DeckRow).order_by(DeckRow.name)
    if not include_archived:
        stmt = stmt.where(DeckRow.archived_at.is_(None))
    return [_deck_dict(row) for row in session.scalars(stmt).all()]


def update_deck_status(session: Session, deck_id: str, status: str,
                       archived: bool = False) -> bool:
    """Apply a lifecycle status (+ optional archive stamp). True when a field
    actually changed; False for unknown decks and no-ops."""
    row = session.get(DeckRow, deck_id)
    if row is None:
        return False
    changed = False
    if row.status != status:
        row.status = status
        changed = True
    if archived and row.archived_at is None:
        row.archived_at = _utcnow()
        changed = True
    if changed:
        row.updated_at = _utcnow()
    return changed


def replace_deck_cards(session: Session, deck_id: str, cards: list[dict]) -> int:
    """Replace a deck's full card list (same transaction as the deck upsert).

    Returns the number of rows written.
    """
    session.execute(delete(DeckCardRow).where(DeckCardRow.deck_id == deck_id))
    for card in cards:
        session.add(DeckCardRow(
            deck_id=deck_id,
            oracle_id=card["oracle_id"],
            qty=int(card["qty"]),
            board=card["board"],
            category=card.get("category"),
        ))
    return len(cards)


def get_deck_cards(session: Session, deck_id: str) -> list[dict]:
    """Deck rows joined with the card reference facts needed by views/math."""
    stmt = (
        select(DeckCardRow, CardRow)
        .join(CardRow, CardRow.oracle_id == DeckCardRow.oracle_id)
        .where(DeckCardRow.deck_id == deck_id)
        .order_by(DeckCardRow.board, CardRow.name)
    )
    out = []
    for dc, card in session.execute(stmt).all():
        out.append({
            "deck_id": dc.deck_id,
            "oracle_id": dc.oracle_id,
            "qty": dc.qty,
            "board": dc.board,
            "category": dc.category,
            "name": card.name,
            "type_line": card.type_line,
            "cmc": card.cmc,
            "colors": card.colors,
            "color_identity": card.color_identity,
            "mana_cost": card.mana_cost,
            "image_uri": card.image_uri,
        })
    return out


def deck_card_counts(session: Session, deck_ids: Iterable[str]) -> dict[str, int]:
    """Total qty per deck (all boards), for deck-list summaries."""
    stmt = (
        select(DeckCardRow.deck_id, func.sum(DeckCardRow.qty))
        .where(DeckCardRow.deck_id.in_(list(deck_ids)))
        .group_by(DeckCardRow.deck_id)
    )
    return {deck_id: int(total) for deck_id, total in session.execute(stmt).all()}


# ---------------------------------------------------------------------------
# card_instances / instance_events
# ---------------------------------------------------------------------------

def _instance_dict(row: CardInstanceRow) -> dict:
    return {
        "id": row.id,
        "oracle_id": row.oracle_id,
        "status": row.status,
        "deck_id": row.deck_id,
        "finish": row.finish,
        "condition": row.condition,
        "acquired_at": row.acquired_at.isoformat() if row.acquired_at else None,
        "notes": row.notes,
        "set_code": row.set_code,
        "collector_number": row.collector_number,
        "price_paid": row.price_paid,
        "source": row.source,
        "considered_for_deck": row.considered_for_deck,
        "legacy_deck_ref": row.legacy_deck_ref,
    }


def insert_instance(session: Session, inst: dict) -> bool:
    """Insert one instance; False when the id already exists (idempotent)."""
    if session.get(CardInstanceRow, inst["id"]) is not None:
        return False
    session.add(CardInstanceRow(
        id=inst["id"],
        oracle_id=inst["oracle_id"],
        status=inst.get("status", "not_owned"),
        deck_id=inst.get("deck_id"),
        finish=inst.get("finish", "nonfoil"),
        condition=inst.get("condition", "NM"),
        acquired_at=inst.get("acquired_at"),
        notes=inst.get("notes", ""),
        set_code=inst.get("set_code", ""),
        collector_number=inst.get("collector_number", ""),
        price_paid=float(inst.get("price_paid") or 0),
        source=inst.get("source", ""),
        considered_for_deck=inst.get("considered_for_deck"),
        legacy_deck_ref=inst.get("legacy_deck_ref"),
    ))
    return True


def rebind_instances(session: Session, existing_deck_ids: set[str]) -> list[tuple[str, str]]:
    """Bind unbound migrated instances whose legacy deck ref now resolves.

    The Redis registry stored a numeric Archidekt deck id per instance; when a
    migration runs before the matching deck import, instances land unbound.
    Re-running apply after the import binds them. Returns (instance_id,
    deck_id) pairs that changed.
    """
    stmt = select(CardInstanceRow).where(
        CardInstanceRow.deck_id.is_(None),
        CardInstanceRow.legacy_deck_ref.isnot(None),
    )
    rebound: list[tuple[str, str]] = []
    for row in session.scalars(stmt).all():
        candidate = f"archidekt:{row.legacy_deck_ref}"
        if candidate in existing_deck_ids:
            row.deck_id = candidate
            rebound.append((row.id, candidate))
    return rebound


def list_instances(
    session: Session,
    oracle_ids: Optional[Iterable[str]] = None,
    status: Optional[str] = None,
    deck_id: Optional[str] = None,
) -> list[dict]:
    stmt = select(CardInstanceRow)
    if oracle_ids is not None:
        stmt = stmt.where(CardInstanceRow.oracle_id.in_(list(oracle_ids)))
    if status is not None:
        stmt = stmt.where(CardInstanceRow.status == status)
    if deck_id is not None:
        stmt = stmt.where(CardInstanceRow.deck_id == deck_id)
    return [_instance_dict(row) for row in session.scalars(stmt).all()]


def instance_counts_by_status(session: Session) -> dict[str, int]:
    stmt = select(CardInstanceRow.status, func.count()).group_by(CardInstanceRow.status)
    return {status: int(n) for status, n in session.execute(stmt).all()}


def collection_stats(session: Session) -> dict:
    """Collection-wide counts (profile snapshot inputs)."""
    total = session.scalar(select(func.count()).select_from(CardInstanceRow)) or 0
    distinct = session.scalar(
        select(func.count(func.distinct(CardInstanceRow.oracle_id)))
    ) or 0
    return {
        "instances": int(total),
        "distinct_cards": int(distinct),
        "by_status": instance_counts_by_status(session),
    }


def instance_counts_by_oracle(session: Session) -> dict[tuple[str, str], int]:
    """(oracle_id, status) -> count; reconciliation against the Redis registry."""
    stmt = (
        select(CardInstanceRow.oracle_id, CardInstanceRow.status, func.count())
        .group_by(CardInstanceRow.oracle_id, CardInstanceRow.status)
    )
    return {(o, s): int(n) for o, s, n in session.execute(stmt).all()}


def insert_event(session: Session, event: dict) -> None:
    session.add(InstanceEventRow(
        instance_id=event["instance_id"],
        ts=event.get("ts") or _utcnow(),
        kind=event["kind"],
        from_deck=event.get("from_deck"),
        to_deck=event.get("to_deck"),
        draft_id=event.get("draft_id"),
    ))


def event_counts_by_instance(session: Session, instance_ids: Iterable[str]) -> dict[str, int]:
    stmt = (
        select(InstanceEventRow.instance_id, func.count())
        .where(InstanceEventRow.instance_id.in_(list(instance_ids)))
        .group_by(InstanceEventRow.instance_id)
    )
    return {iid: int(n) for iid, n in session.execute(stmt).all()}


def event_counts_by_kind(session: Session) -> dict[str, int]:
    """Provenance events per kind (migration reconciliation reports)."""
    stmt = select(InstanceEventRow.kind, func.count()).group_by(InstanceEventRow.kind)
    return {kind: int(n) for kind, n in session.execute(stmt).all()}


# ---------------------------------------------------------------------------
# profile_snapshots
# ---------------------------------------------------------------------------

def _snapshot_dict(row: ProfileSnapshotRow) -> dict:
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "trigger": row.trigger,
        "inputs": row.inputs,
        "metrics": row.metrics,
        "summary_text": row.summary_text,
    }


def append_snapshot(
    session: Session,
    trigger: str,
    inputs: dict,
    metrics: dict,
    summary_text: str,
) -> int:
    row = ProfileSnapshotRow(
        trigger=trigger, inputs=inputs, metrics=metrics, summary_text=summary_text
    )
    session.add(row)
    session.flush()  # assign the id inside the caller's transaction
    return row.id


def latest_snapshot(session: Session) -> Optional[dict]:
    row = session.scalars(
        select(ProfileSnapshotRow).order_by(ProfileSnapshotRow.id.desc()).limit(1)
    ).first()
    return _snapshot_dict(row) if row else None


def list_snapshots(session: Session, limit: int = 50) -> list[dict]:
    rows = session.scalars(
        select(ProfileSnapshotRow).order_by(ProfileSnapshotRow.id.desc()).limit(limit)
    ).all()
    return [_snapshot_dict(row) for row in rows]


# ---------------------------------------------------------------------------
# profile_overrides
# ---------------------------------------------------------------------------

def _override_dict(row: ProfileOverrideRow) -> dict:
    return {
        "key": row.key,
        "value": row.value,
        "set_at": row.set_at.isoformat() if row.set_at else None,
    }


def get_overrides(session: Session) -> dict[str, dict]:
    """All active overrides keyed by override key."""
    rows = session.scalars(select(ProfileOverrideRow).order_by(ProfileOverrideRow.key)).all()
    return {row.key: _override_dict(row) for row in rows}


def upsert_override(session: Session, key: str, value: Any) -> dict:
    """Insert or replace one override; returns the stored row as a dict.

    Appending the matching profile_snapshots row is the caller's job
    (ARCHITECTURE.md §4.2: every override change appends a snapshot).
    """
    row = session.get(ProfileOverrideRow, key)
    if row is None:
        row = ProfileOverrideRow(key=key, value=value)
        session.add(row)
    else:
        row.value = value
        row.set_at = _utcnow()
    session.flush()
    return _override_dict(row)


# ---------------------------------------------------------------------------
# inventory read model (Slice 2 inventory page)
# ---------------------------------------------------------------------------

def inventory_rows(session: Session, query: str = "") -> list[dict]:
    """Per-card collection rows: instance counts by ownership status plus the
    decks copies are bound to, joined with card reference facts.

    One row per oracle_id that has at least one instance; ``not_owned``
    placeholder copies are counted but never summed into ``owned``.
    """
    stmt = (
        select(CardInstanceRow, CardRow)
        .join(CardRow, CardRow.oracle_id == CardInstanceRow.oracle_id)
        .order_by(CardRow.name)
    )
    if query:
        stmt = stmt.where(CardRow.name.ilike(f"%{query}%"))
    by_card: dict[str, dict] = {}
    for inst, card in session.execute(stmt).all():
        row = by_card.setdefault(inst.oracle_id, {
            "oracle_id": inst.oracle_id,
            "name": card.name,
            "type_line": card.type_line,
            "image_uri": card.image_uri,
            "in_collection": 0,
            "in_deck": 0,
            "in_mail": 0,
            "not_owned": 0,
            "decks": set(),
        })
        if inst.status in ("in_collection", "in_deck", "in_mail", "not_owned"):
            row[inst.status] += 1
        if inst.status == "in_deck" and inst.deck_id:
            row["decks"].add(inst.deck_id)
    rows = list(by_card.values())
    deck_names = {
        d["id"]: d["name"]
        for d in list_decks(session, include_archived=True)
    }
    for row in rows:
        row["decks"] = sorted(deck_names.get(d, d) for d in row["decks"])
        row["owned"] = row["in_collection"] + row["in_deck"] + row["in_mail"]
    rows.sort(key=lambda r: (-r["owned"], r["name"].lower()))
    return rows

# ---------------------------------------------------------------------------
# game_imports / games / game_telemetry (Slice 4, ARCHITECTURE.md §4.2)
# ---------------------------------------------------------------------------

def create_game_import(session: Session, imp: dict) -> int:
    row = GameImportRow(
        deck_id=imp["deck_id"],
        raw_log_uri=imp.get("raw_log_uri", ""),
        parser_version=imp["parser_version"],
        status=imp["status"],
        error=imp.get("error"),
        log_hash=imp["log_hash"],
    )
    session.add(row)
    session.flush()
    return row.id


def _import_dict(row: GameImportRow) -> dict:
    return {
        "id": row.id,
        "deck_id": row.deck_id,
        "imported_at": row.imported_at.isoformat() if row.imported_at else None,
        "raw_log_uri": row.raw_log_uri,
        "parser_version": row.parser_version,
        "status": row.status,
        "error": row.error,
        "log_hash": row.log_hash,
    }


def get_game_import_by_hash(session: Session, log_hash: str) -> Optional[dict]:
    row = session.scalars(
        select(GameImportRow).where(GameImportRow.log_hash == log_hash)
    ).first()
    return _import_dict(row) if row else None


def insert_game(session: Session, game: dict) -> int:
    row = GameRow(
        import_id=game["import_id"],
        deck_id=game["deck_id"],
        played_at=game.get("played_at"),
        seat_count=int(game["seat_count"]),
        result=game.get("result"),
        turns=game.get("turns"),
        notes=game.get("notes", ""),
    )
    session.add(row)
    session.flush()
    return row.id


def insert_telemetry(session: Session, game_id: int, metric: str, value: dict,
                     oracle_id: Optional[str] = None) -> None:
    session.add(GameTelemetryRow(
        game_id=game_id, metric=metric, value=value, oracle_id=oracle_id,
    ))


def _game_dict(row: GameRow, deck_name: Optional[str] = None,
               imported_at: Optional[str] = None) -> dict:
    return {
        "id": row.id,
        "import_id": row.import_id,
        "deck_id": row.deck_id,
        "deck_name": deck_name,
        "played_at": row.played_at.isoformat() if row.played_at else None,
        "seat_count": row.seat_count,
        "result": row.result,
        "turns": row.turns,
        "notes": row.notes,
        "imported_at": imported_at,
    }


def list_games_for_import(session: Session, import_id: int) -> list[dict]:
    rows = session.scalars(
        select(GameRow).where(GameRow.import_id == import_id).order_by(GameRow.id)
    ).all()
    return [_game_dict(r) for r in rows]


def list_games(session: Session, deck_id: Optional[str] = None,
               limit: int = 50) -> list[dict]:
    stmt = (
        select(GameRow, DeckRow.name, GameImportRow.imported_at)
        .join(DeckRow, DeckRow.id == GameRow.deck_id)
        .join(GameImportRow, GameImportRow.id == GameRow.import_id)
        .order_by(GameRow.id.desc())
        .limit(max(1, min(int(limit), 200)))
    )
    if deck_id:
        stmt = stmt.where(GameRow.deck_id == deck_id)
    out = []
    for game, deck_name, imported_at in session.execute(stmt).all():
        out.append(_game_dict(game, deck_name=deck_name,
                              imported_at=imported_at.isoformat() if imported_at else None))
    return out


def get_game(session: Session, game_id: int) -> Optional[dict]:
    row = session.execute(
        select(GameRow, DeckRow.name, GameImportRow.imported_at,
               GameImportRow.parser_version, GameImportRow.raw_log_uri)
        .join(DeckRow, DeckRow.id == GameRow.deck_id)
        .join(GameImportRow, GameImportRow.id == GameRow.import_id)
        .where(GameRow.id == game_id)
    ).first()
    if row is None:
        return None
    game, deck_name, imported_at, parser_version, raw_log_uri = row
    out = _game_dict(game, deck_name=deck_name,
                     imported_at=imported_at.isoformat() if imported_at else None)
    out["parser_version"] = parser_version
    out["raw_log_uri"] = raw_log_uri
    return out


def get_game_telemetry(session: Session, game_id: int) -> list[dict]:
    rows = session.scalars(
        select(GameTelemetryRow)
        .where(GameTelemetryRow.game_id == game_id)
        .order_by(GameTelemetryRow.id)
    ).all()
    return [
        {"metric": r.metric, "oracle_id": r.oracle_id, "value": r.value}
        for r in rows
    ]

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

from src.server.domain.draft import draft_mode
from src.server.infra.db.orm_models import (
    CardInstanceRow,
    CardRow,
    DeckCardRow,
    DeckRow,
    DraftCardRow,
    DraftInstanceMoveRow,
    DraftRow,
    GameImportRow,
    GameRow,
    GameTelemetryRow,
    InstanceEventRow,
    PrintingRow,
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


# ---------------------------------------------------------------------------
# drafts / draft_cards / draft_instance_moves (Slice 5, ARCHITECTURE.md §7.8)
#
# Staging-only: NOTHING below touches decks/deck_cards/card_instances except
# commit_draft, which applies the domain commit plan in ONE transaction (the
# caller's session: flush here, commit/rollback at the session boundary).
# abandon_draft deletes every staging row — zero residue.
# ---------------------------------------------------------------------------

def _draft_dict(row: DraftRow) -> dict:
    return {
        "id": row.id,
        "kind": row.kind,
        "status": row.status,
        "name": row.name,
        "source_decks": row.source_decks,
        "target_name": row.target_name,
        "target_commanders": row.target_commanders,
        "result_deck_id": row.result_deck_id,
        "notes": row.notes,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "closed_at": row.closed_at.isoformat() if row.closed_at else None,
    }


def create_draft(session: Session, draft: dict) -> dict:
    row = DraftRow(
        id=draft["id"],
        kind=draft["kind"],
        status=draft_mode.STATUS_OPEN,
        name=draft["name"],
        source_decks=draft.get("source_decks") or [],
        target_name=draft["target_name"],
        target_commanders=draft.get("target_commanders") or [],
        notes=draft.get("notes", ""),
    )
    session.add(row)
    session.flush()
    return _draft_dict(row)


def get_draft(session: Session, draft_id: str) -> Optional[dict]:
    row = session.get(DraftRow, draft_id)
    return _draft_dict(row) if row else None


def list_drafts(session: Session, status: Optional[str] = None,
                limit: int = 50) -> list[dict]:
    stmt = select(DraftRow).order_by(DraftRow.created_at.desc()).limit(
        max(1, min(int(limit), 200))
    )
    if status:
        stmt = stmt.where(DraftRow.status == status)
    return [_draft_dict(r) for r in session.scalars(stmt).all()]


def _require_open_draft(session: Session, draft_id: str) -> DraftRow:
    row = session.get(DraftRow, draft_id)
    if row is None:
        raise draft_mode.DraftError(f"draft not found: {draft_id}")
    if row.status != draft_mode.STATUS_OPEN:
        raise draft_mode.DraftError(f"draft is {row.status}, not open")
    return row


def upsert_draft_card(session: Session, draft_id: str, card: dict) -> None:
    _require_open_draft(session, draft_id)
    draft_mode.validate_staged_card(card["oracle_id"], card["qty"], card["board"])
    row = session.get(DraftCardRow, (draft_id, card["oracle_id"], card["board"]))
    if row is None:
        session.add(DraftCardRow(
            draft_id=draft_id, oracle_id=card["oracle_id"],
            qty=int(card["qty"]), board=card["board"],
            category=card.get("category"),
        ))
    else:
        row.qty = int(card["qty"])
        row.category = card.get("category")


def remove_draft_card(session: Session, draft_id: str, oracle_id: str,
                      board: str) -> bool:
    _require_open_draft(session, draft_id)
    row = session.get(DraftCardRow, (draft_id, oracle_id, board))
    if row is None:
        return False
    session.delete(row)
    return True


def list_draft_cards(session: Session, draft_id: str) -> list[dict]:
    stmt = (
        select(DraftCardRow, CardRow)
        .join(CardRow, CardRow.oracle_id == DraftCardRow.oracle_id)
        .where(DraftCardRow.draft_id == draft_id)
        .order_by(DraftCardRow.board, CardRow.name)
    )
    return [
        {
            "oracle_id": dc.oracle_id, "qty": dc.qty, "board": dc.board,
            "category": dc.category, "name": c.name, "type_line": c.type_line,
            "cmc": c.cmc, "color_identity": c.color_identity,
            "image_uri": c.image_uri,
        }
        for dc, c in session.execute(stmt).all()
    ]


def stage_move(session: Session, draft_id: str, instance_id: str,
               action: str) -> None:
    _require_open_draft(session, draft_id)
    inst = session.get(CardInstanceRow, instance_id)
    draft_mode.validate_move(action, _instance_dict(inst) if inst else None)
    assert inst is not None  # validate_move raises on None
    row = session.get(DraftInstanceMoveRow, (draft_id, instance_id))
    if row is None:
        session.add(DraftInstanceMoveRow(
            draft_id=draft_id, instance_id=instance_id,
            from_deck=inst.deck_id, action=action,
        ))
    else:
        row.action = action
        row.from_deck = inst.deck_id


def unstage_move(session: Session, draft_id: str, instance_id: str) -> bool:
    _require_open_draft(session, draft_id)
    row = session.get(DraftInstanceMoveRow, (draft_id, instance_id))
    if row is None:
        return False
    session.delete(row)
    return True


def list_draft_moves(session: Session, draft_id: str) -> list[dict]:
    stmt = (
        select(DraftInstanceMoveRow, CardInstanceRow, CardRow)
        .join(CardInstanceRow,
              CardInstanceRow.id == DraftInstanceMoveRow.instance_id)
        .join(CardRow, CardRow.oracle_id == CardInstanceRow.oracle_id)
        .where(DraftInstanceMoveRow.draft_id == draft_id)
        .order_by(CardRow.name)
    )
    return [
        {
            "instance_id": m.instance_id, "action": m.action,
            "from_deck": m.from_deck, "oracle_id": i.oracle_id,
            "card_name": c.name, "status": i.status, "deck_id": i.deck_id,
        }
        for m, i, c in session.execute(stmt).all()
    ]


def get_draft_state(session: Session, draft_id: str) -> Optional[dict]:
    """The full staging view: draft row + staged cards + staged moves."""
    draft = get_draft(session, draft_id)
    if draft is None:
        return None
    return {
        **draft,
        "cards": list_draft_cards(session, draft_id),
        "moves": list_draft_moves(session, draft_id),
    }


def commit_draft(session: Session, draft_id: str) -> dict:
    """Apply the draft to the canonical tables in ONE transaction: the new
    deck row + its deck_cards + instance moves + instance_events provenance
    (kind=assigned|unassigned, from_deck/to_deck, draft_id), per §4.2.

    Everything is flushed inside the caller's session — a DraftError (or any
    DB error) before the session boundary leaves zero canonical changes.
    """
    row = _require_open_draft(session, draft_id)
    draft = _draft_dict(row)
    cards = list_draft_cards(session, draft_id)
    moves = list_draft_moves(session, draft_id)
    move_ids = [m["instance_id"] for m in moves]
    instances_by_id = {}
    if move_ids:
        for inst in session.scalars(
            select(CardInstanceRow).where(CardInstanceRow.id.in_(move_ids))
        ).all():
            instances_by_id[inst.id] = _instance_dict(inst)
    plan = draft_mode.plan_commit(draft, cards, moves, instances_by_id)

    upsert_deck(session, plan["deck"])
    replace_deck_cards(session, plan["deck"]["id"], plan["deck_cards"])
    for instance_id, status, deck_id in plan["updates"]:
        inst = session.get(CardInstanceRow, instance_id)
        if inst is None:  # vanished mid-transaction: fail the whole commit
            raise draft_mode.DraftError(f"instance not found: {instance_id}")
        inst.status = status
        inst.deck_id = deck_id
        inst.updated_at = _utcnow()
    for event in plan["events"]:
        insert_event(session, event)

    row.status = draft_mode.STATUS_COMMITTED
    row.result_deck_id = plan["deck"]["id"]
    row.closed_at = _utcnow()
    session.flush()
    return {
        "draft_id": draft_id,
        "deck_id": plan["deck"]["id"],
        "cards_written": len(plan["deck_cards"]),
        "moves_applied": len(plan["updates"]),
        "events_written": len(plan["events"]),
    }


def abandon_draft(session: Session, draft_id: str) -> dict:
    """Abandon = delete every staging row for the draft. Zero residue:
    drafts/draft_cards/draft_instance_moves all end empty for this id, and
    canonical tables were never touched."""
    row = session.get(DraftRow, draft_id)
    if row is None:
        raise draft_mode.DraftError(f"draft not found: {draft_id}")
    if row.status != draft_mode.STATUS_OPEN:
        raise draft_mode.DraftError(f"draft is {row.status}, not open")
    cards = getattr(session.execute(
        delete(DraftCardRow).where(DraftCardRow.draft_id == draft_id)
    ), "rowcount", 0) or 0
    moves = getattr(session.execute(
        delete(DraftInstanceMoveRow).where(
            DraftInstanceMoveRow.draft_id == draft_id)
    ), "rowcount", 0) or 0
    session.delete(row)
    session.flush()
    return {"draft_id": draft_id, "staged_cards_deleted": int(cards or 0),
            "staged_moves_deleted": int(moves or 0)}


def list_instance_events(session: Session, instance_id: str) -> list[dict]:
    """One copy's full provenance stream, oldest first (§4.2)."""
    rows = session.scalars(
        select(InstanceEventRow)
        .where(InstanceEventRow.instance_id == instance_id)
        .order_by(InstanceEventRow.id)
    ).all()
    return [
        {
            "id": r.id,
            "ts": r.ts.isoformat() if r.ts else None,
            "kind": r.kind,
            "from_deck": r.from_deck,
            "to_deck": r.to_deck,
            "draft_id": r.draft_id,
        }
        for r in rows
    ]


def instance_provenance(session: Session, instance_id: str) -> Optional[dict]:
    """The queryable provenance path for one physical copy ("this copy moved
    A→B"), with deck display names resolved. None when the instance doesn't
    exist."""
    inst = session.get(CardInstanceRow, instance_id)
    if inst is None:
        return None
    events = list_instance_events(session, instance_id)
    deck_ids = {e["from_deck"] for e in events} | {e["to_deck"] for e in events}
    deck_ids.discard(None)
    names = {}
    if deck_ids:
        for d in session.scalars(
            select(DeckRow).where(DeckRow.id.in_(list(deck_ids)))
        ).all():
            names[d.id] = d.name
    path = draft_mode.provenance_path(instance_id, events, names)
    card = session.get(CardRow, inst.oracle_id)
    path["card_name"] = card.name if card else inst.oracle_id
    path["oracle_id"] = inst.oracle_id
    path["current_status"] = inst.status
    path["current_deck_id"] = inst.deck_id
    return path

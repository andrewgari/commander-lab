"""Persistence for the Forge auto-recorder: idempotent game ingest and the
cumulative play-style profile, on top of the shared db session helpers.

Ingest contract: the watcher POSTs raw log lines; the server segments and
parses them here so the parser has exactly one home. ``game_uid`` (content
hash from the parser) makes re-ingest of the same game a no-op, so the watcher
never needs exactly-once delivery.
"""
from __future__ import annotations

import datetime
from typing import Iterable, List, Optional

from sqlalchemy import select

from src.server.infra.db.orm_models import GameRow, PlayStyleProfileRow, UserRow
from src.server.infra.db.session import session_scope as _session_scope  # re-exported for tests/routes
from src.server.workers.forge_recorder import analyzer, parser

session_scope = _session_scope


class IngestError(Exception):
    pass


def resolve_user_id(session, user_id: Optional[str] = None) -> str:
    """The lab is single-user: an explicit id wins, otherwise the single
    users row is used. Raises IngestError when no user exists yet (run the
    redis_pg_import cutover first)."""
    if user_id:
        row = session.get(UserRow, user_id)
        if row is None:
            raise IngestError(f"unknown user_id: {user_id}")
        return user_id
    ids = session.execute(select(UserRow.id)).scalars().all()
    if not ids:
        raise IngestError("no users row exists; run the cutover import first")
    if len(ids) > 1:
        raise IngestError("multiple users exist; pass user_id explicitly")
    return ids[0]


def _insert_game(session, user_id: str, game: dict,
                 played_at: Optional[datetime.datetime]) -> bool:
    """Insert one parsed game. Returns False when the game_uid already exists."""
    exists = session.execute(
        select(GameRow.id).where(GameRow.game_uid == game["game_uid"])
    ).first()
    if exists:
        return False
    session.add(GameRow(
        user_id=user_id,
        game_uid=game["game_uid"],
        source="forge",
        played_at=played_at,
        players=game["players"],
        winner=game["winner"],
        turns=game["turns"],
        is_simulation=game["is_simulation"],
        summary=analyzer.summarize_game(game),
        events=game["events"],
    ))
    return True


def ingest_lines(session, user_id: Optional[str], lines: Iterable[str],
                 played_at: Optional[datetime.datetime] = None) -> dict:
    """Segment raw log lines into games, parse and store new ones, then rebuild
    the profile. Returns counts for the watcher/API response."""
    uid = resolve_user_id(session, user_id)
    stored: List[str] = []
    duplicates: List[str] = []
    blocks = parser.segment_games(lines)
    for block in blocks:
        game = parser.parse_game(block)
        if game is None:
            continue
        if _insert_game(session, uid, game, played_at):
            stored.append(game["game_uid"])
        else:
            duplicates.append(game["game_uid"])
    if stored:
        rebuild_profile(session, uid)
    return {"stored": stored, "duplicates": duplicates, "blocks": len(blocks)}


def rebuild_profile(session, user_id: str) -> dict:
    """Recompute the cumulative play-style profile from all stored games."""
    rows = session.execute(
        select(GameRow).where(GameRow.user_id == user_id).order_by(GameRow.id)
    ).scalars().all()
    games = [_row_to_game(r) for r in rows]
    profile = analyzer.build_profile(user_id, games)
    row = session.get(PlayStyleProfileRow, user_id)
    if row is None:
        session.add(PlayStyleProfileRow(
            user_id=user_id, games_analyzed=profile["games_analyzed"], profile=profile))
    else:
        row.games_analyzed = profile["games_analyzed"]
        row.profile = profile
    return profile


def _row_to_game(row: GameRow) -> dict:
    """Reconstruct the parser-shaped record the analyzer expects."""
    summary = row.summary or {}
    return {
        "game_uid": row.game_uid,
        "players": row.players or [],
        "is_simulation": row.is_simulation,
        "turns": row.turns,
        "winner": row.winner,
        "win_reason": summary.get("win_reason"),
        "player_stats": summary.get("player_stats") or {},
    }


def list_games(session, user_id: Optional[str] = None, limit: int = 50) -> List[dict]:
    uid = resolve_user_id(session, user_id)
    rows = session.execute(
        select(GameRow).where(GameRow.user_id == uid)
        .order_by(GameRow.id.desc()).limit(max(1, min(int(limit), 200)))
    ).scalars().all()
    return [
        {
            "game_uid": r.game_uid,
            "played_at": r.played_at.isoformat() if r.played_at else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "players": r.players,
            "winner": r.winner,
            "turns": r.turns,
            "is_simulation": r.is_simulation,
            "summary": r.summary,
        }
        for r in rows
    ]


def get_profile(session, user_id: Optional[str] = None) -> Optional[dict]:
    uid = resolve_user_id(session, user_id)
    row = session.get(PlayStyleProfileRow, uid)
    if row is None:
        return None
    return {
        "user_id": uid,
        "games_analyzed": row.games_analyzed,
        "profile": row.profile,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "text": analyzer.render_profile_text(row.profile),
    }

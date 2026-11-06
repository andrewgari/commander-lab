"""Games endpoints (Slice 4): imported-game list and the game report."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.server.app.api import schemas
from src.server.app.api.deps import db_session
from src.server.infra.db import repositories as repo

router = APIRouter()


@router.get("/games", response_model=schemas.GameListResponse)
def list_games(deck_id: Optional[str] = None, limit: int = 50,
               session: Session = Depends(db_session)):
    """Imported games, newest first (optionally per deck)."""
    return schemas.GameListResponse(
        games=[schemas.GameRecord(**g)
               for g in repo.list_games(session, deck_id=deck_id, limit=limit)]
    )


@router.get("/games/{game_id}", response_model=schemas.GameReportResponse)
def game_report(game_id: int, session: Session = Depends(db_session)):
    """The full game report: game row, analysis notes, per-seat curves and
    per-card cast telemetry with explicit unavailable markers."""
    game = repo.get_game(session, game_id)
    if game is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "game_not_found", "reason": f"no game with id {game_id}"},
        )
    telemetry = repo.get_game_telemetry(session, game_id)
    meta = next((t["value"] for t in telemetry if t["metric"] == "meta"), {})
    user_seat = meta.get("user_seat")
    players = [
        schemas.PlayerCurve(
            seat=t["value"].get("seat"),
            deck=t["value"].get("deck") or "",
            is_user_seat=(t["value"].get("seat") == user_seat),
            points=t["value"].get("points") or [],
            unavailable=t["value"].get("unavailable") or {},
        )
        for t in telemetry if t["metric"] == "curve"
    ]
    cards = [
        schemas.CardCast(
            seat=t["value"].get("seat"),
            deck=t["value"].get("deck") or "",
            card=t["value"].get("card") or "",
            oracle_id=t["oracle_id"],
            oracle_resolved=t["oracle_id"] is not None,
            cast_turn=t["value"].get("cast_turn"),
            drawn=t["value"].get("drawn"),
            stuck=t["value"].get("stuck"),
            unavailable=t["value"].get("unavailable") or {},
        )
        for t in telemetry if t["metric"] == "card_cast"
    ]
    return schemas.GameReportResponse(
        game=schemas.GameDetail(**game), meta=meta, players=players, cards=cards,
    )

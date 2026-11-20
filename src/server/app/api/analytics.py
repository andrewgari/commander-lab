"""Analytics endpoints (Slice 6 PR1): similarity reads from the derived
pgvector store and the derived-index freshness status.

The vector store is a rebuildable index (ARCHITECTURE.md §4.3/§7.1): these
endpoints only READ it via src/server/infra/vector/. When the index is
absent/stale or a card/deck is not indexed, they answer "unavailable" with
the reason — never a dressed-up empty result (AGENT_RULES.md §4).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from src.server.app.api import schemas
from src.server.app.api.deps import db_session
from src.server.infra.vector import store as vstore

log = logging.getLogger("commander_lab.api.analytics")

router = APIRouter()


def _iso(dt):
    return dt.isoformat() if dt is not None else None


@router.get("/analytics/similar-cards/{oracle_id}",
            response_model=schemas.SimilarCardsResponse)
def similar_cards(oracle_id: str, k: int = Query(default=10, ge=1, le=100),
                  session: Session = Depends(db_session)):
    """Top-k similar cards from the derived vector index."""
    try:
        exists = session.execute(
            text("SELECT 1 FROM cards WHERE oracle_id = :o"), {"o": oracle_id}
        ).first()
        if exists is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "card_not_found",
                        "reason": f"no card with oracle_id {oracle_id}"},
            )
        result = vstore.similar_cards(session, oracle_id, k=k)
    except HTTPException:
        raise
    except SQLAlchemyError as e:  # embedding tables not created yet, ...
        log.warning("similar-cards unavailable: %s", e)
        session.rollback()
        return schemas.SimilarCardsResponse(
            status="unavailable", oracle_id=oracle_id,
            reason=f"vector index not readable: {e.__class__.__name__}; "
                   "run index_rebuild.py vector")
    if result is None:
        return schemas.SimilarCardsResponse(
            status="unavailable", oracle_id=oracle_id,
            reason="card is not in the vector index (stale or never built); "
                   "run index_rebuild.py vector")
    return schemas.SimilarCardsResponse(
        status="ok", oracle_id=oracle_id, model_id=result["model_id"],
        results=[schemas.SimilarCardResult(**r) for r in result["results"]])


@router.get("/analytics/similar-decks/{deck_id}",
            response_model=schemas.SimilarDecksResponse)
def similar_decks(deck_id: str, k: int = Query(default=10, ge=1, le=100),
                  session: Session = Depends(db_session)):
    """Top-k similar decks from the derived vector index."""
    try:
        exists = session.execute(
            text("SELECT 1 FROM decks WHERE id = :d"), {"d": deck_id}
        ).first()
        if exists is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "deck_not_found",
                        "reason": f"no deck with id {deck_id}"},
            )
        result = vstore.similar_decks(session, deck_id, k=k)
    except HTTPException:
        raise
    except SQLAlchemyError as e:
        log.warning("similar-decks unavailable: %s", e)
        session.rollback()
        return schemas.SimilarDecksResponse(
            status="unavailable", deck_id=deck_id,
            reason=f"vector index not readable: {e.__class__.__name__}; "
                   "run index_rebuild.py vector")
    if result is None:
        return schemas.SimilarDecksResponse(
            status="unavailable", deck_id=deck_id,
            reason="deck is not in the vector index (stale or never built); "
                   "run index_rebuild.py vector")
    return schemas.SimilarDecksResponse(
        status="ok", deck_id=deck_id, model_id=result["model_id"],
        results=[schemas.SimilarDeckResult(**r) for r in result["results"]])


@router.get("/analytics/derived-index-status",
            response_model=schemas.DerivedIndexStatusResponse)
def derived_index_status(session: Session = Depends(db_session)):
    """Freshness of every derived store (vector today; graph with the AGE
    slice). fresh = the recorded source watermark covers the current max
    cards/decks update time."""
    try:
        rows = vstore.status(session)
    except SQLAlchemyError as e:  # derived_index_status table not created yet
        log.warning("derived-index-status unavailable: %s", e)
        session.rollback()
        return schemas.DerivedIndexStatusResponse(
            status="unavailable",
            reason=f"derived_index_status not readable: {e.__class__.__name__}; "
                   "run the a007 migration")
    return schemas.DerivedIndexStatusResponse(
        status="ok",
        stores=[schemas.DerivedIndexStoreStatus(
            store=r["store"], status=r["status"], fresh=r["fresh"],
            rebuilt_at=_iso(r["rebuilt_at"]),
            source_watermark=_iso(r["source_watermark"]),
            current_watermark=_iso(r["current_watermark"]),
            row_count=r["row_count"],
        ) for r in rows])

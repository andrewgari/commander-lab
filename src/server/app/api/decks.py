"""Deck list + deck detail over the Postgres source of truth (Slice 1)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.server.app.api import schemas
from src.server.app.api.deps import db_session
from src.server.domain.inventory import have_buy as have_buy_mod
from src.server.infra.db import repositories as repo
from src.server.infra.services import import_service

router = APIRouter()


def _summary(session: Session, deck: dict, instances: list) -> schemas.DeckSummary:
    deck_cards = repo.get_deck_cards(session, deck["id"])
    report = have_buy_mod.compute_have_buy(deck_cards, instances, deck["id"])
    return schemas.DeckSummary(
        id=deck["id"],
        name=deck["name"],
        status=deck["status"],
        source=deck["source"],
        archidekt_id=deck.get("archidekt_id"),
        color_identity=deck.get("color_identity") or [],
        commanders=deck.get("commanders") or [],
        card_qty=sum(c["qty"] for c in deck_cards),
        have_buy=schemas.HaveBuyTotals(**report["totals"]),
    )


@router.get("/decks", response_model=schemas.DeckListResponse)
def list_decks(session: Session = Depends(db_session)):
    decks = repo.list_decks(session)
    instances = repo.list_instances(session)
    return schemas.DeckListResponse(
        decks=[_summary(session, deck, instances) for deck in decks]
    )


@router.get("/decks/{deck_id}", response_model=schemas.DeckDetailResponse)
def deck_detail(deck_id: str, session: Session = Depends(db_session)):
    detail = import_service.deck_detail(session, deck_id)
    if detail is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "deck_not_found", "reason": f"no deck with id {deck_id!r}"},
        )
    return schemas.DeckDetailResponse(**detail)

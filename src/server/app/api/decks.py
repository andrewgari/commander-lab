"""Imported-deck read models (Slice 1). Thin: repo → domain math → pydantic."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.server.app.api.deps import DbSession
from src.server.domain.inventory import have_buy as have_buy_math
from src.server.infra.db import repositories as repo

router = APIRouter(prefix="/api/v2/decks", tags=["decks-v2"])


class DeckSummary(BaseModel):
    id: int
    name: str
    archidekt_id: Optional[str]
    archidekt_url: Optional[str]
    status: str
    color_identity: list[str]
    commanders: list[str]
    source: str


class DeckListResponse(BaseModel):
    decks: list[DeckSummary]


class HaveBuyCard(BaseModel):
    oracle_id: str
    name: str
    board: str
    category: Optional[str]
    required: int
    have: int
    have_in_deck: int
    have_from_collection: int
    pending: int
    buy: int


class HaveBuyTotals(BaseModel):
    required: int
    have: int
    pending: int
    buy: int
    have_in_deck: int
    have_from_collection: int


class DeckCardOut(BaseModel):
    oracle_id: str
    name: str
    board: str
    qty: int
    category: Optional[str]
    type_line: Optional[str]
    cmc: Optional[float]
    image_uri: Optional[str]


class DeckDetailResponse(BaseModel):
    deck: DeckSummary
    cards: list[DeckCardOut]
    have_buy: dict  # {"cards": [HaveBuyCard], "totals": HaveBuyTotals}


def _deck_summary(d: dict) -> DeckSummary:
    return DeckSummary(
        id=d["id"], name=d["name"], archidekt_id=d.get("archidekt_id"),
        archidekt_url=d.get("archidekt_url"), status=d["status"],
        color_identity=d.get("color_identity") or [],
        commanders=d.get("commanders") or [], source=d.get("source") or "unknown",
    )


def _have_buy_for(session: Session, deck_id: int) -> dict:
    deck_cards = repo.deck_cards_for(session, deck_id)
    instances = repo.instances_for_deck(session, deck_id) + repo.collection_instances(session)
    return have_buy_math.compute_have_buy(deck_cards, instances, deck_id)


@router.get("", response_model=DeckListResponse)
def list_imported_decks(session: Session = DbSession) -> DeckListResponse:
    return DeckListResponse(decks=[_deck_summary(d) for d in repo.list_decks(session)])


@router.get("/{deck_id}", response_model=DeckDetailResponse)
def get_imported_deck(deck_id: int, session: Session = DbSession) -> DeckDetailResponse:
    deck = repo.get_deck(session, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=f"deck {deck_id} not found")
    return DeckDetailResponse(
        deck=_deck_summary(deck),
        cards=[DeckCardOut(**c) for c in repo.deck_cards_for(session, deck_id)],
        have_buy=_have_buy_for(session, deck_id),
    )


@router.get("/{deck_id}/have-buy")
def get_have_buy(deck_id: int, session: Session = DbSession) -> dict:
    if repo.get_deck(session, deck_id) is None:
        raise HTTPException(status_code=404, detail=f"deck {deck_id} not found")
    return _have_buy_for(session, deck_id)

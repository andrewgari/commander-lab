"""Deck list + deck detail over the Postgres source of truth (Slice 1).

STA-11 work package B adds the deck-local workflow endpoints: per-deck
intent (owner-stated, Postgres-canonical), copy demand vs inventory with
explicit unknowns, and local text export.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from src.server.app.api import schemas
from src.server.app.api.deps import db_session
from src.server.domain.decks import deck_intent as deck_intent_mod
from src.server.domain.decks import decklist_export
from src.server.domain.inventory import copy_demand as copy_demand_mod
from src.server.domain.inventory import have_buy as have_buy_mod
from src.server.infra.db import repositories as repo
from src.server.infra.services import import_service

router = APIRouter()


def _deck_or_404(session: Session, deck_id: str) -> dict:
    deck = repo.get_deck(session, deck_id)
    if deck is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "deck_not_found", "reason": f"no deck with id {deck_id!r}"},
        )
    return deck


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


# --- STA-11 work package B: deck-local intent, copy demand, export ----------


def _intent_response(deck_id: str, row: dict | None) -> schemas.DeckIntentResponse:
    if row is None or deck_intent_mod.is_unstated(row["payload"]):
        return schemas.DeckIntentResponse(deck_id=deck_id, status="unstated")
    return schemas.DeckIntentResponse(
        deck_id=deck_id,
        status="stated",
        intent=row["payload"],
        provenance=row["provenance"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@router.get("/decks/{deck_id}/intent", response_model=schemas.DeckIntentResponse)
def deck_intent_get(deck_id: str, session: Session = Depends(db_session)):
    """The deck's stated intent, or status=unstated (never a defaulted profile)."""
    _deck_or_404(session, deck_id)
    return _intent_response(deck_id, repo.get_deck_intent(session, deck_id))


@router.put("/decks/{deck_id}/intent", response_model=schemas.DeckIntentResponse)
def deck_intent_put(
    deck_id: str,
    body: schemas.DeckIntentPutRequest,
    session: Session = Depends(db_session),
):
    """Store the deck's owner-stated intent. Only stated sections persist;
    nothing is backfilled, inferred, or copied from another deck."""
    _deck_or_404(session, deck_id)
    try:
        if body.merge:
            stored = repo.get_deck_intent(session, deck_id)
            payload = deck_intent_mod.merge_sections(
                (stored or {}).get("payload"), body.intent
            )
        else:
            payload = deck_intent_mod.validate_stated(body.intent)
    except deck_intent_mod.DeckIntentError as e:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_intent", "reason": str(e), "errors": e.errors},
        )
    row = repo.upsert_deck_intent(session, deck_id, payload)
    return _intent_response(deck_id, row)


@router.delete("/decks/{deck_id}/intent", response_model=schemas.DeckIntentResponse)
def deck_intent_delete(deck_id: str, session: Session = Depends(db_session)):
    """Un-state the deck's intent (back to an empty, unstated record)."""
    _deck_or_404(session, deck_id)
    repo.delete_deck_intent(session, deck_id)
    return schemas.DeckIntentResponse(deck_id=deck_id, status="unstated")


@router.get("/decks/{deck_id}/copy-demand", response_model=schemas.CopyDemandResponse)
def deck_copy_demand(deck_id: str, session: Session = Depends(db_session)):
    """Proposed copy demand vs known inventory. Printing/finish of demanded
    copies is explicitly unknown (deck rows carry no printing columns)."""
    deck = _deck_or_404(session, deck_id)
    deck_cards = repo.get_deck_cards(session, deck["id"])
    instances = repo.list_instances(
        session, oracle_ids={c["oracle_id"] for c in deck_cards}
    )
    report = copy_demand_mod.copy_demand(deck_cards, instances, deck["id"])
    return schemas.CopyDemandResponse(**report)


_EXPORT_BOARD_MAP = {"maybe": "maybeboard"}


@router.get("/decks/{deck_id}/export", response_class=PlainTextResponse)
def deck_export(
    deck_id: str,
    format: str = "plain",
    include_sideboard: bool = False,
    include_maybeboard: bool = False,
    include_printing: bool = False,
    session: Session = Depends(db_session),
):
    """Local text export of the canonical list (no remote writes)."""
    deck = _deck_or_404(session, deck_id)
    cards = [
        {
            "name": c["name"],
            "quantity": c["qty"],
            "board": _EXPORT_BOARD_MAP.get(c["board"], c["board"]),
        }
        for c in repo.get_deck_cards(session, deck["id"])
    ]
    try:
        text = decklist_export.export_decklist_text(
            {"name": deck["name"], "cards": cards},
            format=format,
            include_sideboard=include_sideboard,
            include_maybeboard=include_maybeboard,
            include_printing=include_printing,
        )
    except decklist_export.ExportFormatError as e:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_format", "reason": str(e)},
        )
    return PlainTextResponse(text + "\n")

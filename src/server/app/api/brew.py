"""Brew draft card editing (/api/v2/brew): add or remove one card on the brewed
list of a `drafts_ready` brew session, re-tiering from the pools.

Brew sessions live in Redis (domain/draft/brew.py), not Postgres, so these
routes take the app's Redis client instead of a DB session. Thin router only:
the rules (singleton, commander protection, tier recompute) are in the domain.
"""
from __future__ import annotations

from typing import Any, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

import src.server.domain.draft.brew as brew

router = APIRouter()


def get_redis() -> Any:
    """The app's Redis client. Resolved per request (main.py builds it after
    this module is imported, and tests swap it)."""
    import src.server.app.main as app_main

    return app_main.r


class BrewCardAddRequest(BaseModel):
    oracle_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    qty: int = Field(default=1, ge=1, le=99)

    @field_validator("oracle_id", "name")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("must not be blank")
        return v


class BrewDraftCard(BaseModel):
    model_config = {"extra": "allow"}

    name: str
    quantity: int
    tier: int
    tier_label: str
    cost: Optional[float] = None
    oracle_id: Optional[str] = None


class BrewDraftResponse(BaseModel):
    session_id: str
    draft: "BrewDraft"


class BrewDraft(BaseModel):
    model_config = {"extra": "allow"}

    cards: List[BrewDraftCard]
    total_new_cost: float = 0.0
    is_complete: bool = True
    unpriced_cards: List[str] = []


BrewDraftResponse.model_rebuild()


def _http(e: brew.BrewEditError) -> HTTPException:
    return HTTPException(status_code=e.status, detail={"error": e.code, "reason": str(e)})


@router.post("/brew/{session_id}/cards", response_model=BrewDraftResponse)
def add_brew_card(session_id: str, body: BrewCardAddRequest, r: Any = Depends(get_redis)):
    """Add `qty` of a card to the brew draft. Non-basic duplicates answer 409."""
    try:
        draft = brew.add_draft_card(r, session_id, body.oracle_id, body.name, body.qty)
    except brew.BrewEditError as e:
        raise _http(e)
    return {"session_id": session_id, "draft": draft}


@router.delete("/brew/{session_id}/cards/{oracle_id}", response_model=BrewDraftResponse)
def remove_brew_card(session_id: str, oracle_id: str, r: Any = Depends(get_redis)):
    """Remove a card line from the brew draft. The commander cannot be removed."""
    try:
        draft = brew.remove_draft_card(r, session_id, oracle_id)
    except brew.BrewEditError as e:
        raise _http(e)
    return {"session_id": session_id, "draft": draft}

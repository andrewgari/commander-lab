"""Typed pydantic v2 response/request models for the /api/v2 Slice 1 API."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class HaveBuyTotals(BaseModel):
    need: int
    have: int
    pending: int
    buy: int
    complete: bool
    have_pct: Optional[int] = None


class DeckSummary(BaseModel):
    id: str
    name: str
    status: str
    source: str
    archidekt_id: Optional[str] = None
    color_identity: list[str] = Field(default_factory=list)
    commanders: list[str] = Field(default_factory=list)
    card_qty: int = 0
    have_buy: Optional[HaveBuyTotals] = None


class DeckListResponse(BaseModel):
    decks: list[DeckSummary]


class DeckCardLine(BaseModel):
    oracle_id: str
    name: str
    qty: int
    board: str
    category: Optional[str] = None
    type_line: str = ""
    cmc: float = 0
    mana_cost: str = ""
    colors: list[str] = Field(default_factory=list)
    image_uri: str = ""
    have: int = 0
    pending: int = 0
    buy: int = 0
    elsewhere: int = 0


class HaveBuyLine(BaseModel):
    oracle_id: str
    name: str
    board: str
    category: Optional[str] = None
    need: int
    have: int
    pending: int
    buy: int
    elsewhere: int


class HaveBuyReport(BaseModel):
    deck_id: str
    totals: HaveBuyTotals
    lines: list[HaveBuyLine]


class ProfileSnapshot(BaseModel):
    id: int
    created_at: Optional[str] = None
    trigger: str
    inputs: dict = Field(default_factory=dict)
    metrics: dict = Field(default_factory=dict)
    summary_text: str = ""


class DeckRecord(BaseModel):
    id: str
    name: str
    archidekt_id: Optional[str] = None
    status: str
    color_identity: list[str] = Field(default_factory=list)
    commanders: list[str] = Field(default_factory=list)
    source: str
    fingerprint: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    archived_at: Optional[str] = None


class DeckDetailResponse(BaseModel):
    deck: DeckRecord
    boards: dict[str, list[DeckCardLine]]
    have_buy: HaveBuyReport
    snapshot: Optional[ProfileSnapshot] = None


class ImportRequest(BaseModel):
    identifier: str = Field(min_length=1, description="Archidekt deck id or deck URL")


class ImportResponse(BaseModel):
    deck_id: str
    archidekt_id: str
    name: str
    status: str
    commanders: list[str]
    color_identity: list[str]
    rows: int
    card_qty: int
    distinct_cards: int
    created: bool
    changed: bool
    fingerprint: str
    snapshot_id: Optional[int] = None
    have_buy: HaveBuyTotals
    parse_flags: list[str] = Field(default_factory=list)


class ProfileLatestResponse(BaseModel):
    available: bool
    reason: Optional[str] = None
    snapshot: Optional[ProfileSnapshot] = None

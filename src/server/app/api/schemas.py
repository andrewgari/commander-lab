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


class ProfileHistoryResponse(BaseModel):
    snapshots: list[ProfileSnapshot]


class ProfileOverride(BaseModel):
    key: str
    value: Optional[object] = None
    set_at: Optional[str] = None


class ProfileOverridesResponse(BaseModel):
    overrides: dict[str, ProfileOverride]
    known_keys: dict[str, str] = Field(
        default_factory=dict,
        description="override key -> human label, for the override controls")


class OverridePutRequest(BaseModel):
    value: Optional[object] = Field(
        default=None, description="typed per key; null clears the override")


class OverridePutResponse(BaseModel):
    override: ProfileOverride
    snapshot_id: int


class InventoryRow(BaseModel):
    oracle_id: str
    name: str
    type_line: str = ""
    image_uri: str = ""
    owned: int = 0
    in_collection: int = 0
    in_deck: int = 0
    in_mail: int = 0
    not_owned: int = 0
    decks: list[str] = Field(default_factory=list)


class InventoryTotals(BaseModel):
    cards: int
    owned: int
    in_collection: int
    in_deck: int
    in_mail: int


class InventoryResponse(BaseModel):
    inventory: list[InventoryRow]
    totals: InventoryTotals


# --- Slice 4: games on demand -------------------------------------------------

class ForgeLogImportRequest(BaseModel):
    deck_id: str = Field(min_length=1, description="Commander Lab deck id (e.g. archidekt:5054882)")
    log_text: str = Field(min_length=1, description="Raw verbose Forge log contents")
    seat: int = Field(default=1, ge=1, le=8, description="Seat the deck piloted")
    played_at: Optional[str] = Field(
        default=None,
        description="ISO-8601 timestamp; Forge logs carry none, so the user may supply it",
    )


class ImportedGameSummary(BaseModel):
    game_id: int
    result: Optional[str] = None
    turns: Optional[int] = None
    seat_count: int
    notes: Optional[str] = None


class ForgeLogImportResponse(BaseModel):
    duplicate: bool
    import_id: int
    status: str
    error: Optional[str] = None
    deck_id: str
    deck_name: str
    log_hash: str
    raw_log_uri: str
    games: list[ImportedGameSummary] = Field(default_factory=list)
    snapshot_id: Optional[int] = None


class GameRecord(BaseModel):
    id: int
    import_id: int
    deck_id: str
    deck_name: Optional[str] = None
    played_at: Optional[str] = None
    seat_count: int
    result: Optional[str] = None
    turns: Optional[int] = None
    imported_at: Optional[str] = None


class GameListResponse(BaseModel):
    games: list[GameRecord]


class GameDetail(GameRecord):
    notes: str = ""
    parser_version: str = ""
    raw_log_uri: str = ""


class PlayerCurve(BaseModel):
    seat: int
    deck: str
    is_user_seat: bool = False
    points: list[dict] = Field(default_factory=list)
    unavailable: dict = Field(default_factory=dict)


class CardCast(BaseModel):
    seat: int
    deck: str
    card: str
    oracle_id: Optional[str] = None
    oracle_resolved: bool = False
    cast_turn: Optional[int] = None
    drawn: Optional[int] = None
    stuck: Optional[bool] = None
    unavailable: dict = Field(default_factory=dict)


class GameReportResponse(BaseModel):
    game: GameDetail
    meta: dict = Field(default_factory=dict)
    players: list[PlayerCurve] = Field(default_factory=list)
    cards: list[CardCast] = Field(default_factory=list)

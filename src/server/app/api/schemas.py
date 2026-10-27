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


# --- Deck-local intent + copy demand (STA-11 work package B) ----------------

class DeckIntentResponse(BaseModel):
    deck_id: str
    status: str  # "stated" | "unstated"
    intent: Optional[dict] = None
    provenance: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class DeckIntentPutRequest(BaseModel):
    intent: dict = Field(
        description="Sparse UserDeckIntent payload; only stated sections are stored."
    )
    merge: bool = Field(
        default=False,
        description="false = replace the whole record; true = merge whole sections.",
    )


class CopyDemandLine(HaveBuyLine):
    printing: Optional[str] = None
    printing_status: str = "unknown"
    printing_reason: str = ""


class CopyDemandResponse(BaseModel):
    deck_id: str
    totals: HaveBuyTotals
    lines: list[CopyDemandLine]
    unknowns: dict = Field(default_factory=dict)


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


class ReconciliationPreviewResponse(BaseModel):
    """Read-only preview: every field is a proposal or a source fact.
    ``writes`` is always "none" -- nothing is applied by a preview."""
    writes: str
    identity: dict
    source_facts: dict
    proposed_import: dict
    main_deck_selection: str
    timestamps: dict
    printing_metadata: dict
    differences: Optional[dict] = None
    protected: list[str] = Field(default_factory=list)
    exceptions: list[str] = Field(default_factory=list)


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


# ---------------------------------------------------------------------------
# Reviews (Slice 3, ARCHITECTURE.md §4.2/§6)
# ---------------------------------------------------------------------------

class ReviewJobRecord(BaseModel):
    id: int
    review_id: int
    stage: str
    status: str
    error: Optional[str] = None
    ts: Optional[str] = None


class ReviewSummary(BaseModel):
    id: int
    deck_id: str
    version: int
    fingerprint: str
    status: str
    created_at: Optional[str] = None


class ReviewListResponse(BaseModel):
    reviews: list[ReviewSummary]


class ReviewResponse(BaseModel):
    """One stored review: fact_pack + analysis + doc from the same row, so
    the document and the dashboard render the same numbers."""
    id: int
    deck_id: str
    version: int
    fingerprint: str
    status: str
    fact_pack: dict = Field(default_factory=dict)
    analysis: dict = Field(default_factory=dict)
    doc: dict = Field(default_factory=dict)
    panels: list[dict] = Field(default_factory=list)
    created_at: Optional[str] = None
    jobs: list[ReviewJobRecord] = Field(default_factory=list)
# --- Slice 5: draft mode (staging + one-transaction commit) -------------------

class DraftCreateRequest(BaseModel):
    kind: str = Field(description="combine (>=2 source decks) | dismantle (exactly 1)")
    source_deck_ids: list[str] = Field(min_length=1)
    name: str = Field(min_length=1, description="Draft session name")
    target_name: str = Field(min_length=1, description="Name of the deck being built")
    prefill: bool = Field(
        default=False,
        description="Seed staging from the source decks' lists (union; commander board wins)",
    )
    notes: str = ""


class DraftRecord(BaseModel):
    id: str
    kind: str
    status: str
    name: str
    source_decks: list[dict] = Field(default_factory=list)
    target_name: str
    target_commanders: list = Field(default_factory=list)
    result_deck_id: Optional[str] = None
    notes: str = ""
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    closed_at: Optional[str] = None


class DraftListResponse(BaseModel):
    drafts: list[DraftRecord]


class DraftCardLine(BaseModel):
    oracle_id: str
    name: str
    qty: int
    board: str
    category: Optional[str] = None
    type_line: str = ""
    cmc: float = 0
    color_identity: list[str] = Field(default_factory=list)
    image_uri: str = ""


class DraftMoveLine(BaseModel):
    instance_id: str
    action: str
    from_deck: Optional[str] = None
    oracle_id: str
    card_name: str
    status: str
    deck_id: Optional[str] = None


class DraftStateResponse(BaseModel):
    draft: DraftRecord
    cards: list[DraftCardLine]
    moves: list[DraftMoveLine]


class DraftCardUpsertRequest(BaseModel):
    oracle_id: str = Field(min_length=1)
    qty: int = Field(ge=1)
    board: str = Field(description="commander | main | sideboard | maybe | considering")
    category: Optional[str] = None


class DraftMoveRequest(BaseModel):
    instance_id: str = Field(min_length=1)
    action: str = Field(description="assign (into the result deck) | unassign (back to collection)")


class DraftCommitResponse(BaseModel):
    draft_id: str
    deck_id: str
    cards_written: int
    moves_applied: int
    events_written: int


class DraftAbandonResponse(BaseModel):
    draft_id: str
    staged_cards_deleted: int
    staged_moves_deleted: int


class DraftSuggestionAdd(BaseModel):
    oracle_id: str
    name: str
    tier: str
    qty_available: int
    score: Optional[float] = None
    score_unavailable: bool = False
    donor: Optional[dict] = None
    reasons: list[str] = Field(default_factory=list)


class DraftSuggestionCut(BaseModel):
    oracle_id: str
    name: str
    board: Optional[str] = None
    weak_score: float
    parts: dict = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)


class DraftSuggestionsResponse(BaseModel):
    adds: list[DraftSuggestionAdd]
    hidden_donor_pulls: int = 0
    hidden_reason: Optional[str] = None
    cuts: list[DraftSuggestionCut]
    inputs_unavailable: dict = Field(default_factory=dict)
    note: str = ""


class ProvenanceStep(BaseModel):
    ts: Optional[str] = None
    kind: str
    from_deck: Optional[str] = None
    from_deck_name: Optional[str] = None
    to_deck: Optional[str] = None
    to_deck_name: Optional[str] = None
    draft_id: Optional[str] = None
    summary: str


class InstanceProvenanceResponse(BaseModel):
    instance_id: str
    oracle_id: str
    card_name: str
    current_status: str
    current_deck_id: Optional[str] = None
    steps: list[ProvenanceStep]
    count: int


# ---------------------------------------------------------------------------
# Slice 6 PR1: derived vector index (ARCHITECTURE.md §4.3). The store is a
# rebuildable index; "unavailable" + reason is a first-class value (§4).
# ---------------------------------------------------------------------------

class SimilarCardResult(BaseModel):
    oracle_id: str
    name: str
    score: float


class SimilarCardsResponse(BaseModel):
    status: str  # ok | unavailable
    oracle_id: str
    model_id: Optional[str] = None
    reason: Optional[str] = None
    results: list[SimilarCardResult] = Field(default_factory=list)


class SimilarDeckResult(BaseModel):
    deck_id: str
    name: str
    score: float


class SimilarDecksResponse(BaseModel):
    status: str  # ok | unavailable
    deck_id: str
    model_id: Optional[str] = None
    reason: Optional[str] = None
    results: list[SimilarDeckResult] = Field(default_factory=list)


class DerivedIndexStoreStatus(BaseModel):
    store: str  # vector | graph
    status: str  # ok | never_built
    fresh: bool
    rebuilt_at: Optional[str] = None
    source_watermark: Optional[str] = None
    current_watermark: Optional[str] = None
    row_count: int = 0


class DerivedIndexStatusResponse(BaseModel):
    status: str  # ok | unavailable
    reason: Optional[str] = None
    stores: list[DerivedIndexStoreStatus] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Card dossier (docs/architecture/card-dossier-spec.md). One aggregate
# response feeds every panel of /card/{oracle_id}; each panel maps 1:1 to a
# key and is an empty list/state when its query has no rows — never
# fabricated data (AGENT_RULES.md §4).
# ---------------------------------------------------------------------------

class DossierCard(BaseModel):
    oracle_id: str
    name: str
    mana_cost: str = ""
    type_line: str = ""
    oracle_text: str = ""
    colors: list[str] = Field(default_factory=list)
    color_identity: list[str] = Field(default_factory=list)
    cmc: float = 0
    keywords: list[str] = Field(default_factory=list)
    image_uri: str = ""


class DossierPrinting(BaseModel):
    scryfall_id: str
    oracle_id: str
    set_code: str = ""
    set_name: str = ""
    collector_number: str = ""
    rarity: str = ""
    released_at: Optional[str] = None
    is_default: bool = False
    image_uri_normal: str = ""
    image_uri_art_crop: str = ""
    prices: dict = Field(default_factory=dict)


class DossierTag(BaseModel):
    tag: str
    source: str  # learned | manual
    confidence: Optional[float] = None


class DossierDeckLine(BaseModel):
    deck_id: str
    name: str
    status: str
    board: str
    category: Optional[str] = None
    qty: int


class DossierSynergyNeighbor(BaseModel):
    oracle_id: str
    name: str
    direction: str  # out | in
    weight: int
    reasons: list[str] = Field(default_factory=list)
    reason_labels: list[str] = Field(default_factory=list)
    reason_text: str = ""


class DossierAffinity(BaseModel):
    oracle_id: str
    affinity: Optional[int] = None
    notes: str = ""
    updated_at: Optional[str] = None


class DossierCollection(BaseModel):
    owned: bool = False
    copies: int = 0
    statuses: list[str] = Field(default_factory=list)
    finishes: list[str] = Field(default_factory=list)


class CardDossierResponse(BaseModel):
    card: DossierCard
    printing: Optional[DossierPrinting] = None
    printings: list[DossierPrinting] = Field(default_factory=list)
    tags: list[DossierTag] = Field(default_factory=list)
    decks: list[DossierDeckLine] = Field(default_factory=list)
    synergy: list[DossierSynergyNeighbor] = Field(default_factory=list)
    affinity: Optional[DossierAffinity] = None
    collection: DossierCollection


class CardNotesPutRequest(BaseModel):
    affinity: Optional[int] = Field(default=None, ge=1, le=5)
    notes: str = ""


class CardNotesResponse(BaseModel):
    affinity: DossierAffinity


# --- Player taste profile + feedback (deck reviewer card 04) ----------------

class PlayerProfileResponse(BaseModel):
    name: str
    profile: dict
    provenance: dict
    versions: list[dict]
    pending_proposals: list[dict]


class PlayerProfilePutRequest(BaseModel):
    values: dict = Field(description="full profile values (no provenance); edits become 'stated'")


class FeedbackRequest(BaseModel):
    deck_id: str
    review_id: str
    cut: str
    add: str
    decision: str = Field(description="accepted | rejected | maybe")
    reason: str = ""

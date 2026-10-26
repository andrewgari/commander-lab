"""ORM tables. Domain shapes (analytics.composition_models) stay the public contract;
rows hold the normalized dict as JSON(B) and are converted at the repository boundary.
"""
from __future__ import annotations

import datetime
from typing import Any, Optional

from sqlalchemy import JSON, DateTime, Integer, String, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    """The single lab user. Every deck/inventory/journal/review/profile row
    references this id; it is the cutover's anchor row."""
    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    archidekt_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CompositionPresetRow(Base):
    __tablename__ = "composition_presets"
    seq: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)
    id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    data: Mapped[Any] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class DeckCompositionRow(Base):
    __tablename__ = "deck_compositions"
    registry_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    data: Mapped[Any] = mapped_column(JsonType, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DeckLinkRow(Base):
    """A deck's link to an external provider deck (Archidekt / Moxfield).

    Identity is the link itself: ``(provider, external_id)``. Never name or
    commander, so two decks with the same name never collide and there is no
    merge/dedupe logic. ``deck_id`` is the local registry id and is nullable
    because a link may reference an external deck not (yet) registered locally.
    """
    __tablename__ = "deck_links"
    id: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id"), nullable=False)
    deck_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)  # archidekt | moxfield
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)  # provider-native deck id
    url: Mapped[str] = mapped_column(String(512), nullable=False)
    folder_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)  # Archidekt parentFolderId
    name_at_source: Mapped[str] = mapped_column(String(255), nullable=False)
    commander_at_source: Mapped[str] = mapped_column(String(255), nullable=True)
    match_method: Mapped[str] = mapped_column(String(64), nullable=False)
    last_synced_at: Mapped[datetime.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    upstream_missing_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    raw: Mapped[Any] = mapped_column(JsonType, nullable=False)
    __table_args__ = (
        UniqueConstraint("provider", "external_id", name="uq_deck_links_provider_external_id"),
    )


class LegacyGameRow(Base):
    """LEGACY Forge auto-recorder row (pre-Slice-4): parsed per-player stats,
    summary and the (noise-filtered) event list. ``game_uid`` is a content
    hash for idempotent ingest from the log watcher.

    The canonical games model is the ARCHITECTURE.md §4.2 trio
    (game_imports/games/game_telemetry) below; migration a004 renames the
    old table to ``legacy_games`` so the watcher keeps working unchanged."""
    __tablename__ = "legacy_games"
    id: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255), ForeignKey("users.id"), nullable=False)
    game_uid: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="forge")
    played_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    players: Mapped[Any] = mapped_column(JsonType, nullable=False)
    winner: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    turns: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_simulation: Mapped[bool] = mapped_column(nullable=False, default=False)
    summary: Mapped[Any] = mapped_column(JsonType, nullable=False)
    events: Mapped[Any] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Slice 4 games-on-demand tables (ARCHITECTURE.md §4.2; DDL in
# migrations/a004_games_on_demand). On-demand Forge verbose-log imports only
# (§0); the parser is domain/forge/forge_game_telemetry.py (verbatim port).
# ---------------------------------------------------------------------------

class GameImportRow(Base):
    """One on-demand Forge log import. ``log_hash`` (sha256 of the raw log)
    fingerprints the file: re-importing the same log short-circuits on this
    column, so no duplicate games are ever written."""
    __tablename__ = "game_imports"
    id: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)
    deck_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("decks.id"), nullable=False
    )
    imported_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    raw_log_uri: Mapped[str] = mapped_column(String, nullable=False, default="")
    parser_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False)  # parsed | failed
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    log_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class GameRow(Base):
    """One completed game from an imported log (§4.2). ``played_at``,
    ``result`` and ``turns`` are NULL when the log does not carry them —
    unavailable is a first-class value, the reason lives in ``notes`` and the
    game's `meta` telemetry row (AGENT_RULES.md §4), never a dressed-up 0."""
    __tablename__ = "games"
    id: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)
    import_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("game_imports.id"), nullable=False
    )
    deck_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("decks.id"), nullable=False
    )
    played_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    seat_count: Mapped[int] = mapped_column(Integer, nullable=False)
    result: Mapped[Optional[str]] = mapped_column(String(8), nullable=True)  # win | loss | draw
    turns: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    notes: Mapped[str] = mapped_column(String, nullable=False, default="")
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class GameTelemetryRow(Base):
    """Parser output for one game (§4.2). Metrics: ``meta`` (one per game:
    user seat, game number, unavailable reasons), ``curve`` (one per seat:
    per-turn land/mana/spells), ``card_cast`` (one per seat+card: cast_turn,
    drawn/stuck NULL with explicit unavailable reasons). ``oracle_id`` is the
    resolved cards row for card_cast metrics, NULL when unresolved."""
    __tablename__ = "game_telemetry"
    id: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)
    game_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("games.id"), nullable=False
    )
    oracle_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("cards.oracle_id"), nullable=True
    )
    metric: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[Any] = mapped_column(JsonType, nullable=False)
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class PlayStyleProfileRow(Base):
    """The per-user cumulative play-style profile derived from recorded games.
    One row per user; replaced wholesale on every rebuild."""
    __tablename__ = "play_style_profile"
    user_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("users.id"), primary_key=True
    )
    games_analyzed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    profile: Mapped[Any] = mapped_column(JsonType, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


# ---------------------------------------------------------------------------
# Slice 1 core tables (ARCHITECTURE.md §4.2; DDL in migrations/a003_slice1_core).
# ---------------------------------------------------------------------------

class CardRow(Base):
    """Scryfall oracle-card mirror. Facts only; refreshed by sync jobs."""
    __tablename__ = "cards"
    oracle_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type_line: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    oracle_text: Mapped[str] = mapped_column(String, nullable=False, default="")
    mana_cost: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    colors: Mapped[Any] = mapped_column(JsonType, nullable=False, default=list)
    color_identity: Mapped[Any] = mapped_column(JsonType, nullable=False, default=list)
    cmc: Mapped[float] = mapped_column(nullable=False, default=0)
    keywords: Mapped[Any] = mapped_column(JsonType, nullable=False, default=list)
    legalities: Mapped[Any] = mapped_column(JsonType, nullable=False, default=dict)
    image_uri: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    etag: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    refreshed_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class PrintingRow(Base):
    __tablename__ = "printings"
    scryfall_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    oracle_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cards.oracle_id"), nullable=False
    )
    set_code: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    collector_number: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    rarity: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    prices: Mapped[Any] = mapped_column(JsonType, nullable=False, default=dict)
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class DeckRow(Base):
    """A Commander Lab-owned deck. ``id`` is the registry id
    (``archidekt:{archidekt_id}`` for imported decks)."""
    __tablename__ = "decks"
    id: Mapped[str] = mapped_column(String(255), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    archidekt_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="virtual")
    color_identity: Mapped[Any] = mapped_column(JsonType, nullable=False, default=list)
    commanders: Mapped[Any] = mapped_column(JsonType, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="manual")
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    archived_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class DeckCardRow(Base):
    __tablename__ = "deck_cards"
    deck_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("decks.id"), primary_key=True
    )
    oracle_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cards.oracle_id"), primary_key=True
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    board: Mapped[str] = mapped_column(String(16), primary_key=True)
    category: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class CardInstanceRow(Base):
    """One row per physical copy; carries the instances.py ownership states."""
    __tablename__ = "card_instances"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    oracle_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cards.oracle_id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="not_owned")
    deck_id: Mapped[Optional[str]] = mapped_column(
        String(255), ForeignKey("decks.id"), nullable=True
    )
    finish: Mapped[str] = mapped_column(String(32), nullable=False, default="nonfoil")
    condition: Mapped[str] = mapped_column(String(16), nullable=False, default="NM")
    acquired_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[str] = mapped_column(String, nullable=False, default="")
    set_code: Mapped[str] = mapped_column(String(16), nullable=False, default="")
    collector_number: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    price_paid: Mapped[float] = mapped_column(nullable=False, default=0)
    source: Mapped[str] = mapped_column(String(64), nullable=False, default="")
    considered_for_deck: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    legacy_deck_ref: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class InstanceEventRow(Base):
    """Append-only provenance log for physical copies."""
    __tablename__ = "instance_events"
    id: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)
    instance_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("card_instances.id"), nullable=False
    )
    ts: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    from_deck: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    to_deck: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    draft_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


# ---------------------------------------------------------------------------
# Slice 5 draft-mode staging tables (ARCHITECTURE.md §7.8, §8 Slice 5; DDL in
# migrations/a005_draft_mode). Staging-only: canonical tables are untouched
# until Commit; Abandon deletes every staging row (zero residue).
# ---------------------------------------------------------------------------

class DraftRow(Base):
    """One draft session (combine decks / dismantle one to build another).

    ``source_decks`` is a jsonb snapshot of the source deck ids/names at
    creation; ``result_deck_id`` is set only by Commit. Committed drafts keep
    their rows as the audit record ``instance_events.draft_id`` points to.
    """
    __tablename__ = "drafts"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # combine | dismantle
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_decks: Mapped[Any] = mapped_column(JsonType, nullable=False, default=list)
    target_name: Mapped[str] = mapped_column(String(255), nullable=False)
    target_commanders: Mapped[Any] = mapped_column(JsonType, nullable=False, default=list)
    result_deck_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    notes: Mapped[str] = mapped_column(String, nullable=False, default="")
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    closed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class DraftCardRow(Base):
    """A staged decklist row for the deck being built (mirrors deck_cards)."""
    __tablename__ = "draft_cards"
    draft_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("drafts.id"), primary_key=True
    )
    oracle_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cards.oracle_id"), primary_key=True
    )
    qty: Mapped[int] = mapped_column(Integer, nullable=False)
    board: Mapped[str] = mapped_column(String(16), primary_key=True)
    category: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class DraftInstanceMoveRow(Base):
    """A staged physical-copy move. ``from_deck`` snapshots the instance's
    deck binding at staging time; the assign target is always the draft's
    result deck (known only at commit)."""
    __tablename__ = "draft_instance_moves"
    draft_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("drafts.id"), primary_key=True
    )
    instance_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("card_instances.id"), primary_key=True
    )
    from_deck: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # assign | unassign
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class ProfileSnapshotRow(Base):
    """Append-only profile history; current profile = latest snapshot."""
    __tablename__ = "profile_snapshots"
    id: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(16), nullable=False)
    inputs: Mapped[Any] = mapped_column(JsonType, nullable=False, default=dict)
    metrics: Mapped[Any] = mapped_column(JsonType, nullable=False, default=dict)
    summary_text: Mapped[str] = mapped_column(String, nullable=False, default="")
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class DerivedIndexStatusRow(Base):
    """Freshness bookkeeping for the derived (rebuildable) indexes
    (ARCHITECTURE.md §4.2/§7.1). Written ONLY by
    src/server/workers/index_rebuild.py. The embedding tables themselves are
    deliberately not ORM-mapped: the ``vector`` column type is
    PostgreSQL-only and access goes through src/server/infra/vector/ with raw
    SQL."""
    __tablename__ = "derived_index_status"
    store: Mapped[str] = mapped_column(String(16), primary_key=True)  # vector | graph
    rebuilt_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    source_watermark: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class ProfileOverrideRow(Base):
    """User steering over the computed profile (ARCHITECTURE.md §4.2). Every
    write also appends a profile_snapshots row — enforced by the service
    layer (infra/services/profile_service.py), not by this table."""
    __tablename__ = "profile_overrides"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JsonType, nullable=False)
    set_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


# ---------------------------------------------------------------------------
# Slice 3 review pipeline (ARCHITECTURE.md §4.2; DDL in migrations/a005_create_reviews).
# ---------------------------------------------------------------------------

class ReviewRow(Base):
    """One stored review. `fact_pack` + `doc` render both the styled document
    and the dashboard panels — always from this same row."""
    __tablename__ = "reviews"
    id: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)
    deck_id: Mapped[str] = mapped_column(
        String(255), ForeignKey("decks.id"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False)  # complete | partial
    fact_pack: Mapped[Any] = mapped_column(JsonType, nullable=False, default=dict)
    analysis: Mapped[Any] = mapped_column(JsonType, nullable=False, default=dict)
    doc: Mapped[Any] = mapped_column(JsonType, nullable=False, default=dict)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class ReviewJobRow(Base):
    """Pipeline audit trail: one row per build stage."""
    __tablename__ = "review_jobs"
    id: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)
    review_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("reviews.id"), nullable=False
    )
    stage: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok | degraded | failed
    error: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    ts: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)

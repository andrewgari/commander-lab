"""Canonical Slice-1 tables (docs/ARCHITECTURE.md §4.2).

Card reference mirror (``cards``/``printings``), Commander-Lab-owned decks and
collection (``decks``/``deck_cards``/``card_instances``/``instance_events``)
and the append-only taste profile (``profile_snapshots``).

Every table carries ``owner_id NULL`` per the single-user-ready note in §4.2:
multi-user is an additive migration, not a redesign.

Enum vocabularies are ratified conventions (AGENT_RULES.md §9 /
docs/ARCHITECTURE.md §4.2) and are enforced as VARCHAR + CHECK constraints so
``Base.metadata.create_all`` (tests) and the hand-written a003 migration
produce the identical shape on both Postgres and SQLite.
"""
from __future__ import annotations

import datetime
from typing import Any, Optional

from sqlalchemy import JSON, CheckConstraint, DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from src.server.infra.db.orm_models import Base, JsonType

# ---------------------------------------------------------------------------
# Enum vocabularies (canonical; do not rename or repurpose — AGENT_RULES §9)
# ---------------------------------------------------------------------------

DECK_STATUSES = ("have", "virtual", "pending", "possible")
DECK_SOURCES = ("archidekt", "brew", "draft", "manual")
BOARDS = ("commander", "main", "sideboard", "maybe", "considering")
INSTANCE_STATUSES = ("not_owned", "in_mail", "in_collection", "in_deck")
INSTANCE_EVENT_KINDS = ("acquired", "assigned", "unassigned", "mailed", "traded", "lost")
SNAPSHOT_TRIGGERS = ("import", "game", "rating", "manual", "override")

# Structural boards/categories are preserved and excluded from thematic tag
# learning (AGENT_RULES §9). "Sideboard" maps to maybe on import (the old
# provider behaviour); the enum keeps it for explicit storage.
STRUCTURAL_CATEGORIES = ("Commander", "Sideboard", "Maybeboard", "Considering")


class CardRow(Base):
    """Scryfall oracle-level card mirror. Facts only — refreshed by sync jobs."""
    __tablename__ = "cards"
    oracle_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    type_line: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    oracle_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    colors: Mapped[Any] = mapped_column(JsonType, nullable=False, default=list)
    cmc: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    keywords: Mapped[Any] = mapped_column(JsonType, nullable=False, default=list)
    legalities: Mapped[Any] = mapped_column(JsonType, nullable=False, default=dict)
    image_uri: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    etag: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    refreshed_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class PrintingRow(Base):
    """One physical printing of a card (Scryfall id keyed)."""
    __tablename__ = "printings"
    scryfall_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    oracle_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cards.oracle_id"), nullable=False, index=True
    )
    set_code: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    collector_number: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    rarity: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    prices: Mapped[Any] = mapped_column(JsonType, nullable=False, default=dict)
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)


class DeckRow(Base):
    __tablename__ = "decks"
    id: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    archidekt_id: Mapped[Optional[str]] = mapped_column(String(64), unique=True, nullable=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="virtual")
    color_identity: Mapped[Any] = mapped_column(JsonType, nullable=False, default=list)
    commanders: Mapped[Any] = mapped_column(JsonType, nullable=False, default=list)
    source: Mapped[str] = mapped_column(String(32), nullable=False, default="manual")
    fingerprint: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
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
    __table_args__ = (
        CheckConstraint(f"status IN {DECK_STATUSES}", name="ck_decks_status"),
        CheckConstraint(f"source IN {DECK_SOURCES}", name="ck_decks_source"),
    )


class DeckCardRow(Base):
    __tablename__ = "deck_cards"
    deck_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("decks.id"), primary_key=True
    )
    oracle_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cards.oracle_id"), primary_key=True
    )
    board: Mapped[str] = mapped_column(String(32), primary_key=True)
    qty: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    category: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    __table_args__ = (
        CheckConstraint("qty > 0", name="ck_deck_cards_qty_positive"),
        CheckConstraint(f"board IN {BOARDS}", name="ck_deck_cards_board"),
    )


class CardInstanceRow(Base):
    """One row per physical copy — the ownership source of truth.

    Carries the instances.py state machine vocabulary unchanged
    (``INSTANCE_STATUSES`` == OWNERSHIP_STATUSES in domain/inventory/instances.py).
    ``printing_id``/``price_paid`` preserve the Redis instance record's printing
    identity (``archidekt_uid``) and cost basis through the migration.
    """
    __tablename__ = "card_instances"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # uuid, same ids as Redis
    oracle_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("cards.oracle_id"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    deck_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("decks.id"), nullable=True, index=True
    )
    finish: Mapped[str] = mapped_column(String(32), nullable=False, default="nonfoil")
    condition: Mapped[str] = mapped_column(String(8), nullable=False, default="NM")
    printing_id: Mapped[Optional[str]] = mapped_column(
        String(64), ForeignKey("printings.scryfall_id"), nullable=True
    )
    price_paid: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    acquired_at: Mapped[Optional[datetime.datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    __table_args__ = (
        CheckConstraint(f"status IN {INSTANCE_STATUSES}", name="ck_card_instances_status"),
    )


class InstanceEventRow(Base):
    """Append-only provenance log. This table IS card provenance (§4.2)."""
    __tablename__ = "instance_events"
    id: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)
    instance_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("card_instances.id"), nullable=False, index=True
    )
    ts: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    from_deck: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("decks.id"), nullable=True
    )
    to_deck: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("decks.id"), nullable=True
    )
    draft_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    __table_args__ = (
        CheckConstraint(f"kind IN {INSTANCE_EVENT_KINDS}", name="ck_instance_events_kind"),
    )


class ProfileSnapshotRow(Base):
    """Append-only taste profile history. Never updated (§4.2): the current
    profile is the latest snapshot + active overrides."""
    __tablename__ = "profile_snapshots"
    id: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    trigger: Mapped[str] = mapped_column(String(32), nullable=False)
    inputs: Mapped[Any] = mapped_column(JsonType, nullable=False, default=dict)
    metrics: Mapped[Any] = mapped_column(JsonType, nullable=False, default=dict)
    summary_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    owner_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    __table_args__ = (
        CheckConstraint(f"trigger IN {SNAPSHOT_TRIGGERS}", name="ck_profile_snapshots_trigger"),
    )


SLICE1_TABLES = (
    "cards", "printings", "decks", "deck_cards",
    "card_instances", "instance_events", "profile_snapshots",
)

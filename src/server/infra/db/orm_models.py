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


class GameRow(Base):
    """One recorded Forge game: parsed per-player stats, summary and the
    (noise-filtered) event list. ``game_uid`` is a content hash for idempotent
    ingest from the log watcher."""
    __tablename__ = "games"
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

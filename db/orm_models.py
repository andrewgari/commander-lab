"""ORM tables. Domain shapes (analytics.composition_models) stay the public contract;
rows hold the normalized dict as JSON(B) and are converted at the repository boundary."""
from __future__ import annotations

import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

JsonType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    pass


class CompositionPresetRow(Base):
    __tablename__ = "composition_presets"
    seq: Mapped[int] = mapped_column(Integer, autoincrement=True, primary_key=True)
    id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    data: Mapped[Any] = mapped_column(JsonType, nullable=False)
    created_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False)


class DeckCompositionRow(Base):
    __tablename__ = "deck_compositions"
    registry_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    data: Mapped[Any] = mapped_column(JsonType, nullable=False)
    updated_at: Mapped[datetime.datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

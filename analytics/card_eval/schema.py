"""Typed result schema for card evaluation (documented in docs/CARD_EVAL.md)."""
from __future__ import annotations

from typing import Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

DeckKind = Literal["physical", "digital"]
EdhrecStatus = Literal["ok", "card_not_listed", "no_commander_page", "unavailable", "no_commander"]
ModelStatus = Literal["ok", "disabled", "unavailable"]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DeckContext(_Model):
    commanders: List[str]
    cards: List[str] = Field(default_factory=list, description="Full decklist (names; quantity ignored).")
    kind: DeckKind = "digital"


class RoleScore(_Model):
    role: str
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["model", "heuristic", "edhrec"]


class EdhrecStats(_Model):
    status: EdhrecStatus
    commander_slug: Optional[str] = None
    inclusion: Optional[float] = Field(None, ge=0.0, le=1.0, description="num_decks / potential_decks")
    synergy: Optional[float] = Field(None, description="EDHREC synergy, roughly -1..1")
    num_decks: Optional[int] = None
    decks_sampled: Optional[int] = None
    slot: Optional[str] = Field(None, description="EDHREC cardlist tag the card appears in, e.g. 'topcards'")
    slot_peers: List[str] = Field(default_factory=list, description="Other top cards in the same slot")
    stale: bool = False
    error: Optional[str] = None


class ModelSignal(_Model):
    status: ModelStatus
    model_id: Optional[str] = None
    error: Optional[str] = None
    scores: Dict[str, float] = Field(default_factory=dict, description="role -> model confidence")


class StrengthBreakdown(_Model):
    inclusion: Optional[float] = None
    synergy: Optional[float] = None
    role_confidence: Optional[float] = None
    role_need: Optional[float] = None


class Candidate(_Model):
    name: str
    roles: List[str] = Field(default_factory=list)
    same_role: bool
    inclusion: Optional[float] = None
    synergy: Optional[float] = None
    score: float
    color_identity: str = ""
    owned: Optional[bool] = Field(None, description="Physical decks only: card is in the collection")


class CardInfo(_Model):
    name: str
    oracle_id: Optional[str] = None
    type_line: str = ""
    oracle_text: str = ""
    color_identity: str = ""
    legal: bool = True


class EvaluationResult(_Model):
    card: CardInfo
    deck_kind: DeckKind
    commanders: List[str]
    roles: List[RoleScore]
    strength: float = Field(ge=0.0, le=100.0)
    strength_breakdown: StrengthBreakdown
    edhrec: EdhrecStats
    model: ModelSignal
    candidates: List[Candidate]
    warnings: List[str] = Field(default_factory=list)

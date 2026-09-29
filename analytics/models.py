"""
Domain models for deck analytics inputs and standardized outputs.

Provides Pydantic models for standard inputs (decklist, commander card identifiers)
and standardized output schemas covering meta scores (salt, power), card
recommendations, synergy, and popularity metrics.
"""
from __future__ import annotations

import re
from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field, field_validator

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def _validate_non_empty_str(v: str, field_name: str = "field") -> str:
    """Helper to validate that a string is not empty or whitespace only."""
    if not isinstance(v, str):
        raise TypeError(f"{field_name} must be a string")
    cleaned = v.strip()
    if not cleaned:
        raise ValueError(f"{field_name} cannot be empty or whitespace only")
    return cleaned


def _validate_uuid_optional(v: Optional[str], field_name: str = "UUID") -> Optional[str]:
    """Helper to validate an optional UUID string."""
    if v is None:
        return None
    if not isinstance(v, str):
        raise TypeError(f"{field_name} must be a string")
    cleaned = v.strip()
    if not cleaned:
        return None
    if not _UUID_RE.match(cleaned):
        raise ValueError(f"Invalid UUID format for {field_name}: '{cleaned}'")
    return cleaned.lower()


# =============================================================================
# Input Models
# =============================================================================

class CommanderIdentifier(BaseModel):
    """
    Identifies a commander card for analytics queries.

    Attributes:
        name: Canonical name of the commander card.
        oracle_id: Scryfall Oracle ID (UUID) if known.
        scryfall_id: Specific printing Scryfall ID (UUID) if known.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Atraxa, Praetors' Voice",
                "oracle_id": "402eb06a-ff55-4623-a1bf-4ad67f8fbcf4",
                "scryfall_id": "b06ae34e-0c4a-4c28-98e3-05ec86c07a3c",
            }
        }
    )

    name: str = Field(..., description="Canonical name of the commander")
    oracle_id: Optional[str] = Field(None, description="Scryfall Oracle UUID")
    scryfall_id: Optional[str] = Field(None, description="Scryfall card printing UUID")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_non_empty_str(v, "Commander name")

    @field_validator("oracle_id", "scryfall_id")
    @classmethod
    def validate_uuids(cls, v: Optional[str]) -> Optional[str]:
        return _validate_uuid_optional(v)


class DeckCardEntry(BaseModel):
    """
    Represents an individual card entry within a decklist for analytics input.

    Attributes:
        name: Name of the card.
        quantity: Number of copies in the deck (minimum 1).
        oracle_id: Scryfall Oracle ID (UUID) if known.
        scryfall_id: Scryfall printing ID (UUID) if known.
        category: Structural board category (e.g. 'Mainboard', 'Commander', 'Sideboard').
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Sol Ring",
                "quantity": 1,
                "oracle_id": "4a58b98b-e666-41f2-850f-e23a54b321a6",
                "scryfall_id": "e674b09e-7164-4e2e-8424-6997ffb0e6ad",
                "category": "Mainboard",
            }
        }
    )

    name: str = Field(..., description="Card name")
    quantity: int = Field(default=1, ge=1, description="Quantity of the card in the deck")
    oracle_id: Optional[str] = Field(None, description="Scryfall Oracle UUID")
    scryfall_id: Optional[str] = Field(None, description="Scryfall card printing UUID")
    category: Optional[str] = Field(None, description="Structural category or board")

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: str) -> str:
        return _validate_non_empty_str(v, "Card name")

    @field_validator("oracle_id", "scryfall_id")
    @classmethod
    def validate_uuids(cls, v: Optional[str]) -> Optional[str]:
        return _validate_uuid_optional(v)


class DecklistInput(BaseModel):
    """
    Standard input payload representing a deck submitted for analytics.

    Attributes:
        deck_id: Optional external or provider identifier for the deck.
        name: Optional display name of the deck.
        commanders: List of one or more commander card identifiers.
        cards: List of cards in the main decklist (minimum 1 card).
        format: Format of the deck (defaults to 'commander').
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "deck_id": "archidekt:6862011",
                "name": "Atraxa Proliferate Engine",
                "commanders": [
                    {
                        "name": "Atraxa, Praetors' Voice",
                        "oracle_id": "402eb06a-ff55-4623-a1bf-4ad67f8fbcf4",
                    }
                ],
                "cards": [
                    {"name": "Sol Ring", "quantity": 1},
                    {"name": "Arcane Signet", "quantity": 1},
                    {"name": "Doubling Season", "quantity": 1},
                ],
                "format": "commander",
            }
        }
    )

    deck_id: Optional[str] = Field(None, description="External deck identifier")
    name: Optional[str] = Field(None, description="Display name of the deck")
    commanders: List[CommanderIdentifier] = Field(
        ..., min_length=1, description="One or more commanders"
    )
    cards: List[DeckCardEntry] = Field(
        ..., min_length=1, description="List of cards in the deck"
    )
    format: str = Field(default="commander", description="Game format")

    @field_validator("format")
    @classmethod
    def validate_format(cls, v: str) -> str:
        return _validate_non_empty_str(v, "Format")

    def total_cards(self, include_commanders: bool = True) -> int:
        """Return the sum of card quantities in the deck."""
        count = sum(entry.quantity for entry in self.cards)
        if include_commanders:
            count += len(self.commanders)
        return count

    def all_card_names(self, include_commanders: bool = True) -> List[str]:
        """Return distinct card names present in the deck."""
        names: List[str] = []
        if include_commanders:
            for cmd in self.commanders:
                if cmd.name not in names:
                    names.append(cmd.name)
        for entry in self.cards:
            if entry.name not in names:
                names.append(entry.name)
        return names


# =============================================================================
# Output Models: Meta Scores (Salt, Power)
# =============================================================================

class SaltCardDetail(BaseModel):
    """
    Detailed salt contribution for a specific card in a deck.

    Attributes:
        card_name: Name of the card.
        salt_score: Individual salt score metric (ge 0.0).
        rank: Rank among high-salt cards in the deck or meta (ge 1).
        oracle_id: Scryfall Oracle ID if known.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "card_name": "Cyclonic Rift",
                "salt_score": 2.45,
                "rank": 1,
                "oracle_id": "f516a22f-d890-48e0-bb1b-01ec25ffac62",
            }
        }
    )

    card_name: str = Field(..., description="Card name")
    salt_score: float = Field(..., ge=0.0, description="Individual salt score")
    rank: Optional[int] = Field(None, ge=1, description="Relative salt rank")
    oracle_id: Optional[str] = Field(None, description="Scryfall Oracle UUID")

    @field_validator("card_name")
    @classmethod
    def validate_card_name(cls, v: str) -> str:
        return _validate_non_empty_str(v, "Card name")

    @field_validator("oracle_id")
    @classmethod
    def validate_oracle_id(cls, v: Optional[str]) -> Optional[str]:
        return _validate_uuid_optional(v)


class SaltScore(BaseModel):
    """
    Deck salt score metrics (e.g. from Commander Salt).

    Attributes:
        score: Normalized deck salt score (ge 0.0).
        salt_sum: Total accumulated salt score across cards (ge 0.0).
        high_salt_cards: Breakdown of top salty cards contributing to the score.
        description: Qualitative summary of the salt level.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "score": 42.5,
                "salt_sum": 68.2,
                "high_salt_cards": [
                    {"card_name": "Cyclonic Rift", "salt_score": 2.45, "rank": 1},
                    {"card_name": "Rhystic Study", "salt_score": 2.10, "rank": 2},
                ],
                "description": "Moderately salty deck with multiple high-interaction staples.",
            }
        }
    )

    score: float = Field(..., ge=0.0, description="Normalized overall salt score")
    salt_sum: Optional[float] = Field(None, ge=0.0, description="Sum of raw salt values")
    high_salt_cards: List[SaltCardDetail] = Field(
        default_factory=list, description="Top salty cards in deck"
    )
    description: Optional[str] = Field(None, description="Human-readable description")


class PowerScore(BaseModel):
    """
    Deck power level evaluation (e.g. 1-10 power scale).

    Attributes:
        score: Power score on a standard 0.0 to 10.0 scale.
        tier: Descriptive tier name (e.g. Casual, Focused, Optimized, cEDH).
        description: Summary of the power assessment rationale.
        breakdown: Detailed component scores (e.g. speed, consistency, interaction).
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "score": 7.5,
                "tier": "Optimized",
                "description": "High synergy and efficient ramp with consistent win conditions.",
                "breakdown": {
                    "speed": 8.0,
                    "consistency": 7.5,
                    "interaction": 7.0,
                    "combos": 2,
                },
            }
        }
    )

    score: float = Field(..., ge=0.0, le=10.0, description="Power rating from 0.0 to 10.0")
    tier: Optional[str] = Field(None, description="Power tier classification")
    description: Optional[str] = Field(None, description="Rationale for the power rating")
    breakdown: dict[str, Any] = Field(
        default_factory=dict, description="Detailed component metrics"
    )


class MetaScores(BaseModel):
    """
    Aggregated meta scores encompassing salt, power, and provider-specific ratings.

    Attributes:
        salt: Salt score metrics if evaluated.
        power: Power score evaluation if evaluated.
        meta_rank: Optional meta ranking percentile or score.
        provider_metrics: Arbitrary additional provider-specific metrics.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "salt": {
                    "score": 35.0,
                    "salt_sum": 52.0,
                    "high_salt_cards": [],
                    "description": "Low to moderate salt.",
                },
                "power": {
                    "score": 6.8,
                    "tier": "Focused",
                    "description": "Well-tuned casual deck.",
                },
                "meta_rank": 82.5,
                "provider_metrics": {"edhrec_rank": 142},
            }
        }
    )

    salt: Optional[SaltScore] = Field(None, description="Salt rating breakdown")
    power: Optional[PowerScore] = Field(None, description="Power rating breakdown")
    meta_rank: Optional[float] = Field(None, ge=0.0, description="Meta percentile or rank")
    provider_metrics: dict[str, Any] = Field(
        default_factory=dict, description="Provider-specific meta indicators"
    )


# =============================================================================
# Output Models: Card Recommendations & Cuts
# =============================================================================

class CardRecommendation(BaseModel):
    """
    Individual card recommendation for inclusion in a deck.

    Attributes:
        card_name: Name of the suggested card.
        oracle_id: Scryfall Oracle ID if known.
        score: Recommendation strength or score.
        synergy: Synergy score with commander or deck theme (-1.0 to 1.0).
        inclusion_rate: Fraction of decks featuring this card (0.0 to 1.0).
        reason: Justification for the recommendation.
        categories: Archetype or functional categories (e.g. 'Ramp', 'Synergy').
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "card_name": "Evolution Sage",
                "oracle_id": "0f63e007-88f2-491c-b25c-0c159239ba5c",
                "score": 0.88,
                "synergy": 0.65,
                "inclusion_rate": 0.72,
                "reason": "Proliferates on every land drop, synergizing with +1/+1 counters.",
                "categories": ["Synergy", "Proliferate"],
            }
        }
    )

    card_name: str = Field(..., description="Recommended card name")
    oracle_id: Optional[str] = Field(None, description="Scryfall Oracle UUID")
    score: Optional[float] = Field(None, description="General recommendation score")
    synergy: Optional[float] = Field(
        None, ge=-1.0, le=1.0, description="Synergy rating (-1.0 to 1.0)"
    )
    inclusion_rate: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Inclusion rate in matching archetype"
    )
    reason: Optional[str] = Field(None, description="Recommendation rationale")
    categories: List[str] = Field(
        default_factory=list, description="Associated functional tags/categories"
    )
    sources: List[str] = Field(
        default_factory=list, description="Provider sources recommending this card"
    )

    @field_validator("card_name")
    @classmethod
    def validate_card_name(cls, v: str) -> str:
        return _validate_non_empty_str(v, "Card name")

    @field_validator("oracle_id")
    @classmethod
    def validate_oracle_id(cls, v: Optional[str]) -> Optional[str]:
        return _validate_uuid_optional(v)


class CardCutRecommendation(BaseModel):
    """
    Suggestion to remove or replace a card currently in the deck.

    Attributes:
        card_name: Name of the card suggested for removal.
        oracle_id: Scryfall Oracle ID if known.
        synergy: Synergy score indicating low alignment (-1.0 to 1.0).
        reason: Explanation for why this card is a candidate cut.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "card_name": "Vryn Wingmare",
                "oracle_id": "18f2f451-b06f-4d92-bb8a-2bb6da417578",
                "synergy": -0.25,
                "reason": "Tax effect slows your own noncreature counter spells.",
            }
        }
    )

    card_name: str = Field(..., description="Card suggested for cut")
    oracle_id: Optional[str] = Field(None, description="Scryfall Oracle UUID")
    synergy: Optional[float] = Field(
        None, ge=-1.0, le=1.0, description="Synergy rating (-1.0 to 1.0)"
    )
    reason: Optional[str] = Field(None, description="Reason to cut this card")
    sources: List[str] = Field(
        default_factory=list, description="Provider sources recommending this cut"
    )

    @field_validator("card_name")
    @classmethod
    def validate_card_name(cls, v: str) -> str:
        return _validate_non_empty_str(v, "Card name")

    @field_validator("oracle_id")
    @classmethod
    def validate_oracle_id(cls, v: Optional[str]) -> Optional[str]:
        return _validate_uuid_optional(v)


class CardRecommendations(BaseModel):
    """
    Collection of additions and suggested cuts for a decklist.

    Attributes:
        items: List of recommended additions.
        cuts: List of suggested cuts from current decklist.
        total: Total count of recommendations available.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "items": [
                    {
                        "card_name": "Evolution Sage",
                        "score": 0.88,
                        "synergy": 0.65,
                        "inclusion_rate": 0.72,
                        "reason": "Proliferates counters on land drops.",
                    }
                ],
                "cuts": [
                    {
                        "card_name": "Vryn Wingmare",
                        "synergy": -0.25,
                        "reason": "Non-synergistic stax piece.",
                    }
                ],
                "total": 1,
            }
        }
    )

    items: List[CardRecommendation] = Field(
        default_factory=list, description="Recommended additions"
    )
    cuts: List[CardCutRecommendation] = Field(
        default_factory=list, description="Suggested cuts"
    )
    total: Optional[int] = Field(
        default=None,
        ge=0,
        validate_default=True,
        description="Total recommendation count (defaults to len(items))",
    )

    @field_validator("total", mode="before")
    @classmethod
    def populate_total(cls, v: Optional[int], info) -> int:
        """Default `total` to the number of recommended items when omitted."""
        if v is None:
            items = info.data.get("items") or []
            return len(items)
        return v


# =============================================================================
# Output Models: Synergy Metrics
# =============================================================================

class SynergyMetric(BaseModel):
    """
    Synergy measurement between an individual card and the commander / deck theme.

    Attributes:
        card_name: Name of the card being measured.
        synergy_score: Normalized synergy delta (-1.0 to 1.0, e.g. +0.40 = +40% synergy).
        commander_name: Name of the specific commander evaluated against.
        context: Contextual explanation (e.g. '+42% more likely to appear with Atraxa').
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "card_name": "Deepglow Skate",
                "synergy_score": 0.58,
                "commander_name": "Atraxa, Praetors' Voice",
                "context": "+58% synergy compared to general multicolor decks",
            }
        }
    )

    card_name: str = Field(..., description="Card name")
    synergy_score: float = Field(
        ..., ge=-1.0, le=1.0, description="Synergy rating between -1.0 and 1.0"
    )
    commander_name: Optional[str] = Field(None, description="Commander evaluated against")
    context: Optional[str] = Field(None, description="Synergy description / context")

    @field_validator("card_name")
    @classmethod
    def validate_card_name(cls, v: str) -> str:
        return _validate_non_empty_str(v, "Card name")


class DeckSynergy(BaseModel):
    """
    Deck-wide synergy profile and individual card synergy breakdown.

    Attributes:
        overall_synergy: Aggregate deck synergy score (-1.0 to 1.0).
        card_synergies: Synergy metrics for cards in the deck.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "overall_synergy": 0.44,
                "card_synergies": [
                    {
                        "card_name": "Deepglow Skate",
                        "synergy_score": 0.58,
                        "commander_name": "Atraxa, Praetors' Voice",
                    },
                    {
                        "card_name": "Inexorable Tide",
                        "synergy_score": 0.49,
                        "commander_name": "Atraxa, Praetors' Voice",
                    },
                ],
            }
        }
    )

    overall_synergy: Optional[float] = Field(
        None, ge=-1.0, le=1.0, description="Overall aggregate synergy score"
    )
    card_synergies: List[SynergyMetric] = Field(
        default_factory=list, description="Per-card synergy metrics"
    )


# =============================================================================
# Output Models: Popularity Metrics
# =============================================================================

class PopularityMetric(BaseModel):
    """
    Popularity and inclusion statistics for a single card in the broader meta.

    Attributes:
        card_name: Name of the card.
        deck_count: Number of tracked decks including this card.
        percentage: Percentage of tracked decks including this card (0.0 to 100.0).
        rank: Popularity rank within the commander archetype or format.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "card_name": "Sol Ring",
                "deck_count": 845210,
                "percentage": 84.5,
                "rank": 1,
            }
        }
    )

    card_name: str = Field(..., description="Card name")
    deck_count: Optional[int] = Field(None, ge=0, description="Deck occurrence count")
    percentage: Optional[float] = Field(
        None, ge=0.0, le=100.0, description="Inclusion percentage (0.0 to 100.0)"
    )
    rank: Optional[int] = Field(None, ge=1, description="Popularity rank")

    @field_validator("card_name")
    @classmethod
    def validate_card_name(cls, v: str) -> str:
        return _validate_non_empty_str(v, "Card name")


class DeckPopularity(BaseModel):
    """
    Meta-level popularity statistics for a deck's commander and card choices.

    Attributes:
        rank: Popularity rank of the commander (ge 1).
        num_decks: Number of decks recorded in meta for this commander.
        popularity_percentile: Popularity percentile ranking (0.0 to 100.0).
        card_popularity: Popularity breakdown for key cards in the deck.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "rank": 3,
                "num_decks": 28410,
                "popularity_percentile": 99.2,
                "card_popularity": [
                    {
                        "card_name": "Sol Ring",
                        "deck_count": 27100,
                        "percentage": 95.4,
                        "rank": 1,
                    }
                ],
            }
        }
    )

    rank: Optional[int] = Field(None, ge=1, description="Commander popularity rank")
    num_decks: Optional[int] = Field(
        None, ge=0, description="Total decks recorded in meta"
    )
    popularity_percentile: Optional[float] = Field(
        None, ge=0.0, le=100.0, description="Meta percentile"
    )
    card_popularity: List[PopularityMetric] = Field(
        default_factory=list, description="Per-card popularity metrics"
    )


# =============================================================================
# Aggregated Output Model
# =============================================================================

class DeckAnalyticsResult(BaseModel):
    """
    Unified analytics response aggregating meta scores, recommendations,
    synergy, and popularity metrics for a deck.

    Attributes:
        provider_name: Identifier of the analytics provider that generated this result.
        timestamp: ISO-8601 timestamp string when analysis was performed.
        meta_scores: Power and salt scores.
        recommendations: Suggested additions and cuts.
        synergy: Deck and card synergy data.
        popularity: Archetype and card popularity statistics.
        raw_metadata: Provider-specific raw response payload or extra fields.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "provider_name": "edhrec",
                "timestamp": "2026-09-29T12:00:00Z",
                "meta_scores": {
                    "salt": {"score": 25.0, "salt_sum": 30.0},
                    "power": {"score": 7.0, "tier": "Optimized"},
                },
                "recommendations": {
                    "items": [
                        {"card_name": "Evolution Sage", "synergy": 0.65}
                    ],
                    "cuts": [],
                    "total": 1,
                },
                "synergy": {
                    "overall_synergy": 0.45,
                    "card_synergies": [],
                },
                "popularity": {
                    "rank": 12,
                    "num_decks": 15400,
                },
                "raw_metadata": {"version": "v1"},
            }
        }
    )

    provider_name: str = Field(..., description="Name of the provider")
    timestamp: Optional[str] = Field(None, description="ISO timestamp of analysis")
    meta_scores: Optional[MetaScores] = Field(None, description="Meta scores")
    recommendations: Optional[CardRecommendations] = Field(
        None, description="Card recommendations and cuts"
    )
    synergy: Optional[DeckSynergy] = Field(None, description="Synergy metrics")
    popularity: Optional[DeckPopularity] = Field(None, description="Popularity metrics")
    raw_metadata: dict[str, Any] = Field(
        default_factory=dict, description="Arbitrary raw provider data"
    )

    @field_validator("provider_name")
    @classmethod
    def validate_provider_name(cls, v: str) -> str:
        return _validate_non_empty_str(v, "Provider name")

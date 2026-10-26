"""
Consolidated deck intake orchestration facade.

Re-exports all public symbols from deck_intake.py so downstream consumers
can import from either `deck_intake` or `intake`.
"""

from src.server.domain.decks.deck_slots import (
    SIDEBOARD_MAPPED_FLAG,
    VARIANT_SLOTS,
    DeckSlot,
    migrate_serialized_deck,
    normalize_slot,
)
from src.server.domain.intake.deck_intake import (
    ANY_NUMBER_ALLOWED_CARDS,
    BASIC_LAND_NAMES,
    ConsolidatedDeckIntake,
    DeckIntakeError,
    DeckIntakePayload,
    DeckIntakeResult,
    DeckIntakeService,
    DeckIntakeSummary,
    DeckIntakeValidationError,
    IntakeValidationResult,
    default_deck_intake_service,
    process_deck_intake,
    validate_deck_intake,
)

__all__ = [
    "ANY_NUMBER_ALLOWED_CARDS",
    "BASIC_LAND_NAMES",
    "ConsolidatedDeckIntake",
    "DeckIntakeError",
    "DeckIntakePayload",
    "DeckIntakeResult",
    "DeckIntakeService",
    "DeckIntakeSummary",
    "DeckIntakeValidationError",
    "DeckSlot",
    "IntakeValidationResult",
    "SIDEBOARD_MAPPED_FLAG",
    "VARIANT_SLOTS",
    "default_deck_intake_service",
    "migrate_serialized_deck",
    "normalize_slot",
    "process_deck_intake",
    "validate_deck_intake",
]

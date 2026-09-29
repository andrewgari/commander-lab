"""
Consolidated deck intake orchestration facade.

Re-exports all public symbols from deck_intake.py so downstream consumers
can import from either `deck_intake` or `intake`.
"""

from deck_intake import (
    ANY_NUMBER_ALLOWED_CARDS,
    BASIC_LAND_NAMES,
    ConsolidatedDeckIntake,
    DeckIntakeError,
    DeckIntakePayload,
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
    "DeckIntakeService",
    "DeckIntakeSummary",
    "DeckIntakeValidationError",
    "IntakeValidationResult",
    "default_deck_intake_service",
    "process_deck_intake",
    "validate_deck_intake",
]

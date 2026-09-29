"""
Unified deck intake orchestration module and service.

Integrates the multi-format Commander decklist parser (decklist_parser.py)
and user intent & playstyle schema (user_intent.py) into a unified intake pipeline.
Validates incoming payloads against card structure and user intent rules,
returning a consolidated deck intake object ready for downstream analysis.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set, Tuple, Union

from pydantic import BaseModel, ConfigDict, Field

from analytics.models import CommanderIdentifier, DeckCardEntry, DecklistInput
from decklist_parser import DeckSection, ParsedCard, ParsedDeck, parse_decklist
from user_intent import (
    UserDeckIntent,
    parse_user_intent,
    resolve_intent_defaults,
    validate_intent,
)

# Canonical basic land names allowed multiple copies in singleton formats
BASIC_LAND_NAMES: Set[str] = {
    "Plains",
    "Island",
    "Swamp",
    "Mountain",
    "Forest",
    "Wastes",
    "Snow-Covered Plains",
    "Snow-Covered Island",
    "Snow-Covered Swamp",
    "Snow-Covered Mountain",
    "Snow-Covered Forest",
}

# Cards with explicit text overriding singleton limits ("A deck can have any number of cards named...")
ANY_NUMBER_ALLOWED_CARDS: Set[str] = {
    "Relentless Rats",
    "Shadowborn Apostle",
    "Persistent Petitioners",
    "Dragon's Approach",
    "Rat Colony",
    "Slime Against Humanity",
    "Templar Knight",
    "Hare Apparent",
}


# =============================================================================
# Exceptions
# =============================================================================

class DeckIntakeError(Exception):
    """Base exception for deck intake orchestration failures."""
    pass


class DeckIntakeValidationError(DeckIntakeError, ValueError):
    """Raised when an intake payload fails card structure or user intent validation."""

    def __init__(
        self,
        errors: List[str],
        warnings: Optional[List[str]] = None,
        message: Optional[str] = None,
    ):
        self.errors = list(errors)
        self.warnings = list(warnings or [])
        if message is None:
            message = f"Deck intake validation failed with {len(self.errors)} error(s): {'; '.join(self.errors)}"
        super().__init__(message)


# =============================================================================
# Pydantic Schemas for Payloads and Responses
# =============================================================================

class DeckIntakePayload(BaseModel):
    """
    Unified input payload for deck intake.

    Supports either raw decklist text (MTGA, MTGO, plain text) or structured card lists,
    along with optional user intent and deck metadata.
    """
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "name": "Atraxa Proliferate",
                "format": "commander",
                "decklist": "Commander\n1 Atraxa, Praetors' Voice\n\nDeck\n1 Sol Ring\n1 Arcane Signet\n",
                "user_intent": {
                    "deck_vision": {"intent": "tune_up", "description": "High power pod"},
                    "target_power_level": {"scale": 8},
                },
            }
        }
    )

    name: Optional[str] = Field(None, description="Display or project name for the deck")
    format: str = Field("commander", description="Game format (default: 'commander')")
    deck_id: Optional[str] = Field(None, description="External or provider deck identifier")
    decklist: Optional[str] = Field(None, description="Raw decklist text in MTGA, MTGO, or plain text format")
    cards: Optional[List[Dict[str, Any]]] = Field(
        None, description="Pre-structured list of card dictionaries with at least 'name' and optional 'quantity', 'section'"
    )
    commanders: Optional[List[str]] = Field(
        None, description="Explicit list of commander names (useful if not tagged in decklist text)"
    )
    user_intent: Optional[Dict[str, Any]] = Field(
        None, description="User intent payload conforming to user_intent schema"
    )
    strict_coherence: bool = Field(
        True, description="Enforce strict coherence between power level and budget"
    )
    strict_deck_size: bool = Field(
        False, description="Enforce strict 100-card deck size for commander format"
    )
    strict_singleton: bool = Field(
        False, description="Enforce strict singleton rules for non-basic lands"
    )
    metadata: Optional[Dict[str, Any]] = Field(
        default_factory=dict, description="Arbitrary client metadata"
    )


# =============================================================================
# Intake Summary and Consolidated Deck Intake Models
# =============================================================================

@dataclass
class DeckIntakeSummary:
    """Summary metrics of an ingested decklist."""
    total_cards: int
    commander_count: int
    mainboard_count: int
    sideboard_count: int
    unique_cards: int
    is_valid_commander_count: bool
    is_100_cards: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class IntakeValidationResult:
    """Detailed validation verdict for card structure and user intent."""
    is_valid: bool
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "is_valid": self.is_valid,
            "errors": list(self.errors),
            "warnings": list(self.warnings),
        }


@dataclass
class ConsolidatedDeckIntake:
    """
    Consolidated deck intake object containing normalized cards, validated user intent,
    and metadata ready for downstream analysis providers.
    """
    name: str
    format: str
    deck_id: Optional[str]
    commanders: List[CommanderIdentifier]
    cards: List[DeckCardEntry]
    sideboard: List[DeckCardEntry]
    parsed_deck: ParsedDeck
    user_intent: UserDeckIntent
    summary: DeckIntakeSummary
    warnings: List[str] = field(default_factory=list)
    raw_decklist: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def total_cards(self, include_commanders: bool = True, include_sideboard: bool = False) -> int:
        """Calculate total card count across specified sections."""
        count = sum(c.quantity for c in self.cards)
        if include_commanders:
            count += len(self.commanders)
        if include_sideboard:
            count += sum(c.quantity for c in self.sideboard)
        return count

    def all_card_names(
        self, include_commanders: bool = True, include_sideboard: bool = False
    ) -> List[str]:
        """Return unique list of card names in the intake object."""
        names: List[str] = []
        if include_commanders:
            for c in self.commanders:
                if c.name not in names:
                    names.append(c.name)
        for c in self.cards:
            if c.name not in names:
                names.append(c.name)
        if include_sideboard:
            for c in self.sideboard:
                if c.name not in names:
                    names.append(c.name)
        return names

    def to_analytics_input(self) -> DecklistInput:
        """
        Convert this consolidated intake object into a standard DecklistInput
        ready for downstream BaseAnalyticsProvider queries.
        """
        return DecklistInput(
            deck_id=self.deck_id,
            name=self.name,
            commanders=list(self.commanders),
            cards=list(self.cards),
            format=self.format,
        )

    def to_dict(self) -> Dict[str, Any]:
        """Convert the entire intake object to a JSON-serializable dictionary."""
        return {
            "name": self.name,
            "format": self.format,
            "deck_id": self.deck_id,
            "commanders": [c.model_dump() for c in self.commanders],
            "cards": [c.model_dump() for c in self.cards],
            "sideboard": [c.model_dump() for c in self.sideboard],
            "user_intent": self.user_intent.to_dict(),
            "summary": self.summary.to_dict(),
            "warnings": list(self.warnings),
            "raw_decklist": self.raw_decklist,
            "metadata": dict(self.metadata),
        }

    def analyze(
        self,
        provider: Any = None,
        provider_name: Optional[str] = None,
    ) -> Any:
        """
        Execute analytics on this deck intake object using a given or registered provider.

        Args:
            provider: Concrete BaseAnalyticsProvider instance.
            provider_name: Name of registered provider (retrieved from analytics registry).
        """
        if provider is None:
            if not provider_name:
                raise ValueError("Must provide either a provider instance or provider_name")
            from analytics.registry import get_provider
            provider = get_provider(provider_name)

        return provider.analyze_deck(self.to_analytics_input())


# =============================================================================
# Deck Intake Orchestration Service
# =============================================================================

class DeckIntakeService:
    """
    Unified service for ingesting, validating, and consolidating decklists and user intent.
    """

    def __init__(
        self,
        card_resolver: Optional[Callable[[str], Optional[Dict[str, Any]]]] = None,
    ):
        """
        Initialize the intake service.

        Args:
            card_resolver: Optional callable `(name: str) -> dict` returning card metadata
                           such as oracle_id and scryfall_id for enrichment.
        """
        self.card_resolver = card_resolver

    def validate(
        self,
        decklist: Optional[Union[str, ParsedDeck, List[Any], DecklistInput]] = None,
        intent: Optional[Union[Dict[str, Any], UserDeckIntent]] = None,
        name: Optional[str] = None,
        format: str = "commander",
        commanders: Optional[List[str]] = None,
        deck_id: Optional[str] = None,
        strict_coherence: bool = True,
        strict_deck_size: bool = False,
        strict_singleton: bool = False,
    ) -> IntakeValidationResult:
        """
        Validate incoming decklist structure and user intent without raising exceptions.
        Returns an IntakeValidationResult with is_valid, errors, and warnings.
        """
        errors: List[str] = []
        warnings: List[str] = []

        # 1. Parse and validate card structure
        parsed_deck, card_errors, card_warnings = self._parse_and_validate_cards(
            decklist=decklist,
            format=format,
            explicit_commanders=commanders,
            strict_deck_size=strict_deck_size,
            strict_singleton=strict_singleton,
        )
        errors.extend(card_errors)
        warnings.extend(card_warnings)

        # 2. Validate user intent
        intent_errors, intent_warnings = self._validate_user_intent(
            intent=intent,
            strict_coherence=strict_coherence,
        )
        errors.extend(intent_errors)
        warnings.extend(intent_warnings)

        return IntakeValidationResult(
            is_valid=(len(errors) == 0),
            errors=errors,
            warnings=warnings,
        )

    def process(
        self,
        decklist: Optional[Union[str, ParsedDeck, List[Any], DecklistInput]] = None,
        intent: Optional[Union[Dict[str, Any], UserDeckIntent]] = None,
        name: Optional[str] = None,
        format: str = "commander",
        commanders: Optional[List[str]] = None,
        deck_id: Optional[str] = None,
        strict_coherence: bool = True,
        strict_deck_size: bool = False,
        strict_singleton: bool = False,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> ConsolidatedDeckIntake:
        """
        Process incoming decklist and user intent into a consolidated deck intake object.
        Raises DeckIntakeValidationError if any validation check fails.
        """
        errors: List[str] = []
        warnings: List[str] = []

        raw_decklist_str = decklist if isinstance(decklist, str) else None

        # 1. Parse and validate cards
        if isinstance(decklist, DecklistInput):
            if not name and decklist.name:
                name = decklist.name
            if not deck_id and decklist.deck_id:
                deck_id = decklist.deck_id

        parsed_deck, card_errors, card_warnings = self._parse_and_validate_cards(
            decklist=decklist,
            format=format,
            explicit_commanders=commanders,
            strict_deck_size=strict_deck_size,
            strict_singleton=strict_singleton,
        )
        errors.extend(card_errors)
        warnings.extend(card_warnings)

        # 2. Validate user intent
        intent_errors, intent_warnings = self._validate_user_intent(
            intent=intent,
            strict_coherence=strict_coherence,
        )
        errors.extend(intent_errors)
        warnings.extend(intent_warnings)

        # If any validation errors occurred, fail fast with aggregated errors
        if errors:
            raise DeckIntakeValidationError(errors=errors, warnings=warnings)

        # 3. Parse intent into UserDeckIntent
        if isinstance(intent, UserDeckIntent):
            user_deck_intent = intent
        elif isinstance(intent, dict):
            user_deck_intent = parse_user_intent(intent, strict_coherence=strict_coherence)
        else:
            # Fall back to default intent
            user_deck_intent = parse_user_intent({}, strict_coherence=strict_coherence)

        # 4. Construct normalized card entries
        cmd_entries: List[CommanderIdentifier] = []
        card_entries: List[DeckCardEntry] = []
        sb_entries: List[DeckCardEntry] = []

        for card in parsed_deck.commander:
            resolved_meta = self._resolve_card_meta(card.name)
            cmd_entries.append(
                CommanderIdentifier(
                    name=card.name,
                    oracle_id=card.get("oracle_id") or resolved_meta.get("oracle_id"),
                    scryfall_id=card.get("scryfall_id") or resolved_meta.get("scryfall_id"),
                )
            )

        for card in parsed_deck.mainboard:
            resolved_meta = self._resolve_card_meta(card.name)
            card_entries.append(
                DeckCardEntry(
                    name=card.name,
                    quantity=card.quantity,
                    category=card.get("category", "Mainboard"),
                    oracle_id=card.get("oracle_id") or resolved_meta.get("oracle_id"),
                    scryfall_id=card.get("scryfall_id") or resolved_meta.get("scryfall_id"),
                )
            )

        for card in parsed_deck.sideboard:
            resolved_meta = self._resolve_card_meta(card.name)
            sb_entries.append(
                DeckCardEntry(
                    name=card.name,
                    quantity=card.quantity,
                    category=card.get("category", "Sideboard"),
                    oracle_id=card.get("oracle_id") or resolved_meta.get("oracle_id"),
                    scryfall_id=card.get("scryfall_id") or resolved_meta.get("scryfall_id"),
                )
            )

        # 5. Determine deck name
        effective_name = name
        if not effective_name:
            if cmd_entries:
                cmdr_names = " & ".join(c.name for c in cmd_entries)
                effective_name = f"{cmdr_names} Commander Deck"
            else:
                effective_name = "Untitled Deck"

        # 6. Build summary
        total_main = sum(c.quantity for c in card_entries)
        total_cmd = len(cmd_entries)
        total_sb = sum(c.quantity for c in sb_entries)
        total_deck = total_main + total_cmd
        unique_names = set(c.name for c in cmd_entries) | set(c.name for c in card_entries)

        summary = DeckIntakeSummary(
            total_cards=total_deck,
            commander_count=total_cmd,
            mainboard_count=total_main,
            sideboard_count=total_sb,
            unique_cards=len(unique_names),
            is_valid_commander_count=(1 <= total_cmd <= 2) if format.lower() == "commander" else True,
            is_100_cards=(total_deck == 100),
        )

        return ConsolidatedDeckIntake(
            name=effective_name,
            format=format,
            deck_id=deck_id,
            commanders=cmd_entries,
            cards=card_entries,
            sideboard=sb_entries,
            parsed_deck=parsed_deck,
            user_intent=user_deck_intent,
            summary=summary,
            warnings=warnings,
            raw_decklist=raw_decklist_str,
            metadata=dict(metadata or {}),
        )

    def process_payload(
        self, payload: Union[Dict[str, Any], DeckIntakePayload]
    ) -> ConsolidatedDeckIntake:
        """
        Process a unified payload dictionary or DeckIntakePayload model.
        """
        if isinstance(payload, DeckIntakePayload):
            data = payload.model_dump()
        elif isinstance(payload, dict):
            # Validate through Pydantic model for schema normalization
            parsed_payload = DeckIntakePayload(**payload)
            data = parsed_payload.model_dump()
        else:
            raise DeckIntakeValidationError(
                errors=["Payload must be a dictionary or DeckIntakePayload instance"]
            )

        return self.process(
            decklist=data.get("decklist") or data.get("cards"),
            intent=data.get("user_intent"),
            name=data.get("name"),
            format=data.get("format", "commander"),
            commanders=data.get("commanders"),
            deck_id=data.get("deck_id"),
            strict_coherence=data.get("strict_coherence", True),
            strict_deck_size=data.get("strict_deck_size", False),
            strict_singleton=data.get("strict_singleton", False),
            metadata=data.get("metadata"),
        )

    # -------------------------------------------------------------------------
    # Internal Helpers
    # -------------------------------------------------------------------------

    def _resolve_card_meta(self, card_name: str) -> Dict[str, Any]:
        """Call optional resolver to enrich cards with Scryfall/Oracle IDs."""
        if self.card_resolver:
            try:
                res = self.card_resolver(card_name)
                if isinstance(res, dict):
                    return res
            except Exception:
                pass
        return {}

    def _parse_and_validate_cards(
        self,
        decklist: Optional[Union[str, ParsedDeck, List[Any], DecklistInput]],
        format: str,
        explicit_commanders: Optional[List[str]],
        strict_deck_size: bool,
        strict_singleton: bool,
    ) -> Tuple[ParsedDeck, List[str], List[str]]:
        """
        Parse and validate decklist cards, returning (parsed_deck, errors, warnings).
        """
        errors: List[str] = []
        warnings: List[str] = []

        if decklist is None:
            return ParsedDeck(), ["No decklist provided. Please supply raw decklist text or cards."], []

        # Convert to ParsedDeck
        parsed: ParsedDeck
        if isinstance(decklist, ParsedDeck):
            parsed = decklist
        elif isinstance(decklist, str):
            if not decklist.strip():
                return ParsedDeck(), ["Decklist text is empty."], []
            parsed = parse_decklist(decklist)
        elif isinstance(decklist, DecklistInput):
            parsed = ParsedDeck()
            for cmd in decklist.commanders:
                parsed.append(
                    ParsedCard(
                        name=cmd.name,
                        quantity=1,
                        section=DeckSection.COMMANDER,
                        oracle_id=cmd.oracle_id,
                        scryfall_id=cmd.scryfall_id,
                    )
                )
            for card in decklist.cards:
                parsed.append(
                    ParsedCard(
                        name=card.name,
                        quantity=card.quantity,
                        section=DeckSection.MAINBOARD,
                        category=card.category or "Mainboard",
                        oracle_id=card.oracle_id,
                        scryfall_id=card.scryfall_id,
                    )
                )
        elif isinstance(decklist, list):
            parsed = ParsedDeck()
            for item in decklist:
                if isinstance(item, ParsedCard):
                    parsed.append(item)
                elif isinstance(item, dict):
                    name = item.get("name")
                    if not name or not str(name).strip():
                        errors.append("Card entry missing valid 'name' attribute.")
                        continue
                    qty = item.get("quantity", 1)
                    sec = item.get("section", DeckSection.MAINBOARD)
                    parsed.append(
                        ParsedCard(
                            name=str(name).strip(),
                            quantity=qty,
                            section=sec,
                            set_code=item.get("set_code"),
                            collector_number=item.get("collector_number"),
                            foil=bool(item.get("foil", False)),
                            oracle_id=item.get("oracle_id"),
                            scryfall_id=item.get("scryfall_id"),
                        )
                    )
                else:
                    errors.append(f"Unsupported card entry type: {type(item).__name__}")
        else:
            return (
                ParsedDeck(),
                [f"Unsupported decklist format type: {type(decklist).__name__}"],
                [],
            )

        if len(parsed) == 0:
            errors.append("Decklist contains no recognized or valid card entries.")
            return parsed, errors, warnings

        # Apply explicit commanders if provided
        if explicit_commanders:
            clean_cmd_names = {c.strip().lower() for c in explicit_commanders if c.strip()}
            for card in parsed:
                if card.name.strip().lower() in clean_cmd_names:
                    card["section"] = DeckSection.COMMANDER

        # Validate card quantities
        for card in parsed:
            if not isinstance(card.quantity, int) or card.quantity <= 0:
                errors.append(f"Invalid quantity for card '{card.name}': {card.quantity}. Must be >= 1.")

        # Check commander requirements for commander format
        is_commander_format = format.lower() == "commander"
        cmdr_cards = parsed.commander
        main_cards = parsed.mainboard

        if is_commander_format:
            if len(cmdr_cards) == 0:
                errors.append(
                    "In 'commander' format, at least one commander must be designated or tagged in the decklist."
                )
            elif len(cmdr_cards) > 2:
                warnings.append(
                    f"More than 2 commanders identified ({len(cmdr_cards)}). Standard Commander allows at most 2."
                )

        # Deck size checks
        total_deck_cards = sum(c.quantity for c in cmdr_cards) + sum(c.quantity for c in main_cards)
        if is_commander_format:
            if total_deck_cards != 100:
                msg = f"Commander deck contains {total_deck_cards} cards (standard is 100 cards)."
                if strict_deck_size:
                    errors.append(msg)
                else:
                    warnings.append(msg)

        # Singleton rule checks
        if is_commander_format:
            card_counts: Dict[str, int] = {}
            for card in cmdr_cards + main_cards:
                canonical = card.name.strip()
                card_counts[canonical] = card_counts.get(canonical, 0) + card.quantity

            for card_name, count in card_counts.items():
                if count > 1 and card_name not in BASIC_LAND_NAMES and card_name not in ANY_NUMBER_ALLOWED_CARDS:
                    msg = f"Non-basic card '{card_name}' has {count} copies in a singleton Commander deck."
                    if strict_singleton:
                        errors.append(msg)
                    else:
                        warnings.append(msg)

        return parsed, errors, warnings

    def _validate_user_intent(
        self,
        intent: Optional[Union[Dict[str, Any], UserDeckIntent]],
        strict_coherence: bool,
    ) -> Tuple[List[str], List[str]]:
        """
        Validate user intent against business rules, returning (errors, warnings).
        """
        errors: List[str] = []
        warnings: List[str] = []

        if intent is None:
            # None intent is permitted; defaults will be resolved
            return errors, warnings

        if isinstance(intent, UserDeckIntent):
            intent_dict = intent.to_dict()
        elif isinstance(intent, dict):
            intent_dict = intent
        else:
            errors.append(f"User intent must be a dict or UserDeckIntent, got {type(intent).__name__}")
            return errors, warnings

        is_valid, validation_errors = validate_intent(intent_dict, strict_coherence=strict_coherence)
        if not is_valid:
            errors.extend(validation_errors)

        return errors, warnings


# =============================================================================
# Module Convenience Functions & Singletons
# =============================================================================

default_deck_intake_service = DeckIntakeService()


def process_deck_intake(
    decklist: Optional[Union[str, ParsedDeck, List[Any], DecklistInput]] = None,
    intent: Optional[Union[Dict[str, Any], UserDeckIntent]] = None,
    name: Optional[str] = None,
    format: str = "commander",
    commanders: Optional[List[str]] = None,
    deck_id: Optional[str] = None,
    strict_coherence: bool = True,
    strict_deck_size: bool = False,
    strict_singleton: bool = False,
    metadata: Optional[Dict[str, Any]] = None,
) -> ConsolidatedDeckIntake:
    """
    Process incoming decklist and intent into a consolidated deck intake object.
    """
    return default_deck_intake_service.process(
        decklist=decklist,
        intent=intent,
        name=name,
        format=format,
        commanders=commanders,
        deck_id=deck_id,
        strict_coherence=strict_coherence,
        strict_deck_size=strict_deck_size,
        strict_singleton=strict_singleton,
        metadata=metadata,
    )


def validate_deck_intake(
    decklist: Optional[Union[str, ParsedDeck, List[Any], DecklistInput]] = None,
    intent: Optional[Union[Dict[str, Any], UserDeckIntent]] = None,
    name: Optional[str] = None,
    format: str = "commander",
    commanders: Optional[List[str]] = None,
    deck_id: Optional[str] = None,
    strict_coherence: bool = True,
    strict_deck_size: bool = False,
    strict_singleton: bool = False,
) -> IntakeValidationResult:
    """
    Validate incoming decklist structure and intent without raising exceptions.
    """
    return default_deck_intake_service.validate(
        decklist=decklist,
        intent=intent,
        name=name,
        format=format,
        commanders=commanders,
        deck_id=deck_id,
        strict_coherence=strict_coherence,
        strict_deck_size=strict_deck_size,
        strict_singleton=strict_singleton,
    )

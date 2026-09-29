# Deck Intake Orchestration Module & Service

This document describes the unified intake orchestration module (`deck_intake.py` / `intake.py`) for Commander Lab.

## Overview

The Deck Intake service integrates:
1. **Multi-Format Decklist Parser** (`decklist_parser.py`): Parses MTG Arena, MTGO, and plain-text decklists with section identification (commander, mainboard, sideboard), annotations, and split cards.
2. **User Intent & Playstyle Schema** (`user_intent.py`): Ingests and validates player deck-review goals across deck vision, target power level, budget constraints, preferred win conditions, and archetype preferences.
3. **Deck Analytics Domain Models** (`analytics.models`): Formats consolidated deck intakes directly into `DecklistInput` objects for seamless consumption by `BaseAnalyticsProvider` instances (e.g. Commander Salt, EDHRec).

---

## Architecture & Data Flow

```
                      +-----------------------------+
                      |   Raw Decklist / Cards      |
                      |  (MTGA, MTGO, Plain Text)   |
                      +--------------+--------------+
                                     |
                                     v
+------------------------+   +---------------+   +-----------------------+
|  User Review Intent    |-->|  Deck Intake  |<--| Explicit Options      |
| (Vision, Power, Budget)|   |  Orchestrator |   | (Format, Strictness)  |
+------------------------+   +-------+-------+   +-----------------------+
                                     |
                                     v
                     +-------------------------------+
                     |   Validation & Normalization  |
                     |  - Card structure & quantities|
                     |  - Commander format rules     |
                     |  - Intent rules & coherence   |
                     +---------------+---------------+
                                     |
                                     v
                     +-------------------------------+
                     |    ConsolidatedDeckIntake     |
                     |  - commanders                 |
                     |  - cards (mainboard)          |
                     |  - sideboard                  |
                     |  - user_intent                |
                     |  - summary metrics            |
                     |  - warnings                   |
                     +---------------+---------------+
                                     |
                                     v
                     +-------------------------------+
                     |      to_analytics_input()     |
                     |     -> DecklistInput          |
                     +---------------+---------------+
                                     |
                                     v
                     +-------------------------------+
                     | Downstream Analytics Provider |
                     | (Meta scores, Recommendations)|
                     +-------------------------------+
```

---

## Intake Components

### 1. `ConsolidatedDeckIntake`
The primary deliverable of the intake service. Contains:
- `name`: Resolved deck title (e.g. user-specified or derived from commander).
- `format`: Game format (`'commander'` by default).
- `deck_id`: External or provider identifier.
- `commanders`: `List[CommanderIdentifier]` (canonical name, optional Oracle/Scryfall UUIDs).
- `cards`: `List[DeckCardEntry]` (mainboard card objects with quantity >= 1).
- `sideboard`: `List[DeckCardEntry]` (sideboard or considering cards).
- `parsed_deck`: Full underlying `ParsedDeck` preserving raw annotations, set codes, and collector numbers.
- `user_intent`: Strongly-typed `UserDeckIntent` dataclass tree.
- `summary`: `DeckIntakeSummary` (total cards, commander count, mainboard count, sideboard count, unique cards, `is_valid_commander_count`, `is_100_cards`).
- `warnings`: Accumulated non-fatal warnings (e.g. deck size != 100 in non-strict mode).
- `raw_decklist`: Preserved raw input text.
- `metadata`: Arbitrary client metadata dictionary.

#### Key Methods:
- `to_analytics_input() -> DecklistInput`: Converts into standard input model for analytics engines.
- `to_dict() -> Dict[str, Any]`: Serializes the entire intake payload to JSON-compatible dictionary.
- `all_card_names(...) -> List[str]`: Queries distinct card names across sections.
- `total_cards(...) -> int`: Computes total card counts.
- `analyze(provider=None, provider_name=None)`: Executes composite analysis using a `BaseAnalyticsProvider`.

---

## Validation Rules

The service runs dual validation across card structure and user intent:

### Card Structure Validation
1. **Non-empty list**: Rejects empty strings or payloads without recognized cards.
2. **Positive quantities**: Card quantities must be positive integers (`ge 1`).
3. **Commander format rules**:
   - At least 1 commander must be present (via decklist header, inline tag `*CMDR*`, or `commanders` override).
   - Warns if more than 2 commanders are present (Commander allows at most 2 partners/backgrounds).
4. **Deck size**:
   - Standard Commander decks require 100 cards (including commanders).
   - In standard mode: logs descriptive warnings if deck size differs.
   - In `strict_deck_size=True` mode: raises `DeckIntakeValidationError`.
5. **Singleton constraints**:
   - Warns (or errors if `strict_singleton=True`) if non-basic cards have quantity > 1.
   - Basic lands and exempt cards (*Relentless Rats*, *Dragon's Approach*, *Slime Against Humanity*, etc.) are permitted.

### User Intent Validation
Delegates to `user_intent.validate_intent`:
1. Permitted enums: intent (`new_build`, `tune_up`, `overhaul`), power tiers (`casual`, `focused`, `optimized`, `competitive`), currencies (`USD`, `EUR`, `GBP`, `CAD`, `AUD`), win conditions, and archetypes.
2. Value bounds: power scale 1-10, non-negative budget amounts, target win turn 1-20.
3. Mutual exclusivity: no win conditions or archetypes may be both preferred and excluded.
4. Coherence:
   - `no_budget=True` conflicts with explicit `total_budget` or `max_card_price`.
   - `max_card_price` cannot exceed `total_budget`.
   - Competitive power level targets require budget coherence (minimum $200).

---

## API Endpoints

### `POST /api/intake`
Ingests a deck payload and returns the consolidated intake object.

**Request Body (`DeckIntakePayload`):**
```json
{
  "name": "Atraxa Proliferate",
  "format": "commander",
  "decklist": "Commander\n1 Atraxa, Praetors' Voice\n\nDeck\n1 Sol Ring\n1 Arcane Signet\n",
  "user_intent": {
    "deck_vision": {"intent": "tune_up"},
    "target_power_level": {"scale": 8}
  }
}
```

**Response (200 OK):**
```json
{
  "success": true,
  "intake": {
    "name": "Atraxa Proliferate",
    "format": "commander",
    "commanders": [{"name": "Atraxa, Praetors' Voice", "oracle_id": null, "scryfall_id": null}],
    "cards": [
      {"name": "Sol Ring", "quantity": 1, "category": "Mainboard"},
      {"name": "Arcane Signet", "quantity": 1, "category": "Mainboard"}
    ],
    "user_intent": { ... },
    "summary": {
      "total_cards": 3,
      "commander_count": 1,
      "mainboard_count": 2,
      "is_valid_commander_count": true,
      "is_100_cards": false
    },
    "warnings": ["Commander deck contains 3 cards (standard is 100 cards)."]
  }
}
```

**Error Response (422 Unprocessable Entity):**
```json
{
  "success": false,
  "error": "Deck intake validation failed with 1 error(s): ...",
  "errors": ["In 'commander' format, at least one commander must be designated or tagged in the decklist."],
  "warnings": []
}
```

### `POST /api/intake/validate`
Validates decklist and intent payloads without raising errors, returning `{ "success": true, "validation": { "is_valid": bool, "errors": [...], "warnings": [...] } }`.

---

## Programmatic Usage

```python
from deck_intake import process_deck_intake, validate_deck_intake

# 1. Inspect validation without exceptions:
verdict = validate_deck_intake(decklist=raw_text, intent=intent_dict)
if not verdict.is_valid:
    print(verdict.errors)

# 2. Process intake into consolidated object:
intake = process_deck_intake(
    decklist=raw_text,
    intent=intent_dict,
    name="My Upgraded Commander Deck",
)

# 3. Export to analytics input:
analytics_deck = intake.to_analytics_input()

# 4. Run composite analysis:
from analytics import get_provider
provider = get_provider("commandersalt")
result = intake.analyze(provider=provider)
```

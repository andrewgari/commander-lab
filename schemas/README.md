# User Intent & Playstyle Configuration Schema

This directory defines the structured data model, validation rules, and questionnaire templates for capturing user deck-review intent within Commander Lab.

Downstream analysis agents (e.g., deck intake orchestrators, card recommendation engines, and synergy advisors) consume this configuration to tailor their suggestions to the player's specific vision, power level, budget, and archetype preferences.

---

## Table of Contents
1. [Overview & Files](#1-overview--files)
2. [Field Groups Specification](#2-field-groups-specification)
3. [Validation Rules & Error Handling](#3-validation-rules--error-handling)
4. [Cross-Field Constraints & Coherence Rules](#4-cross-field-constraints--coherence-rules)
5. [Default Fallback Resolution](#5-default-fallback-resolution)
6. [How to Extend the Schema with New Fields](#6-how-to-extend-the-schema-with-new-fields)
7. [Python API Usage](#7-python-api-usage)

---

## 1. Overview & Files

- `user_intent.schema.json`: Formal JSON Schema specification adhering to **JSON Schema Draft 2020-12**.
- `questionnaire_template.md`: User-facing questionnaire questions, LLM system intake prompts, and few-shot extraction examples.
- `examples/minimal_payload.json`: Minimal example payload demonstrating default fallback resolution.
- `examples/full_payload.json`: Fully populated payload exercising every field group and custom properties.
- `../user_intent.py`: Core Python validation and default-resolution module with rich error reporting and cross-field checks.

---

## 2. Field Groups Specification

The schema is organized into five core field groups:

### 1. `deck_vision` (Object)
Captures whether the deck review is for a fresh build from scratch or refining an existing decklist.
- `intent` (*string*, enum: `["new_build", "tune_up", "overhaul"]`, default: `"tune_up"`): Scope of the deck review.
- `description` (*string*, default: `""`): Free-text describing the user's primary vision, pet cards, or specific synergies.
- `target_turn_win` (*integer or null*, range: 1–20, default: `null`): Intended turn window to threaten a win or establish complete board control.

### 2. `target_power_level` (Object)
Captures the desired competitiveness on the community 1–10 scale and named tiers.
- `scale` (*integer*, range: 1–10, default: `7`): Numeric power rating (1–4: Casual/Precon, 5–6: Focused, 7–8: Optimized, 9–10: Competitive/cEDH).
- `tier` (*string*, enum: `["casual", "focused", "optimized", "competitive"]`, default: `"optimized"`): Named tier reflecting pod gameplay expectations.

### 3. `budget_constraints` (Object)
Defines monetary boundaries for recommended card upgrades.
- `no_budget` (*boolean*, default: `false`): Flag indicating no financial limits apply (unlimited budget).
- `total_budget` (*number or null*, minimum: 0, default: `null`): Maximum total spending cap for all recommendations combined.
- `max_card_price` (*number or null*, minimum: 0, default: `null`): Maximum price cap for any single card suggestion.
- `currency` (*string*, enum: `["USD", "EUR", "GBP", "CAD", "AUD"]`, default: `"USD"`): Target currency.

### 4. `preferred_win_conditions` (Object)
Specifies how the player desires to win, plus excluded mechanics.
- `primary` (*string*, enum: `["combat", "combo", "attrition", "commander_damage", "alternate_wincon", "mill", "burn", "lockout"]`, default: `"combat"`): Primary win route.
- `secondary` (*array of strings*, items from enum above, default: `[]`): Backup or complementary win conditions.
- `disliked_or_excluded` (*array of strings*, items from enum above, default: `[]`): Win conditions explicitly prohibited (e.g. no infinite combos).
- `custom_win_conditions` (*array of strings*, free text, default: `[]`): Specific win conditions or combos (e.g. "Approach of the Second Sun", "Maze's End").

### 5. `archetype_preferences` (Object)
Specifies core gameplay archetypes and disallowed styles.
- `primary_archetype` (*string*, enum: `["aggro", "midrange", "control", "combo", "stax", "spellslinger", "aristocrats", "tokens", "graveyard", "voltron", "ramp", "tribal", "group_hug", "group_slug"]`, default: `"midrange"`): Dominant archetype.
- `secondary_archetypes` (*array of strings*, items from enum above, default: `[]`): Secondary archetypes or sub-themes.
- `excluded_archetypes` (*array of strings*, items from enum above, default: `[]`): Playstyles to strictly avoid (e.g. Stax, Group Hug).
- `custom_themes` (*array of strings*, free text, default: `[]`): Free-text themes (e.g. "Dinosaur tribal", "Landfall burn").

### Additional Top-Level Fields
- `freeform_notes` (*string*, default: `""`): Open-ended user comments, local pod rules, or specific card requests.
- `metadata` (*object*, default: `{}`): Extensible key-value pairs for orchestrator tracing, client identifiers, or session tags.

---

## 3. Validation Rules & Error Handling

Validation guarantees that all downstream consumers receive consistent, typed data without needing defensive programming:

1. **Type & Range Enforcement**:
   - `target_power_level.scale` must be an integer between 1 and 10. Out-of-range values raise:
     `"target_power_level.scale must be an integer between 1 and 10, got {value}"`
   - `budget_constraints.total_budget` cannot be negative. Out-of-range values raise:
     `"budget_constraints.total_budget cannot be negative, got {value}"`
   - `budget_constraints.max_card_price` cannot be negative. Out-of-range values raise:
     `"budget_constraints.max_card_price cannot be negative, got {value}"`
   - `deck_vision.target_turn_win` must be an integer between 1 and 20 if provided.

2. **Enum Enforcement**:
   - Fields with constrained values reject unrecognized strings with descriptive error messages detailing allowed options.

3. **Unknown & Free-Text Entries**:
   - Unknown top-level fields are gracefully preserved in `metadata` or allowed through without breaking validation.
   - Freeform entries (`custom_win_conditions`, `custom_themes`, `freeform_notes`) allow arbitrary user text strings.

---

## 4. Cross-Field Constraints & Coherence Rules

The schema and validator enforce critical cross-field logic:

1. **Budget Cap Consistency**:
   - If `budget_constraints.max_card_price` and `budget_constraints.total_budget` are both set, `max_card_price` cannot exceed `total_budget`.
     Violation message: `"budget_constraints.max_card_price ({max_card_price}) cannot exceed total_budget ({total_budget})"`
2. **No-Budget Conflict**:
   - If `budget_constraints.no_budget` is `true`, `total_budget` and `max_card_price` must be `null` (or raise conflict error if explicit numeric limits are passed).
3. **Mutual Exclusivity in Win Conditions**:
   - Any win condition in `preferred_win_conditions.disliked_or_excluded` cannot also be selected as `primary` or included in `secondary`.
     Violation message: `"Win condition '{item}' cannot be both preferred and excluded"`
4. **Mutual Exclusivity in Archetypes**:
   - Any archetype in `archetype_preferences.excluded_archetypes` cannot also be selected as `primary_archetype` or included in `secondary_archetypes`.
     Violation message: `"Archetype '{item}' cannot be both preferred and excluded"`
5. **Power Level vs. Budget Coherence**:
   - If `target_power_level.scale >= 9` or `target_power_level.tier == "competitive"`, having an aggressive total budget cap (e.g. `total_budget < 200` without `no_budget`) triggers a cross-field coherence warning / validation error:
     `"Power level / budget incoherence: competitive tier (scale {scale}) requires competitive staples that cannot be satisfied with a total budget of {currency} {total_budget}"`
6. **Power Level Scale & Tier Coherence**:
   - Numeric `scale` and named `tier` should correspond (casual: 1-4, focused: 5-6, optimized: 7-8, competitive: 9-10). Severe mismatches (e.g. scale 10 with tier casual) are rejected or normalized.

---

## 5. Default Fallback Resolution

When fields or entire field groups are omitted by the user, `resolve_intent_defaults(payload)` fills in documented defaults:
- `deck_vision`: `{"intent": "tune_up", "description": "", "target_turn_win": null}`
- `target_power_level`: `{"scale": 7, "tier": "optimized"}`
- `budget_constraints`: `{"no_budget": false, "total_budget": null, "max_card_price": null, "currency": "USD"}`
- `preferred_win_conditions`: `{"primary": "combat", "secondary": [], "disliked_or_excluded": [], "custom_win_conditions": []}`
- `archetype_preferences`: `{"primary_archetype": "midrange", "secondary_archetypes": [], "excluded_archetypes": [], "custom_themes": []}`
- `freeform_notes`: `""`
- `metadata`: `{}`

---

## 6. How to Extend the Schema with New Fields

To add a new configuration parameter (e.g. `interaction_tolerance` or `bracket_level`):

1. **Update `schemas/user_intent.schema.json`**:
   - Locate the target field group (or add a new property under `properties` if it represents a new category).
   - Specify:
     - `"type"` (e.g. `"string"`, `"integer"`, `"array"`)
     - `"description"` detailing purpose and behavior
     - Allowed values (`"enum"`, `"minimum"`, `"maximum"`, or `"pattern"`)
     - Sensible `"default"` fallback value
   - Maintain Draft 2020-12 compatibility (`$schema`, `type`, `properties`).
2. **Update `user_intent.py`**:
   - Add the field with its default and type annotation to the corresponding dataclass/model.
   - Add range, enum, and cross-field validation rules into `validate_intent()`.
   - Update `resolve_intent_defaults()` if custom defaulting logic is needed.
3. **Update Questionnaire Template (`schemas/questionnaire_template.md`)**:
   - Add a row to the Field-to-Question Mapping Table with user-facing prompt text and options.
   - Add guidance to the intake prompt.
4. **Update Example Payloads & Tests**:
   - Add the new field to `schemas/examples/full_payload.json`.
   - Add unit test cases in `tests/test_user_intent_schema.py` verifying validation, out-of-range rejection, and default resolution.

---

## 7. Python API Usage

```python
from user_intent import parse_user_intent, validate_intent, resolve_intent_defaults

raw_payload = {
    "target_power_level": {"scale": 8},
    "budget_constraints": {"total_budget": 100.0, "max_card_price": 20.0}
}

# 1. Validate payload (returns is_valid, list_of_errors)
is_valid, errors = validate_intent(raw_payload)
if not is_valid:
    raise ValueError(f"Validation failed: {errors}")

# 2. Resolve missing fields to defaults
resolved = resolve_intent_defaults(raw_payload)

# 3. Parse into structured UserDeckIntent object
intent = parse_user_intent(raw_payload)
print(intent.target_power_level.scale)  # 8
print(intent.target_power_level.tier)   # "optimized" (auto-derived)
print(intent.preferred_win_conditions.primary)  # "combat" (default fallback)
```

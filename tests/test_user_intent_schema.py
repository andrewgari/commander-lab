"""
Unit tests for user intent and playstyle configuration schema and validator.
"""

import json
import unittest
from pathlib import Path

from user_intent import (
    ALLOWED_ARCHETYPES,
    ALLOWED_DECK_INTENTS,
    ALLOWED_POWER_TIERS,
    ALLOWED_WIN_CONDITIONS,
    load_schema,
    parse_user_intent,
    resolve_intent_defaults,
    validate_intent,
)

BASE_DIR = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = BASE_DIR / "schemas"
EXAMPLES_DIR = SCHEMAS_DIR / "examples"


class TestUserIntentSchema(unittest.TestCase):
    def setUp(self):
        self.schema = load_schema()
        self.minimal_payload_path = EXAMPLES_DIR / "minimal_payload.json"
        self.full_payload_path = EXAMPLES_DIR / "full_payload.json"

    def test_schema_file_exists_and_is_draft_2020_12(self):
        """Schema file exists and declares JSON Schema draft 2020-12."""
        self.assertTrue((SCHEMAS_DIR / "user_intent.schema.json").exists())
        self.assertEqual(
            self.schema.get("$schema"),
            "https://json-schema.org/draft/2020-12/schema",
        )
        self.assertEqual(self.schema.get("type"), "object")
        self.assertEqual(self.schema.get("title"), "UserDeckIntent")

    def test_all_five_required_field_groups_present(self):
        """All five required field groups are present in schema properties."""
        properties = self.schema.get("properties", {})
        expected_groups = [
            "deck_vision",
            "target_power_level",
            "budget_constraints",
            "preferred_win_conditions",
            "archetype_preferences",
        ]
        for group in expected_groups:
            self.assertIn(group, properties, f"Field group '{group}' missing from schema properties")
            group_spec = properties[group]
            self.assertEqual(group_spec.get("type"), "object")
            self.assertIn("description", group_spec)
            self.assertIn("default", group_spec)

    def test_schema_field_specifications_and_defaults(self):
        """Every field within groups has explicit type, description, and documented default."""
        properties = self.schema["properties"]

        # deck_vision
        dv_props = properties["deck_vision"]["properties"]
        self.assertEqual(dv_props["intent"]["type"], "string")
        self.assertEqual(set(dv_props["intent"]["enum"]), ALLOWED_DECK_INTENTS)
        self.assertEqual(dv_props["intent"]["default"], "tune_up")
        self.assertTrue(len(dv_props["intent"]["description"]) > 0)

        # target_power_level
        pl_props = properties["target_power_level"]["properties"]
        self.assertEqual(pl_props["scale"]["type"], "integer")
        self.assertEqual(pl_props["scale"]["minimum"], 1)
        self.assertEqual(pl_props["scale"]["maximum"], 10)
        self.assertEqual(pl_props["scale"]["default"], 7)
        self.assertEqual(set(pl_props["tier"]["enum"]), ALLOWED_POWER_TIERS)
        self.assertEqual(pl_props["tier"]["default"], "optimized")

        # budget_constraints
        bc_props = properties["budget_constraints"]["properties"]
        self.assertEqual(bc_props["no_budget"]["type"], "boolean")
        self.assertEqual(bc_props["no_budget"]["default"], False)
        self.assertIn("total_budget", bc_props)
        self.assertIn("max_card_price", bc_props)

        # preferred_win_conditions
        wc_props = properties["preferred_win_conditions"]["properties"]
        self.assertEqual(wc_props["primary"]["default"], "combat")
        self.assertEqual(set(wc_props["primary"]["enum"]), ALLOWED_WIN_CONDITIONS)

        # archetype_preferences
        ap_props = properties["archetype_preferences"]["properties"]
        self.assertEqual(ap_props["primary_archetype"]["default"], "midrange")
        self.assertEqual(set(ap_props["primary_archetype"]["enum"]), ALLOWED_ARCHETYPES)

    def test_minimal_payload_example(self):
        """Minimal payload example file exists, validates, and parses correctly."""
        self.assertTrue(self.minimal_payload_path.exists())
        with open(self.minimal_payload_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        is_valid, errors = validate_intent(data)
        self.assertTrue(is_valid, f"Minimal payload validation failed: {errors}")

        intent = parse_user_intent(data)
        self.assertEqual(intent.deck_vision.intent, "tune_up")
        self.assertEqual(intent.target_power_level.scale, 7)
        self.assertEqual(intent.target_power_level.tier, "optimized")
        self.assertEqual(intent.budget_constraints.no_budget, False)
        self.assertIsNone(intent.budget_constraints.total_budget)
        self.assertEqual(intent.preferred_win_conditions.primary, "combat")
        self.assertEqual(intent.archetype_preferences.primary_archetype, "midrange")

    def test_full_payload_example(self):
        """Full payload example file exists, validates, and parses correctly."""
        self.assertTrue(self.full_payload_path.exists())
        with open(self.full_payload_path, "r", encoding="utf-8") as f:
            data = json.load(f)

        is_valid, errors = validate_intent(data)
        self.assertTrue(is_valid, f"Full payload validation failed: {errors}")

        intent = parse_user_intent(data)
        self.assertEqual(intent.deck_vision.intent, "tune_up")
        self.assertEqual(intent.target_power_level.scale, 8)
        self.assertEqual(intent.target_power_level.tier, "optimized")
        self.assertEqual(intent.budget_constraints.total_budget, 150.0)
        self.assertEqual(intent.budget_constraints.max_card_price, 35.0)
        self.assertEqual(intent.preferred_win_conditions.primary, "attrition")
        self.assertIn("burn", intent.preferred_win_conditions.secondary)
        self.assertIn("Marionette Master treasure drain", intent.preferred_win_conditions.custom_win_conditions)
        self.assertEqual(intent.archetype_preferences.primary_archetype, "spellslinger")
        self.assertIn("aristocrats", intent.archetype_preferences.secondary_archetypes)
        self.assertIn("stax", intent.archetype_preferences.excluded_archetypes)
        self.assertEqual(intent.metadata.get("requested_by"), "andrew")

    def test_empty_payload_resolves_all_defaults(self):
        """Passing an empty payload {} resolves every field to its documented default."""
        resolved = resolve_intent_defaults({})
        self.assertEqual(resolved["deck_vision"]["intent"], "tune_up")
        self.assertEqual(resolved["deck_vision"]["description"], "")
        self.assertIsNone(resolved["deck_vision"]["target_turn_win"])
        self.assertEqual(resolved["target_power_level"]["scale"], 7)
        self.assertEqual(resolved["target_power_level"]["tier"], "optimized")
        self.assertEqual(resolved["budget_constraints"]["no_budget"], False)
        self.assertIsNone(resolved["budget_constraints"]["total_budget"])
        self.assertIsNone(resolved["budget_constraints"]["max_card_price"])
        self.assertEqual(resolved["budget_constraints"]["currency"], "USD")
        self.assertEqual(resolved["preferred_win_conditions"]["primary"], "combat")
        self.assertEqual(resolved["preferred_win_conditions"]["secondary"], [])
        self.assertEqual(resolved["preferred_win_conditions"]["disliked_or_excluded"], [])
        self.assertEqual(resolved["archetype_preferences"]["primary_archetype"], "midrange")
        self.assertEqual(resolved["archetype_preferences"]["secondary_archetypes"], [])
        self.assertEqual(resolved["archetype_preferences"]["excluded_archetypes"], [])
        self.assertEqual(resolved["freeform_notes"], "")
        self.assertEqual(resolved["metadata"], {})

    def test_power_level_range_validation(self):
        """Out of range power level scales (e.g. 0 or 11) are rejected with clear messages."""
        # Scale too low
        is_valid, errors = validate_intent({"target_power_level": {"scale": 0}})
        self.assertFalse(is_valid)
        self.assertTrue(any("scale must be an integer between 1 and 10" in e for e in errors))

        # Scale too high
        is_valid, errors = validate_intent({"target_power_level": {"scale": 11}})
        self.assertFalse(is_valid)
        self.assertTrue(any("scale must be an integer between 1 and 10" in e for e in errors))

        # Non-integer scale
        is_valid, errors = validate_intent({"target_power_level": {"scale": "seven"}})
        self.assertFalse(is_valid)
        self.assertTrue(any("must be an integer" in e for e in errors))

    def test_budget_negative_range_validation(self):
        """Negative budget caps are rejected with descriptive messages."""
        is_valid, errors = validate_intent({"budget_constraints": {"total_budget": -10}})
        self.assertFalse(is_valid)
        self.assertTrue(any("total_budget cannot be negative" in e for e in errors))

        is_valid, errors = validate_intent({"budget_constraints": {"max_card_price": -5}})
        self.assertFalse(is_valid)
        self.assertTrue(any("max_card_price cannot be negative" in e for e in errors))

    def test_cross_field_budget_max_card_price_exceeds_total(self):
        """max_card_price cannot exceed total_budget."""
        payload = {
            "budget_constraints": {
                "total_budget": 50.0,
                "max_card_price": 75.0,
            }
        }
        is_valid, errors = validate_intent(payload)
        self.assertFalse(is_valid)
        self.assertTrue(
            any("max_card_price (75.0) cannot exceed total_budget (50.0)" in e for e in errors)
        )

    def test_mutual_exclusivity_no_budget_with_numeric_caps(self):
        """no_budget=True conflicts with explicit total_budget or max_card_price."""
        payload = {
            "budget_constraints": {
                "no_budget": True,
                "total_budget": 100.0,
            }
        }
        is_valid, errors = validate_intent(payload)
        self.assertFalse(is_valid)
        self.assertTrue(any("no_budget is true, but total_budget or max_card_price was specified" in e for e in errors))

    def test_mutual_exclusivity_win_conditions(self):
        """A win condition cannot be both preferred and excluded."""
        payload = {
            "preferred_win_conditions": {
                "primary": "combo",
                "disliked_or_excluded": ["combo", "mill"],
            }
        }
        is_valid, errors = validate_intent(payload)
        self.assertFalse(is_valid)
        self.assertTrue(any("Win condition conflict: 'combo' cannot be both preferred and excluded" in e for e in errors))

        payload_sec = {
            "preferred_win_conditions": {
                "primary": "combat",
                "secondary": ["attrition", "burn"],
                "disliked_or_excluded": ["burn"],
            }
        }
        is_valid, errors = validate_intent(payload_sec)
        self.assertFalse(is_valid)
        self.assertTrue(any("Win condition conflict: 'burn' cannot be both preferred and excluded" in e for e in errors))

    def test_mutual_exclusivity_archetypes(self):
        """An archetype cannot be both preferred and excluded."""
        payload = {
            "archetype_preferences": {
                "primary_archetype": "stax",
                "excluded_archetypes": ["stax"],
            }
        }
        is_valid, errors = validate_intent(payload)
        self.assertFalse(is_valid)
        self.assertTrue(any("Archetype conflict: 'stax' cannot be both preferred and excluded" in e for e in errors))

    def test_cross_field_power_level_budget_coherence(self):
        """Target power level 9 or 10 with a tiny budget raises a coherence error."""
        payload = {
            "target_power_level": {
                "scale": 10,
                "tier": "competitive",
            },
            "budget_constraints": {
                "total_budget": 50.0,
                "currency": "USD",
            },
        }
        is_valid, errors = validate_intent(payload, strict_coherence=True)
        self.assertFalse(is_valid)
        self.assertTrue(any("Power level / budget incoherence" in e for e in errors))

    def test_cross_field_scale_tier_mismatch(self):
        """Severe mismatch between numeric scale and named tier is flagged."""
        payload = {
            "target_power_level": {
                "scale": 10,
                "tier": "casual",
            }
        }
        is_valid, errors = validate_intent(payload)
        self.assertFalse(is_valid)
        self.assertTrue(any("conflicts with tier 'casual'" in e for e in errors))

    def test_invalid_enums_rejected(self):
        """Invalid enum choices are rejected with descriptive messages."""
        payload = {
            "deck_vision": {"intent": "destroy_everything"},
            "target_power_level": {"tier": "godlike"},
            "preferred_win_conditions": {"primary": "table_flip"},
            "archetype_preferences": {"primary_archetype": "chaos_inc"},
        }
        is_valid, errors = validate_intent(payload)
        self.assertFalse(is_valid)
        self.assertEqual(len(errors), 4)

    def test_graceful_handling_of_unknown_and_freetext(self):
        """Free-text fields and unknown top-level properties are preserved gracefully."""
        payload = {
            "deck_vision": {
                "intent": "new_build",
                "description": "Custom weird build with obscure old cards",
            },
            "preferred_win_conditions": {
                "custom_win_conditions": ["Battle of Wits with 250 cards", "Barren Glory"],
            },
            "archetype_preferences": {
                "custom_themes": ["Oops all chairs art theme", "Ladies looking left"],
            },
            "freeform_notes": "Playgroup allows silver-bordered cards if reasonable.",
            "unrecognized_custom_field": "preserved_safely",
        }
        is_valid, errors = validate_intent(payload)
        self.assertTrue(is_valid, f"Validation failed: {errors}")

        resolved = resolve_intent_defaults(payload)
        self.assertIn("preserved_safely", resolved["metadata"]["unrecognized_custom_field"])

        intent = parse_user_intent(payload)
        self.assertEqual(len(intent.preferred_win_conditions.custom_win_conditions), 2)
        self.assertEqual(len(intent.archetype_preferences.custom_themes), 2)
        self.assertEqual(intent.metadata["unrecognized_custom_field"], "preserved_safely")


if __name__ == "__main__":
    unittest.main()

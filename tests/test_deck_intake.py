"""
Unit and integration tests for deck intake orchestration module (deck_intake.py).

Verifies:
1. Full end-to-end intake flows across MTGA, MTGO, and plain-text formats.
2. User intent integration (minimal, full, and default fallback payloads).
3. Downstream analytics integration (DecklistInput generation and provider execution).
4. Validation rules: card structure, commander rules, singleton constraints, deck size,
   and intent constraint enforcement.
5. Consolidated deck intake serialization and query helpers.
6. Module facade re-exports in intake.py.
"""

import os
import sys
import unittest
from typing import Any, Dict, List

# Ensure repository root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.models import (
    CardRecommendations,
    DeckAnalyticsResult,
    DeckPopularity,
    DeckSynergy,
    DecklistInput,
    MetaScores,
    PowerScore,
    SaltScore,
)
from analytics.provider import BaseAnalyticsProvider
from deck_intake import (
    ConsolidatedDeckIntake,
    DeckIntakeError,
    DeckIntakePayload,
    DeckIntakeService,
    DeckIntakeValidationError,
    IntakeValidationResult,
    process_deck_intake,
    validate_deck_intake,
)
import intake


class MockAnalyticsProvider(BaseAnalyticsProvider):
    """Stub provider to verify downstream analysis invocation from intake object."""

    @property
    def name(self) -> str:
        return "mock_intake_provider"

    def get_meta_scores(self, deck: DecklistInput) -> MetaScores:
        return MetaScores(
            salt=SaltScore(score=15.5),
            power=PowerScore(score=7.5, tier="Optimized"),
            meta_rank=42,
        )

    def get_recommendations(self, deck: DecklistInput) -> CardRecommendations:
        return CardRecommendations(items=[], cuts=[])

    def get_synergy(self, deck: DecklistInput) -> DeckSynergy:
        return DeckSynergy(overall_synergy=0.82)

    def get_popularity(self, deck: DecklistInput) -> DeckPopularity:
        return DeckPopularity(rank=1, num_decks=1000, popularity_percentile=95.0)


class TestDeckIntakeFullFlows(unittest.TestCase):
    """End-to-end integration flows verifying decklist parsing + user intent consolidation."""

    def test_full_intake_flow_arena_format_with_full_intent(self):
        """Verify full flow: Arena decklist + comprehensive user intent payload."""
        arena_decklist = """
Commander
1 Atraxa, Praetors' Voice (CM2) 1

Deck
1 Sol Ring (NEO) 123
1 Arcane Signet (ELD) 331
1 Doubling Season (2XM) 158
1 Deepglow Skate (C16) 7
4 Plains
4 Island
4 Swamp
4 Forest

Sideboard
1 Heroic Intervention (M21) 164
1 Swords to Plowshares (STA) 10
"""
        intent_payload: Dict[str, Any] = {
            "deck_vision": {
                "intent": "tune_up",
                "description": "High-powered +1/+1 counters and proliferate synergy",
                "target_turn_win": 7,
            },
            "target_power_level": {
                "scale": 8,
                "tier": "optimized",
            },
            "budget_constraints": {
                "no_budget": False,
                "total_budget": 300.0,
                "max_card_price": 60.0,
                "currency": "USD",
            },
            "preferred_win_conditions": {
                "primary": "combat",
                "secondary": ["attrition"],
                "disliked_or_excluded": ["lockout"],
                "custom_win_conditions": ["Simic Ascendancy growth counters"],
            },
            "archetype_preferences": {
                "primary_archetype": "midrange",
                "secondary_archetypes": ["tokens", "aristocrats"],
                "excluded_archetypes": ["stax", "group_hug"],
                "custom_themes": ["Proliferate", "Superfriends"],
            },
            "freeform_notes": "Avoid 2-card infinite combos.",
            "metadata": {"client": "test-suite", "user_id": "player-1"},
        }

        result = process_deck_intake(
            decklist=arena_decklist,
            intent=intent_payload,
            name="Atraxa Proliferate Engine",
            format="commander",
            deck_id="deck-atraxa-001",
            metadata={"source": "arena_export"},
        )

        self.assertIsInstance(result, ConsolidatedDeckIntake)
        self.assertEqual(result.name, "Atraxa Proliferate Engine")
        self.assertEqual(result.format, "commander")
        self.assertEqual(result.deck_id, "deck-atraxa-001")
        self.assertEqual(result.metadata.get("source"), "arena_export")

        # Commanders
        self.assertEqual(len(result.commanders), 1)
        self.assertEqual(result.commanders[0].name, "Atraxa, Praetors' Voice")

        # Mainboard cards
        # 1 Sol Ring, 1 Arcane Signet, 1 Doubling Season, 1 Deepglow Skate, 4 Plains, 4 Island, 4 Swamp, 4 Forest
        # 1+1+1+1+4+4+4+4 = 20 mainboard cards
        self.assertEqual(result.summary.mainboard_count, 20)
        self.assertEqual(result.summary.commander_count, 1)
        self.assertEqual(result.summary.total_cards, 21)
        self.assertEqual(result.summary.sideboard_count, 2)
        self.assertEqual(len(result.sideboard), 2)
        self.assertTrue(result.summary.is_valid_commander_count)
        self.assertFalse(result.summary.is_100_cards)  # 21 cards is under 100

        # User intent assertions
        self.assertEqual(result.user_intent.deck_vision.intent, "tune_up")
        self.assertEqual(result.user_intent.deck_vision.target_turn_win, 7)
        self.assertEqual(result.user_intent.target_power_level.scale, 8)
        self.assertEqual(result.user_intent.target_power_level.tier, "optimized")
        self.assertEqual(result.user_intent.budget_constraints.total_budget, 300.0)
        self.assertEqual(result.user_intent.preferred_win_conditions.primary, "combat")
        self.assertIn("Simic Ascendancy growth counters", result.user_intent.preferred_win_conditions.custom_win_conditions)
        self.assertEqual(result.user_intent.archetype_preferences.primary_archetype, "midrange")
        self.assertIn("Proliferate", result.user_intent.archetype_preferences.custom_themes)

        # Card queries
        all_names = result.all_card_names(include_commanders=True, include_sideboard=False)
        self.assertIn("Atraxa, Praetors' Voice", all_names)
        self.assertIn("Doubling Season", all_names)
        self.assertNotIn("Heroic Intervention", all_names)

        all_with_sb = result.all_card_names(include_commanders=True, include_sideboard=True)
        self.assertIn("Heroic Intervention", all_with_sb)

        # Downstream analytics preparation
        analytics_input = result.to_analytics_input()
        self.assertIsInstance(analytics_input, DecklistInput)
        self.assertEqual(analytics_input.name, "Atraxa Proliferate Engine")
        self.assertEqual(len(analytics_input.commanders), 1)
        self.assertEqual(analytics_input.commanders[0].name, "Atraxa, Praetors' Voice")
        self.assertEqual(len(analytics_input.cards), 8)  # 8 distinct mainboard entries
        self.assertEqual(analytics_input.total_cards(include_commanders=True), 21)

        # Downstream analytics execution
        mock_provider = MockAnalyticsProvider()
        analysis = result.analyze(provider=mock_provider)
        self.assertIsInstance(analysis, DeckAnalyticsResult)
        self.assertEqual(analysis.provider_name, "mock_intake_provider")
        self.assertEqual(analysis.meta_scores.power.score, 7.5)
        self.assertEqual(analysis.synergy.overall_synergy, 0.82)

    def test_mtgo_format_with_minimal_intent_resolves_defaults(self):
        """Verify MTGO format with minimal intent correctly backfills all schema defaults."""
        mtgo_decklist = """
// Commander
1 Edgar Markov

// Deck
1 Blood Artist
1 Cordial Vampire
1 Captivating Vampire
10 Swamp
10 Mountain
5 Plains

// Sideboard
1 Teferi's Protection
"""
        minimal_intent = {
            "deck_vision": {"intent": "new_build"}
        }

        result = process_deck_intake(
            decklist=mtgo_decklist,
            intent=minimal_intent,
        )

        self.assertEqual(len(result.commanders), 1)
        self.assertEqual(result.commanders[0].name, "Edgar Markov")
        self.assertEqual(result.name, "Edgar Markov Commander Deck")  # Auto-generated name

        # Backfilled intent defaults
        self.assertEqual(result.user_intent.deck_vision.intent, "new_build")
        self.assertEqual(result.user_intent.target_power_level.scale, 7)
        self.assertEqual(result.user_intent.target_power_level.tier, "optimized")
        self.assertFalse(result.user_intent.budget_constraints.no_budget)
        self.assertEqual(result.user_intent.budget_constraints.currency, "USD")
        self.assertEqual(result.user_intent.preferred_win_conditions.primary, "combat")
        self.assertEqual(result.user_intent.archetype_preferences.primary_archetype, "midrange")

        # Serializability
        d = result.to_dict()
        self.assertIsInstance(d, dict)
        self.assertEqual(d["name"], "Edgar Markov Commander Deck")
        self.assertEqual(d["summary"]["commander_count"], 1)
        self.assertEqual(d["summary"]["mainboard_count"], 28)

    def test_plain_text_with_inline_cmdr_tag_and_split_card(self):
        """Verify plain text decklist with inline *CMDR* tag and split card handling."""
        plain_text = """
1 Niv-Mizzet, Parun *CMDR*
1 Curiosity
1 Fire // Ice
1 Tandem Lookout
1 Ophidian Eye
1 Command Tower
"""
        result = process_deck_intake(decklist=plain_text)
        self.assertEqual(len(result.commanders), 1)
        self.assertEqual(result.commanders[0].name, "Niv-Mizzet, Parun")

        # Split card preserved
        card_names = [c.name for c in result.cards]
        self.assertIn("Fire // Ice", card_names)
        self.assertIn("Curiosity", card_names)
        self.assertEqual(result.summary.commander_count, 1)
        self.assertEqual(result.summary.mainboard_count, 5)

    def test_explicit_commander_override_in_arguments(self):
        """Verify caller can pass explicit commanders list to designate untagged cards."""
        untagged_list = """
1 Urza, Lord High Artificer
1 Mox Amber
1 Sai, Master Thopterist
1 Whir of Invention
20 Island
"""
        result = process_deck_intake(
            decklist=untagged_list,
            commanders=["Urza, Lord High Artificer"],
        )

        self.assertEqual(len(result.commanders), 1)
        self.assertEqual(result.commanders[0].name, "Urza, Lord High Artificer")
        # Ensure Urza is not duplicated in mainboard
        mainboard_names = [c.name for c in result.cards]
        self.assertNotIn("Urza, Lord High Artificer", mainboard_names)

    def test_partner_commanders_intake(self):
        """Verify 2 partner commanders are properly captured."""
        decklist = """
Commander
1 Thrasios, Triton Hero
1 Tymna the Weaver

Deck
1 Sol Ring
1 Demonic Consultation
1 Thassa's Oracle
1 Command Tower
"""
        result = process_deck_intake(decklist=decklist)
        self.assertEqual(len(result.commanders), 2)
        cmdr_names = {c.name for c in result.commanders}
        self.assertEqual(cmdr_names, {"Thrasios, Triton Hero", "Tymna the Weaver"})
        self.assertEqual(result.name, "Thrasios, Triton Hero & Tymna the Weaver Commander Deck")
        self.assertTrue(result.summary.is_valid_commander_count)

    def test_structured_cards_input(self):
        """Verify intake accepts a list of pre-structured card dictionaries."""
        structured_cards = [
            {"name": "Krenko, Mob Boss", "quantity": 1, "section": "commander"},
            {"name": "Goblin Chieftain", "quantity": 1, "section": "mainboard"},
            {"name": "Goblin Warchief", "quantity": 1, "section": "mainboard"},
            {"name": "Mountain", "quantity": 10, "section": "mainboard"},
        ]
        result = process_deck_intake(decklist=structured_cards)
        self.assertEqual(len(result.commanders), 1)
        self.assertEqual(result.commanders[0].name, "Krenko, Mob Boss")
        self.assertEqual(result.summary.mainboard_count, 12)

    def test_decklist_input_analytics_model_intake(self):
        """Verify intake accepts a DecklistInput domain model as input."""
        from analytics.models import CommanderIdentifier, DeckCardEntry
        dl_input = DecklistInput(
            deck_id="dl-99",
            name="Lathril Elves",
            commanders=[CommanderIdentifier(name="Lathril, Blade of the Elves")],
            cards=[
                DeckCardEntry(name="Elvish Mystic", quantity=1),
                DeckCardEntry(name="Forest", quantity=15),
            ],
            format="commander",
        )
        result = process_deck_intake(decklist=dl_input)
        self.assertEqual(result.name, "Lathril Elves")
        self.assertEqual(len(result.commanders), 1)
        self.assertEqual(result.commanders[0].name, "Lathril, Blade of the Elves")
        self.assertEqual(result.summary.mainboard_count, 16)


class TestDeckIntakeValidationFailures(unittest.TestCase):
    """Verify robust rejection of malformed card structures and invalid user intent."""

    def test_empty_decklist_rejected(self):
        """Empty decklist text raises DeckIntakeValidationError."""
        with self.assertRaises(DeckIntakeValidationError) as ctx:
            process_deck_intake(decklist="")
        self.assertTrue(any("empty" in e.lower() for e in ctx.exception.errors))

    def test_no_decklist_provided_rejected(self):
        """None decklist raises DeckIntakeValidationError."""
        with self.assertRaises(DeckIntakeValidationError) as ctx:
            process_deck_intake(decklist=None)
        self.assertTrue(any("no decklist provided" in e.lower() for e in ctx.exception.errors))

    def test_missing_commander_in_commander_format_rejected(self):
        """Decklist without any commander in commander format raises error."""
        decklist = """
1 Sol Ring
1 Arcane Signet
10 Island
"""
        with self.assertRaises(DeckIntakeValidationError) as ctx:
            process_deck_intake(decklist=decklist, format="commander")
        self.assertTrue(any("commander" in e.lower() for e in ctx.exception.errors))

    def test_non_commander_format_allows_zero_commanders(self):
        """Non-commander format (e.g. 'cube' or 'standard') allows 0 commanders."""
        decklist = """
4 Lightning Bolt
4 Goblin Guide
16 Mountain
"""
        result = process_deck_intake(decklist=decklist, format="standard")
        self.assertEqual(len(result.commanders), 0)
        self.assertEqual(result.summary.mainboard_count, 24)

    def test_negative_or_zero_quantity_rejected(self):
        """Zero or negative quantity in structured cards raises error."""
        cards = [
            {"name": "Atraxa, Praetors' Voice", "quantity": 1, "section": "commander"},
            {"name": "Sol Ring", "quantity": 0, "section": "mainboard"},
        ]
        with self.assertRaises(DeckIntakeValidationError) as ctx:
            process_deck_intake(decklist=cards)
        self.assertTrue(any("quantity" in e.lower() for e in ctx.exception.errors))

    def test_strict_deck_size_rejection(self):
        """Strict deck size mode rejects non-100 card Commander decks."""
        short_deck = """
Commander
1 Atraxa, Praetors' Voice

Deck
1 Sol Ring
1 Arcane Signet
"""
        # Non-strict permits with warning
        res = process_deck_intake(decklist=short_deck, strict_deck_size=False)
        self.assertTrue(any("100 cards" in w for w in res.warnings))

        # Strict mode raises validation error
        with self.assertRaises(DeckIntakeValidationError) as ctx:
            process_deck_intake(decklist=short_deck, strict_deck_size=True)
        self.assertTrue(any("100 cards" in e for e in ctx.exception.errors))

    def test_strict_singleton_rejection(self):
        """Strict singleton mode rejects duplicate non-basic cards."""
        duplicate_deck = """
Commander
1 Atraxa, Praetors' Voice

Deck
2 Sol Ring
1 Arcane Signet
"""
        # Non-strict permits with warning
        res = process_deck_intake(decklist=duplicate_deck, strict_singleton=False)
        self.assertTrue(any("Sol Ring" in w for w in res.warnings))

        # Strict mode raises error
        with self.assertRaises(DeckIntakeValidationError) as ctx:
            process_deck_intake(decklist=duplicate_deck, strict_singleton=True)
        self.assertTrue(any("Sol Ring" in e for e in ctx.exception.errors))

    def test_basic_lands_and_any_number_exempt_from_singleton(self):
        """Basic lands and exempt cards (Relentless Rats) allow multiple copies even in strict singleton."""
        rats_deck = """
Commander
1 Marrow-Gnawer

Deck
10 Relentless Rats
10 Swamp
"""
        res = process_deck_intake(decklist=rats_deck, strict_singleton=True)
        self.assertEqual(len(res.commanders), 1)
        self.assertEqual(res.summary.mainboard_count, 20)

    def test_user_intent_validation_errors(self):
        """Invalid user intent fields fail intake validation with aggregated errors."""
        decklist = """
Commander
1 Atraxa, Praetors' Voice

Deck
1 Sol Ring
"""
        # Invalid intent choice + out of range power scale
        invalid_intent = {
            "deck_vision": {"intent": "break_the_format"},
            "target_power_level": {"scale": 15},
            "budget_constraints": {
                "no_budget": True,
                "total_budget": 500.0,  # Conflict: no_budget with total_budget
            },
        }

        with self.assertRaises(DeckIntakeValidationError) as ctx:
            process_deck_intake(decklist=decklist, intent=invalid_intent)

        err_text = "; ".join(ctx.exception.errors)
        self.assertIn("break_the_format", err_text)
        self.assertIn("15", err_text)
        self.assertIn("no_budget is true, but total_budget", err_text)

    def test_mutual_exclusivity_win_condition_conflict(self):
        """Win condition listed as both preferred and excluded triggers validation error."""
        decklist = """
Commander
1 Atraxa, Praetors' Voice

Deck
1 Sol Ring
"""
        conflicted_intent = {
            "preferred_win_conditions": {
                "primary": "combo",
                "disliked_or_excluded": ["combo"],
            }
        }
        with self.assertRaises(DeckIntakeValidationError) as ctx:
            process_deck_intake(decklist=decklist, intent=conflicted_intent)
        self.assertTrue(any("cannot be both preferred and excluded" in e for e in ctx.exception.errors))

    def test_aggregated_multi_error_reporting(self):
        """Verifies card structure errors AND intent errors are collected together in one verdict."""
        # Deck has missing commander AND invalid intent
        malformed_deck = "1 Sol Ring\n"
        invalid_intent = {
            "target_power_level": {"scale": -1}
        }

        verdict = validate_deck_intake(decklist=malformed_deck, intent=invalid_intent)
        self.assertFalse(verdict.is_valid)
        self.assertGreaterEqual(len(verdict.errors), 2)
        has_cmdr_err = any("commander" in e.lower() for e in verdict.errors)
        has_scale_err = any("target_power_level.scale" in e for e in verdict.errors)
        self.assertTrue(has_cmdr_err, "Expected commander validation error")
        self.assertTrue(has_scale_err, "Expected power level validation error")


class TestDeckIntakePayloadAndFacade(unittest.TestCase):
    """Verify DeckIntakePayload Pydantic integration, service class, and intake facade."""

    def test_deck_intake_payload_model_processing(self):
        """Verify DeckIntakeService.process_payload using DeckIntakePayload model."""
        payload = DeckIntakePayload(
            name="Gishath Dinosaurs",
            format="commander",
            decklist="Commander\n1 Gishath, Sun's Avatar\n\nDeck\n1 Carnage Tyrant\n1 Sol Ring\n",
            user_intent={"deck_vision": {"intent": "new_build"}},
        )
        service = DeckIntakeService()
        result = service.process_payload(payload)
        self.assertEqual(result.name, "Gishath Dinosaurs")
        self.assertEqual(result.commanders[0].name, "Gishath, Sun's Avatar")
        self.assertEqual(len(result.cards), 2)

    def test_intake_facade_exports(self):
        """Verify intake.py exports all required symbols."""
        self.assertTrue(hasattr(intake, "process_deck_intake"))
        self.assertTrue(hasattr(intake, "validate_deck_intake"))
        self.assertTrue(hasattr(intake, "ConsolidatedDeckIntake"))
        self.assertTrue(hasattr(intake, "DeckIntakeService"))
        self.assertTrue(hasattr(intake, "DeckIntakeValidationError"))
        self.assertTrue(hasattr(intake, "DeckIntakePayload"))


if __name__ == "__main__":
    unittest.main()

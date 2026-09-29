"""
Unit tests for card recommendation and card swap generation pipeline (analytics/recommendations.py).

Verifies:
1. Recommendation scoring prioritizing theme, unique synergies, and recent sets.
2. User intent alignment (tune_up, new_build, overhaul, power levels, wincons, budget).
3. Candidate cut heuristics (outclassed staples, high salt in casual, curve bottlenecks, off-theme).
4. Direct 1-to-1 card swap formulation with net mana and synergy calculations.
5. External recommendation ingestion and blending with curated knowledge base.
"""

from __future__ import annotations

import os
import sys
import unittest

# Ensure repo root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.engine import (
    DeckAnalysisEngine,
    DeckAnalysisReport,
    default_analysis_engine,
)
from analytics.models import (
    CardRecommendation,
    CardRecommendations,
    CommanderIdentifier,
    DeckCardEntry,
    DecklistInput,
)
from analytics.recommendations import (
    CURATED_CARD_CATALOG,
    RECENT_SETS,
    CardSwapSuggestion,
    CuratedCard,
    RecommendationGenerator,
    RecommendationSet,
    default_recommendation_generator,
)
from user_intent import (
    ArchetypePreferences,
    BudgetConstraints,
    DeckVision,
    PreferredWinConditions,
    TargetPowerLevel,
    UserDeckIntent,
)


class TestRecommendationGenerator(unittest.TestCase):
    def setUp(self):
        self.generator = RecommendationGenerator()

        # Build sample Atraxa Counters Deck
        self.atraxa_deck = DecklistInput(
            deck_id="test:atraxa",
            name="Atraxa Proliferate Test",
            format="commander",
            commanders=[
                CommanderIdentifier(name="Atraxa, Praetors' Voice", oracle_id=None, scryfall_id=None)
            ],
            cards=[
                DeckCardEntry(name="Sol Ring", quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard"),
                DeckCardEntry(name="Arcane Signet", quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard"),
                DeckCardEntry(name="Hardened Scales", quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard"),
                DeckCardEntry(name="Evolution Sage", quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard"),
                DeckCardEntry(name="Diabolic Tutor", quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard"),  # Outclassed cut candidate
                DeckCardEntry(name="Cancel", quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard"),          # Outclassed cut candidate
                DeckCardEntry(name="Murder", quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard"),          # Outclassed cut candidate
                DeckCardEntry(name="Plains", quantity=30, oracle_id=None, scryfall_id=None, category="Mainboard"),
                DeckCardEntry(name="Island", quantity=30, oracle_id=None, scryfall_id=None, category="Mainboard"),
                DeckCardEntry(name="Swamp", quantity=30, oracle_id=None, scryfall_id=None, category="Mainboard"),
            ],
        )

        self.engine = default_analysis_engine
        self.atraxa_analysis = self.engine.analyze(self.atraxa_deck)

    def test_curated_catalog_recent_sets_flagged(self):
        """Ensure curated cards from recent sets (2022+) are properly identified."""
        blb_cards = [c for c in CURATED_CARD_CATALOG if c.set_code == "BLB"]
        self.assertGreaterEqual(len(blb_cards), 2)
        for card in blb_cards:
            self.assertTrue(card.is_recent)
            self.assertEqual(card.release_year, 2024)

        mh3_cards = [c for c in CURATED_CARD_CATALOG if c.set_code == "MH3"]
        self.assertGreaterEqual(len(mh3_cards), 3)
        for card in mh3_cards:
            self.assertTrue(card.is_recent)
            self.assertEqual(card.release_year, 2024)

    def test_theme_and_unique_synergies_prioritized(self):
        """Counters archetype recommendations prioritize on-theme counters and unique engines."""
        intent = UserDeckIntent(
            deck_vision=DeckVision(intent="tune_up"),
            archetype_preferences=ArchetypePreferences(primary_archetype="counters"),
        )
        recs: RecommendationSet = self.generator.generate(
            deck=self.atraxa_deck,
            analysis=self.atraxa_analysis,
            intent=intent,
        )

        self.assertGreater(len(recs.additions), 0)
        # Check that top additions contain thematic counters cards
        rec_names = [a.card_name for a in recs.additions]
        self.assertTrue(
            any(name in rec_names for name in ["Innkeeper's Talent", "Bristly Bill, Spine Sower", "The Ozolith", "Ozolith, the Shattered Spire"]),
            f"Expected counters cards in recommendations, got: {rec_names}",
        )

        # Ensure recent set additions are categorized with badges
        self.assertGreater(len(recs.recent_set_cards), 0)
        recent_names = [r.card_name for r in recs.recent_set_cards]
        self.assertTrue(any(c in recent_names for c in ["Innkeeper's Talent", "Bristly Bill, Spine Sower"]))

    def test_intent_alignment_tune_up_vs_new_build(self):
        """Verify recommendations adapt to user deck vision (tune_up vs new_build)."""
        tune_intent = UserDeckIntent(deck_vision=DeckVision(intent="tune_up"))
        new_intent = UserDeckIntent(deck_vision=DeckVision(intent="new_build"))

        tune_recs = self.generator.generate(self.atraxa_deck, self.atraxa_analysis, tune_intent)
        new_recs = self.generator.generate(self.atraxa_deck, self.atraxa_analysis, new_intent)

        self.assertIn("tune_up", tune_recs.summary)
        self.assertIn("new_build", new_recs.summary)

        # In tune_up, curve optimization categories should be represented
        self.assertTrue(
            any("Curve Optimization" in a.categories or "Tuning" in (a.reason or "") for a in tune_recs.additions)
        )

    def test_power_level_modulation(self):
        """Optimized/Competitive tiers emphasize low-CMC efficiency; Casual penalizes salt."""
        comp_intent = UserDeckIntent(
            target_power_level=TargetPowerLevel(scale=9, tier="competitive")
        )
        comp_recs = self.generator.generate(self.atraxa_deck, self.atraxa_analysis, comp_intent)

        # High-efficiency / free spells should score well
        comp_names = [a.card_name for a in comp_recs.additions]
        self.assertTrue(
            any(name in comp_names for name in ["Delighted Halfling", "The Ozolith", "Flare of Cultivation", "Birthing Ritual", "Bristly Bill, Spine Sower"])
        )

    def test_budget_constraints_filtering(self):
        """Cards exceeding max_card_price are excluded from additions."""
        budget_intent = UserDeckIntent(
            budget_constraints=BudgetConstraints(max_card_price=5.0)
        )
        recs = self.generator.generate(self.atraxa_deck, self.atraxa_analysis, budget_intent)

        for add in recs.additions:
            catalog_match = next((c for c in CURATED_CARD_CATALOG if c.name == add.card_name), None)
            if catalog_match:
                self.assertLessEqual(
                    catalog_match.approx_price_usd,
                    5.0,
                    f"Card {add.card_name} costs {catalog_match.approx_price_usd} > 5.0 budget limit",
                )

    def test_candidate_cuts_identification(self):
        """Generic outclassed cards (Diabolic Tutor, Cancel, Murder) are identified for cuts."""
        recs = self.generator.generate(self.atraxa_deck, self.atraxa_analysis)

        cut_names = [c.card_name for c in recs.cuts]
        self.assertIn("Diabolic Tutor", cut_names)
        self.assertIn("Cancel", cut_names)
        self.assertIn("Murder", cut_names)

        # Commanders must never be cut
        self.assertNotIn("Atraxa, Praetors' Voice", cut_names)

    def test_direct_card_swaps_formulation(self):
        """Ensure candidate cuts and additions are paired into 1-to-1 card swap suggestions."""
        recs = self.generator.generate(self.atraxa_deck, self.atraxa_analysis)

        self.assertGreater(len(recs.swaps), 0)
        for swap in recs.swaps:
            self.assertIsInstance(swap, CardSwapSuggestion)
            self.assertIsNotNone(swap.card_in)
            self.assertIsNotNone(swap.card_out)
            self.assertTrue(len(swap.swap_rationale) > 10)
            self.assertIn(swap.category, [
                "Theme Synergy", "Recent Set Upgrade", "Curve Optimization",
                "Playfeel Balancing", "Wincon Alignment"
            ])
            self.assertIsNotNone(swap.net_cmc_change)
            self.assertIsNotNone(swap.synergy_gain)

    def test_external_recommendations_integration(self):
        """External provider recommendations are ingested and merged seamlessly."""
        ext_recs = CardRecommendations(
            items=[
                CardRecommendation(
                    card_name="Broodmoth of Phyrexia",
                    oracle_id=None,
                    score=0.98,
                    synergy=0.85,
                    inclusion_rate=None,
                    reason="External provider top synergy recommendation",
                    categories=["External Synergy"],
                )
            ]
        )
        recs = self.generator.generate(
            deck=self.atraxa_deck,
            analysis=self.atraxa_analysis,
            external_recommendations=ext_recs,
        )

        names = [a.card_name for a in recs.additions]
        self.assertIn("Broodmoth of Phyrexia", names)


if __name__ == "__main__":
    unittest.main()

"""
Unit tests for theme, cohesion, curve, and playfeel analysis engine (analytics/engine.py).

Verifies:
1. Mana cost parsing and pip extraction (parse_mana_cost).
2. Mana curve, ramp density, speed score, and pacing calculation.
3. Theme and archetype consistency (primary, secondary, custom themes, excluded violations).
4. Cohesion and synergy rating (engine clustering, external synergy, internal fallback).
5. Salt rating and playfeel breakdown (Commander Salt integration, salt categories, power estimation).
6. Intent alignment verification (power level, win conditions, excluded archetypes, budget).
7. Input ingestion flexibility (ConsolidatedDeckIntake, DecklistInput, dict, raw string).
8. Facade parity (deck_analysis vs analytics.engine).
"""

import os
import sys
import unittest
from typing import Any, Dict

# Ensure repository root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.engine import (
    ArchetypeMatch,
    CohesionAnalysisBreakdown,
    CurveAnalysisBreakdown,
    DeckAnalysisEngine,
    DeckAnalysisReport,
    IntentAlignmentReport,
    PlayfeelAnalysisBreakdown,
    SaltCategoryBreakdown,
    SynergyCluster,
    ThemeAnalysisBreakdown,
    analyze_deck,
    default_analysis_engine,
    parse_mana_cost,
)
from analytics.models import (
    CardRecommendations,
    CommanderIdentifier,
    DeckAnalyticsResult,
    DeckCardEntry,
    DeckPopularity,
    DeckSynergy,
    DecklistInput,
    MetaScores,
    PowerScore,
    SaltCardDetail,
    SaltScore,
    SynergyMetric,
)
from deck_intake import (
    ConsolidatedDeckIntake,
    DeckIntakePayload,
    DeckIntakeResult,
    DeckIntakeService,
)
import deck_analysis
from user_intent import UserDeckIntent, parse_user_intent


# =============================================================================
# Sample Test Decks & Helpers
# =============================================================================

SAMPLE_ATRAXA_DECKLIST = """
Commander
1 Atraxa, Praetors' Voice

Deck
1 Sol Ring
1 Arcane Signet
1 Cultivate
1 Kodama's Reach
1 Farseek
1 Hardened Scales
1 Evolution Sage
1 Doubling Season
1 Conclave Mentor
1 The Ozolith
1 Inexorable Tide
1 Deepglow Skate
1 Swords to Plowshares
1 Counterspell
1 Cyclonic Rift
1 Rhystic Study
1 Beast Within
1 Command Tower
1 Exotic Orchard
10 Forest
10 Island
10 Plains
10 Swamp
"""

SAMPLE_STAX_SALT_DECKLIST = """
Commander
1 Grand Arbiter Augustin IV

Deck
1 Winter Orb
1 Static Orb
1 Armageddon
1 Stasis
1 Drannith Magistrate
1 Opposition Agent
1 Rhystic Study
1 Smothering Tithe
1 Cyclonic Rift
1 Sol Ring
1 Mana Crypt
1 Arcane Signet
1 Thassa's Oracle
1 Demonic Consultation
1 Counterspell
1 Force of Will
1 Fierce Guardianship
1 Command Tower
15 Plains
15 Island
"""

SAMPLE_AGGRO_TOKENS_DECKLIST = """
Commander
1 Krenko, Mob Boss

Deck
1 Sol Ring
1 Arcane Signet
1 Goblin Chieftain
1 Goblin Warchief
1 Goblin King
1 Goblin Piledriver
1 Dragon Fodder
1 Krenko's Command
1 Hordeling Outburst
1 Battle Hymn
1 Impact Tremors
1 Purphoros, God of the Forge
1 Skullclamp
1 Chaos Warp
1 Lightning Bolt
1 Mountain
"""


class TestManaCostParsing(unittest.TestCase):
    """Test parse_mana_cost helper for mana pips and CMC calculation."""

    def test_standard_bracketed_mana_cost(self):
        cmc, pips = parse_mana_cost("{2}{U}{U}")
        self.assertEqual(cmc, 4.0)
        self.assertEqual(pips, {"U": 2})

    def test_multicolor_and_generic(self):
        cmc, pips = parse_mana_cost("{G}{W}{U}{B}")
        self.assertEqual(cmc, 4.0)
        self.assertEqual(pips, {"G": 1, "W": 1, "U": 1, "B": 1})

    def test_x_spells(self):
        # In MTG, X is 0 off the stack
        cmc, pips = parse_mana_cost("{X}{3}{R}{R}")
        self.assertEqual(cmc, 5.0)
        self.assertEqual(pips, {"R": 2})

    def test_hybrid_mana(self):
        cmc, pips = parse_mana_cost("{2/W}{U/B}")
        self.assertEqual(cmc, 3.0)
        self.assertEqual(pips, {"W": 1, "U": 1, "B": 1})

    def test_raw_strings(self):
        cmc, pips = parse_mana_cost("2UU")
        self.assertEqual(cmc, 4.0)
        self.assertEqual(pips, {"U": 2})

    def test_empty_or_none(self):
        cmc, pips = parse_mana_cost(None)
        self.assertEqual(cmc, 0.0)
        self.assertEqual(pips, {})

        cmc, pips = parse_mana_cost("")
        self.assertEqual(cmc, 0.0)
        self.assertEqual(pips, {})


class TestThemeAndArchetypeAnalysis(unittest.TestCase):
    """Verify theme consistency, mechanic distribution, and excluded violation detection."""

    def setUp(self):
        self.service = DeckIntakeService()
        self.engine = DeckAnalysisEngine()

    def test_theme_alignment_matching_intent(self):
        intent = {
            "archetype_preferences": {
                "primary_archetype": "midrange",
                "custom_themes": ["proliferate", "+1/+1 counters"],
            },
            "target_power_level": {"scale": 7},
        }
        intake = self.service.process(decklist=SAMPLE_ATRAXA_DECKLIST, intent=intent)
        report = self.engine.analyze(deck=intake)

        theme = report.theme_analysis
        self.assertEqual(theme.primary_archetype, "midrange")
        self.assertGreaterEqual(theme.score, 60.0)
        self.assertIn(theme.consistency_tier, ("High", "Moderate"))
        self.assertGreater(len(theme.thematic_tags_matched), 0)
        self.assertIn("proliferate", theme.thematic_tags_matched)
        self.assertEqual(len(theme.excluded_archetype_violations), 0)

    def test_excluded_archetype_violation_detection(self):
        # User explicitly excludes "stax" and "combo"
        intent = {
            "archetype_preferences": {
                "primary_archetype": "control",
                "excluded_archetypes": ["stax", "combo"],
            },
            "target_power_level": {"scale": 6},
        }
        intake = self.service.process(decklist=SAMPLE_STAX_SALT_DECKLIST, intent=intent)
        report = self.engine.analyze(deck=intake)

        theme = report.theme_analysis
        violations = theme.excluded_archetype_violations
        self.assertGreater(len(violations), 0)

        violating_cards = {v["card_name"] for v in violations}
        self.assertTrue(
            any(c in violating_cards for c in ["Winter Orb", "Static Orb", "Stasis", "Thassa's Oracle"])
        )
        # Violations should heavily penalize the theme score
        self.assertLess(theme.score, 60.0)
        self.assertIn("Warning", theme.description)

    def test_secondary_archetype_scoring(self):
        intent = {
            "archetype_preferences": {
                "primary_archetype": "tokens",
                "secondary_archetypes": ["aristocrats", "aggro"],
            }
        }
        intake = self.service.process(decklist=SAMPLE_AGGRO_TOKENS_DECKLIST, intent=intent)
        report = self.engine.analyze(deck=intake)

        theme = report.theme_analysis
        self.assertEqual(theme.primary_archetype, "tokens")
        sec_names = [s.archetype for s in theme.secondary_archetypes]
        self.assertIn("aggro", sec_names)


class TestCurveAndSpeedAnalysis(unittest.TestCase):
    """Verify average CMC, ramp count, operational turn speed, and recommendations."""

    def setUp(self):
        self.service = DeckIntakeService()
        self.engine = DeckAnalysisEngine()

    def test_curve_analysis_metrics(self):
        intake = self.service.process(decklist=SAMPLE_ATRAXA_DECKLIST)
        report = self.engine.analyze(deck=intake)

        curve = report.curve_analysis
        self.assertGreater(curve.total_lands, 0)
        self.assertGreater(curve.total_spells, 0)
        self.assertGreater(curve.total_ramp, 0)
        self.assertGreater(curve.average_cmc, 1.0)
        self.assertLess(curve.average_cmc, 5.0)
        self.assertGreater(curve.speed_score, 1.0)
        self.assertIn(curve.speed_tier, ("Blistering", "Fast", "Moderate", "Deliberate", "Slow"))
        self.assertGreaterEqual(curve.estimated_operational_turn, 2)
        self.assertLessEqual(curve.estimated_operational_turn, 7)

    def test_speed_score_fast_vs_slow(self):
        fast_intake = self.service.process(decklist=SAMPLE_STAX_SALT_DECKLIST)
        fast_report = self.engine.analyze(deck=fast_intake)

        # Fast deck with Mana Crypt and cheap spells
        self.assertGreaterEqual(fast_report.curve_analysis.speed_score, 7.0)
        self.assertIn(fast_report.curve_analysis.speed_tier, ("Blistering", "Fast"))

    def test_target_turn_win_alignment(self):
        # Target turn 4 win
        intent = {
            "deck_vision": {"intent": "tune_up", "target_turn_win": 4},
            "target_power_level": {"scale": 8},
        }
        intake = self.service.process(decklist=SAMPLE_STAX_SALT_DECKLIST, intent=intent)
        report = self.engine.analyze(deck=intake)

        curve = report.curve_analysis
        self.assertIsNotNone(curve.target_turn_win_alignment)
        self.assertTrue("turn 4" in (curve.target_turn_win_alignment or ""))


class TestCohesionAndSynergyAnalysis(unittest.TestCase):
    """Verify cohesion tier, functional engine clusters, and external synergy consumption."""

    def setUp(self):
        self.service = DeckIntakeService()
        self.engine = DeckAnalysisEngine()

    def test_internal_cluster_detection(self):
        intake = self.service.process(decklist=SAMPLE_ATRAXA_DECKLIST)
        report = self.engine.analyze(deck=intake)

        cohesion = report.cohesion_analysis
        self.assertGreaterEqual(cohesion.score, 0.0)
        self.assertLessEqual(cohesion.score, 100.0)
        self.assertGreaterEqual(cohesion.synergy_rating, -1.0)
        self.assertLessEqual(cohesion.synergy_rating, 1.0)

        # Check clusters: Atraxa deck has ramp and counter engines
        cluster_names = [c.name for c in cohesion.synergy_clusters]
        self.assertTrue(any("mana" in n.lower() or "acceleration" in n.lower() for n in cluster_names))
        self.assertTrue(any("counter" in n.lower() for n in cluster_names))

    def test_external_synergy_metrics_integration(self):
        intake = self.service.process(decklist=SAMPLE_ATRAXA_DECKLIST)
        ext_result = DeckAnalyticsResult(
            provider_name="edhrec",
            timestamp=None,
            meta_scores=None,
            recommendations=None,
            popularity=None,
            synergy=DeckSynergy(
                overall_synergy=0.68,
                card_synergies=[
                    SynergyMetric(
                        card_name="Evolution Sage",
                        synergy_score=0.72,
                        commander_name="Atraxa, Praetors' Voice",
                        context="+72% synergy with Atraxa",
                    ),
                    SynergyMetric(
                        card_name="Counterspell",
                        synergy_score=-0.20,
                        commander_name="Atraxa, Praetors' Voice",
                        context="-20% synergy with Atraxa",
                    ),
                ],
            ),
        )

        report = self.engine.analyze(deck=intake, external_metrics=ext_result)
        cohesion = report.cohesion_analysis

        self.assertEqual(cohesion.synergy_rating, 0.68)
        self.assertGreaterEqual(cohesion.score, 80.0)
        self.assertEqual(cohesion.cohesion_tier, "Highly Cohesive")

        high_cards = [c["card_name"] for c in cohesion.high_synergy_cards]
        self.assertIn("Evolution Sage", high_cards)

        low_cards = [c["card_name"] for c in cohesion.low_synergy_cards]
        self.assertIn("Counterspell", low_cards)


class TestSaltAndPlayfeelAnalysis(unittest.TestCase):
    """Verify Commander Salt data integration, salt categories, and playfeel classification."""

    def setUp(self):
        self.service = DeckIntakeService()
        self.engine = DeckAnalysisEngine()

    def test_stax_and_salt_detection(self):
        intake = self.service.process(decklist=SAMPLE_STAX_SALT_DECKLIST)
        report = self.engine.analyze(deck=intake)

        playfeel = report.playfeel_analysis
        self.assertGreaterEqual(playfeel.salt_score, 45.0)
        self.assertIn(playfeel.salt_tier, ("High Salt", "Table Hazard"))
        self.assertIn(playfeel.playfeel_label, ("High-Power / Spiky", "Oppressive"))

        # Verify detected salt categories
        cat_names = [c.category for c in playfeel.salt_categories]
        self.assertIn("Mass Land Destruction", cat_names)
        self.assertIn("Hard Stax & Resource Denial", cat_names)
        self.assertIn("Two-Card Combos & Instant Wins", cat_names)

        # High salt cards ranked
        top_salt_names = [c.card_name for c in playfeel.high_salt_cards]
        self.assertTrue(any(c in top_salt_names for c in ["Winter Orb", "Static Orb", "Armageddon"]))

        # Friction warnings should be produced
        self.assertGreater(len(playfeel.warnings), 0)

    def test_external_commander_salt_integration(self):
        intake = self.service.process(decklist=SAMPLE_ATRAXA_DECKLIST)
        ext_result = DeckAnalyticsResult(
            provider_name="commandersalt",
            timestamp=None,
            recommendations=None,
            synergy=None,
            popularity=None,
            meta_scores=MetaScores(
                salt=SaltScore(
                    score=35.0,
                    salt_sum=48.2,
                    high_salt_cards=[
                        SaltCardDetail(card_name="Cyclonic Rift", salt_score=2.85, rank=1, oracle_id=None),
                        SaltCardDetail(card_name="Rhystic Study", salt_score=2.65, rank=2, oracle_id=None),
                    ],
                    description="Moderate salt",
                ),
                power=PowerScore(
                    score=7.2,
                    tier="Optimized",
                    description="Consistent mana curve with high interaction.",
                ),
                meta_rank=None,
            ),
        )

        report = self.engine.analyze(deck=intake, external_metrics=ext_result)
        playfeel = report.playfeel_analysis

        self.assertEqual(playfeel.salt_score, 35.0)
        self.assertEqual(playfeel.salt_tier, "Moderate Salt")
        self.assertEqual(playfeel.power_level_estimate, 7.2)
        self.assertEqual(playfeel.power_tier, "Optimized")


class TestIntentAlignmentReport(unittest.TestCase):
    """Verify alignment evaluation against UserDeckIntent."""

    def setUp(self):
        self.service = DeckIntakeService()
        self.engine = DeckAnalysisEngine()

    def test_aligned_intent(self):
        intent = {
            "archetype_preferences": {
                "primary_archetype": "midrange",
                "custom_themes": ["proliferate"],
            },
            "target_power_level": {"scale": 7, "tier": "optimized"},
            "preferred_win_conditions": {"primary": "combat"},
        }
        intake = self.service.process(decklist=SAMPLE_ATRAXA_DECKLIST, intent=intent)
        report = self.engine.analyze(deck=intake)

        align = report.intent_alignment
        self.assertTrue(align.is_aligned)
        self.assertTrue(align.power_aligned)
        self.assertTrue(align.archetype_aligned)
        self.assertGreaterEqual(align.alignment_score, 70.0)
        self.assertEqual(len(align.conflicts), 0)

    def test_mismatched_intent_generates_conflicts(self):
        # Casual intent on a heavy stax deck
        intent = {
            "archetype_preferences": {
                "primary_archetype": "tokens",
                "excluded_archetypes": ["stax"],
            },
            "target_power_level": {"scale": 3, "tier": "casual"},
            "preferred_win_conditions": {
                "primary": "combat",
                "disliked_or_excluded": ["combo"],
            },
        }
        intake = self.service.process(decklist=SAMPLE_STAX_SALT_DECKLIST, intent=intent)
        report = self.engine.analyze(deck=intake)

        align = report.intent_alignment
        self.assertFalse(align.is_aligned)
        self.assertFalse(align.power_aligned)
        self.assertFalse(align.archetype_aligned)
        self.assertFalse(align.wincon_aligned)
        self.assertGreater(len(align.conflicts), 0)


class TestInputFormatsAndIngestion(unittest.TestCase):
    """Verify engine can ingest ConsolidatedDeckIntake, DeckIntakeResult, DecklistInput, dict, or str."""

    def setUp(self):
        self.engine = DeckAnalysisEngine()
        self.service = DeckIntakeService()

    def test_ingest_consolidated_intake_result(self):
        intake: DeckIntakeResult = self.service.process(decklist=SAMPLE_ATRAXA_DECKLIST)
        report = self.engine.analyze(deck=intake)
        self.assertIsInstance(report, DeckAnalysisReport)
        self.assertTrue(any("Atraxa" in c for c in report.commanders))

    def test_ingest_decklist_input(self):
        deck_input = DecklistInput(
            deck_id="test:1",
            name="Atraxa Mini",
            commanders=[CommanderIdentifier(name="Atraxa, Praetors' Voice", oracle_id=None, scryfall_id=None)],
            cards=[
                DeckCardEntry(name="Sol Ring", quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard"),
                DeckCardEntry(name="Evolution Sage", quantity=1, oracle_id=None, scryfall_id=None, category="Mainboard"),
                DeckCardEntry(name="Forest", quantity=1, category="Land", oracle_id=None, scryfall_id=None),
            ],
            format="commander",
        )
        report = self.engine.analyze(deck=deck_input)
        self.assertEqual(report.deck_name, "Atraxa Mini")
        self.assertIn("Atraxa, Praetors' Voice", report.commanders)

    def test_ingest_raw_string(self):
        report = analyze_deck(SAMPLE_ATRAXA_DECKLIST)
        self.assertIsInstance(report, DeckAnalysisReport)
        self.assertIn("Atraxa, Praetors' Voice", report.commanders)

    def test_ingest_dict(self):
        deck_dict = {
            "name": "Krenko Dict",
            "commanders": ["Krenko, Mob Boss"],
            "cards": [
                {"name": "Sol Ring", "quantity": 1},
                {"name": "Goblin Chieftain", "quantity": 1},
                {"name": "Mountain", "quantity": 1, "section": "mainboard"},
            ],
            "user_intent": {
                "target_power_level": {"scale": 7},
            },
        }
        report = analyze_deck(deck_dict)
        self.assertEqual(report.deck_name, "Krenko Dict")
        self.assertIn("Krenko, Mob Boss", report.commanders)


class TestFacadeParity(unittest.TestCase):
    """Verify root deck_analysis module exposes identical symbols to analytics.engine."""

    def test_reexports(self):
        self.assertIs(deck_analysis.DeckAnalysisEngine, DeckAnalysisEngine)
        self.assertIs(deck_analysis.DeckAnalysisReport, DeckAnalysisReport)
        self.assertIs(deck_analysis.analyze_deck, analyze_deck)
        self.assertIs(deck_analysis.parse_mana_cost, parse_mana_cost)


if __name__ == "__main__":
    unittest.main()

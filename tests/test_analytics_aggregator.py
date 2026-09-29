"""
Unit tests for the deck analytics aggregator service (analytics.aggregator).

Verifies:
1. Aggregator initialization, configuration (timeouts, enabled providers, registry).
2. Input coercion across varied payload shapes (DecklistInput, dict, library deck, raw string).
3. Concurrent execution across providers.
4. Configurable per-provider and global timeouts.
5. Error isolation and partial success handling.
6. Metric consolidation logic (meta scores, recommendations, synergy, popularity, summary).
7. Async aggregation (aggregate_async).
8. Functional API (aggregate_deck_analytics).
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
import unittest
from typing import List, Optional

# Ensure repository root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.aggregator import (
    AggregatedAnalyticsReport,
    AnalyticsSummary,
    ConsolidatedMetrics,
    DeckAnalyticsAggregator,
    ProviderAnalyticsStatus,
    aggregate_deck_analytics,
    coerce_to_decklist_input,
)
from analytics.exceptions import AnalyticsProviderError
from analytics.models import (
    CardCutRecommendation,
    CardRecommendation,
    CardRecommendations,
    CommanderIdentifier,
    DeckAnalyticsResult,
    DeckCardEntry,
    DeckPopularity,
    DeckSynergy,
    DecklistInput,
    MetaScores,
    PopularityMetric,
    PowerScore,
    SaltCardDetail,
    SaltScore,
    SynergyMetric,
)
from analytics.provider import BaseAnalyticsProvider
from analytics.registry import AnalyticsProviderRegistry


# =============================================================================
# Mock Providers for Controlled Unit Testing
# =============================================================================

class MockFastProvider(BaseAnalyticsProvider):
    """Fast provider returning complete analytics."""
    SUPPORTED_QUERIES = {"meta_scores", "recommendations", "synergy", "popularity"}

    @property
    def name(self) -> str:
        return "mock_fast"

    def get_meta_scores(self, deck: DecklistInput) -> MetaScores:
        return MetaScores(
            salt=SaltScore(
                score=20.0,
                salt_sum=25.0,
                high_salt_cards=[
                    SaltCardDetail(card_name="Cyclonic Rift", salt_score=2.5, rank=1, oracle_id=None),
                    SaltCardDetail(card_name="Rhystic Study", salt_score=2.0, rank=2, oracle_id=None),
                ],
                description=None,
            ),
            power=PowerScore(score=7.0, tier="Optimized", breakdown={"ramp": 8.0}, description=None),
            meta_rank=85.0,
            provider_metrics={"fast_score": 100},
        )

    def get_recommendations(self, deck: DecklistInput) -> CardRecommendations:
        return CardRecommendations(
            items=[
                CardRecommendation(
                    card_name="Heroic Intervention",
                    synergy=0.6,
                    inclusion_rate=0.7,
                    reason="Protection for wide boards",
                    oracle_id=None,
                    score=None,
                    categories=[],
                ),
                CardRecommendation(
                    card_name="Sol Ring",  # Already in deck, should be filtered out
                    synergy=0.9,
                    inclusion_rate=0.95,
                    reason=None,
                    oracle_id=None,
                    score=None,
                    categories=[],
                ),
            ],
            cuts=[
                CardCutRecommendation(
                    card_name="Iname, Death Aspect",
                    synergy=-0.4,
                    reason="Too slow and low synergy",
                    oracle_id=None,
                )
            ],
            total=2,
        )

    def get_synergy(self, deck: DecklistInput) -> DeckSynergy:
        return DeckSynergy(
            overall_synergy=0.5,
            card_synergies=[
                SynergyMetric(card_name="Evolution Sage", synergy_score=0.7, commander_name=None, context=None),
                SynergyMetric(card_name="Deepglow Skate", synergy_score=0.6, commander_name=None, context=None),
            ],
        )

    def get_popularity(self, deck: DecklistInput) -> DeckPopularity:
        return DeckPopularity(
            rank=5,
            num_decks=12000,
            popularity_percentile=98.0,
            card_popularity=[
                PopularityMetric(card_name="Sol Ring", deck_count=11500, percentage=95.8, rank=1),
            ],
        )


class MockSaltProvider(BaseAnalyticsProvider):
    """Provider returning meta_scores and combo detection (similar to Commander Salt)."""
    SUPPORTED_QUERIES = {"meta_scores"}

    @property
    def name(self) -> str:
        return "mock_salt"

    def get_meta_scores(self, deck: DecklistInput) -> MetaScores:
        return MetaScores(
            salt=SaltScore(
                score=30.0,
                salt_sum=35.0,
                high_salt_cards=[
                    SaltCardDetail(card_name="Cyclonic Rift", salt_score=2.8, rank=1, oracle_id=None),
                    SaltCardDetail(card_name="Demonic Tutor", salt_score=2.2, rank=2, oracle_id=None),
                ],
                description=None,
            ),
            power=PowerScore(score=8.0, tier="cEDH", breakdown={"combos": 9.0}, description=None),
            meta_rank=None,
            provider_metrics={"combos": {"count": 2, "details": []}},
        )

    def get_recommendations(self, deck: DecklistInput) -> CardRecommendations:
        raise NotImplementedError

    def get_synergy(self, deck: DecklistInput) -> DeckSynergy:
        raise NotImplementedError

    def get_popularity(self, deck: DecklistInput) -> DeckPopularity:
        raise NotImplementedError


class MockSlowProvider(BaseAnalyticsProvider):
    """Provider that sleeps to simulate network latency / timeouts."""
    SUPPORTED_QUERIES = {"meta_scores"}

    def __init__(self, delay_seconds: float = 0.5) -> None:
        self.delay_seconds = delay_seconds

    @property
    def name(self) -> str:
        return "mock_slow"

    def get_meta_scores(self, deck: DecklistInput) -> MetaScores:
        time.sleep(self.delay_seconds)
        return MetaScores(
            salt=SaltScore(score=10.0, salt_sum=None, high_salt_cards=[], description=None),
            power=None,
            meta_rank=None,
            provider_metrics={},
        )

    def get_recommendations(self, deck: DecklistInput) -> CardRecommendations:
        raise NotImplementedError

    def get_synergy(self, deck: DecklistInput) -> DeckSynergy:
        raise NotImplementedError

    def get_popularity(self, deck: DecklistInput) -> DeckPopularity:
        raise NotImplementedError


class MockFailingProvider(BaseAnalyticsProvider):
    """Provider that raises an error during execution."""
    SUPPORTED_QUERIES = {"meta_scores"}

    @property
    def name(self) -> str:
        return "mock_failing"

    def get_meta_scores(self, deck: DecklistInput) -> MetaScores:
        raise AnalyticsProviderError("Remote service unavailable (HTTP 503)")

    def get_recommendations(self, deck: DecklistInput) -> CardRecommendations:
        raise NotImplementedError

    def get_synergy(self, deck: DecklistInput) -> DeckSynergy:
        raise NotImplementedError

    def get_popularity(self, deck: DecklistInput) -> DeckPopularity:
        raise NotImplementedError


# =============================================================================
# Test Suite
# =============================================================================

class TestDeckAnalyticsAggregator(unittest.TestCase):
    def setUp(self) -> None:
        self.registry = AnalyticsProviderRegistry()
        self.fast_provider = MockFastProvider()
        self.salt_provider = MockSaltProvider()
        self.slow_provider = MockSlowProvider(delay_seconds=0.3)
        self.failing_provider = MockFailingProvider()

        self.registry.register(self.fast_provider)
        self.registry.register(self.salt_provider)
        self.registry.register(self.slow_provider)
        self.registry.register(self.failing_provider)

        self.sample_deck = DecklistInput(
            deck_id="archidekt:123",
            name="Atraxa Proliferate",
            commanders=[CommanderIdentifier(name="Atraxa, Praetors' Voice", oracle_id=None, scryfall_id=None)],
            cards=[
                DeckCardEntry(name="Sol Ring", quantity=1, oracle_id=None, scryfall_id=None, category=None),
                DeckCardEntry(name="Arcane Signet", quantity=1, oracle_id=None, scryfall_id=None, category=None),
                DeckCardEntry(name="Doubling Season", quantity=1, oracle_id=None, scryfall_id=None, category=None),
            ],
            format="commander",
        )

    def test_coerce_to_decklist_input(self) -> None:
        # 1. DecklistInput returns self
        res1 = coerce_to_decklist_input(self.sample_deck)
        self.assertIs(res1, self.sample_deck)

        # 2. Dict with standard shape
        raw_dict = {
            "name": "Standard Dict Deck",
            "commanders": [{"name": "Edgar Markov"}],
            "cards": [{"name": "Blood Artist", "quantity": 1}],
            "format": "commander",
        }
        res2 = coerce_to_decklist_input(raw_dict)
        self.assertEqual(res2.name, "Standard Dict Deck")
        self.assertEqual(len(res2.commanders), 1)
        self.assertEqual(res2.commanders[0].name, "Edgar Markov")

        # 3. Library deck dict with string commanders and cards
        lib_dict = {
            "registry_id": "moxfield:999",
            "name": "Library Deck",
            "commanders": ["Krenko, Mob Boss"],
            "cards": ["Goblin Chieftain", "Goblin Warchief"],
        }
        res3 = coerce_to_decklist_input(lib_dict)
        self.assertEqual(res3.deck_id, "moxfield:999")
        self.assertEqual(res3.commanders[0].name, "Krenko, Mob Boss")
        self.assertEqual(len(res3.cards), 2)
        self.assertEqual(res3.cards[0].name, "Goblin Chieftain")

        # 4. Raw string decklist
        raw_str = "1 Sol Ring\n1 Command Tower\n"
        res4 = coerce_to_decklist_input(raw_str)
        self.assertGreaterEqual(len(res4.commanders), 1)
        self.assertGreaterEqual(len(res4.cards), 1)

        # 5. Unsupported type raises ValueError
        with self.assertRaises(ValueError):
            coerce_to_decklist_input(12345)

    def test_aggregator_initialization_and_provider_discovery(self) -> None:
        aggregator = DeckAnalyticsAggregator(registry=self.registry, default_timeout=5.0)
        self.assertEqual(aggregator.default_timeout, 5.0)
        available = aggregator.get_available_providers()
        names = [p["name"] for p in available]
        self.assertIn("mock_fast", names)
        self.assertIn("mock_salt", names)
        self.assertIn("mock_slow", names)
        self.assertIn("mock_failing", names)

    def test_single_provider_aggregation_success(self) -> None:
        aggregator = DeckAnalyticsAggregator(registry=self.registry)
        report = aggregator.aggregate(self.sample_deck, providers=["mock_fast"])

        self.assertIsInstance(report, AggregatedAnalyticsReport)
        self.assertEqual(report.deck_name, "Atraxa Proliferate")
        self.assertEqual(report.providers_queried, ["mock_fast"])
        self.assertEqual(report.successful_providers, ["mock_fast"])
        self.assertEqual(len(report.failed_providers), 0)

        status = report.provider_statuses["mock_fast"]
        self.assertEqual(status.status, "success")
        self.assertGreater(status.execution_time_ms, 0.0)
        self.assertIsNone(status.error)

        metrics = report.consolidated_metrics
        self.assertIsNotNone(metrics.meta_scores)
        self.assertEqual(metrics.meta_scores.salt.score, 20.0)
        self.assertEqual(metrics.meta_scores.power.score, 7.0)
        self.assertEqual(metrics.summary.salt_score, 20.0)
        self.assertEqual(metrics.summary.power_level, 7.0)
        self.assertEqual(metrics.summary.power_tier, "Optimized")
        self.assertEqual(metrics.summary.overall_synergy, 0.5)
        self.assertEqual(metrics.summary.commander_rank, 5)

        # Ensure cards already in deck were filtered out from recommendations
        rec_names = [item.card_name for item in metrics.recommendations.items]
        self.assertIn("Heroic Intervention", rec_names)
        self.assertNotIn("Sol Ring", rec_names)

    def test_multi_provider_metric_consolidation(self) -> None:
        aggregator = DeckAnalyticsAggregator(registry=self.registry)
        report = aggregator.aggregate(
            self.sample_deck, providers=["mock_fast", "mock_salt"]
        )

        self.assertEqual(len(report.successful_providers), 2)
        metrics = report.consolidated_metrics

        # Salt scores averaged: (20.0 + 30.0) / 2 = 25.0
        self.assertEqual(metrics.meta_scores.salt.score, 25.0)
        self.assertEqual(metrics.summary.salt_score, 25.0)

        # High salt cards deduplicated and reranked
        high_salt = metrics.meta_scores.salt.high_salt_cards
        card_names = [c.card_name for c in high_salt]
        self.assertEqual(len(card_names), len(set(card_names)))  # No duplicates
        self.assertIn("Cyclonic Rift", card_names)
        self.assertIn("Demonic Tutor", card_names)
        self.assertIn("Rhystic Study", card_names)
        # Cyclonic Rift had scores 2.5 and 2.8, higher score 2.8 is preserved
        cr = next(c for c in high_salt if c.card_name == "Cyclonic Rift")
        self.assertEqual(cr.salt_score, 2.8)
        self.assertEqual(cr.rank, 1)

        # Power scores averaged: (7.0 + 8.0) / 2 = 7.5
        self.assertEqual(metrics.meta_scores.power.score, 7.5)
        self.assertEqual(metrics.summary.power_level, 7.5)

        # Combos retained from mock_salt
        self.assertIn("combos", metrics.meta_scores.provider_metrics)
        self.assertEqual(metrics.summary.total_combos, 2)

    def test_timeout_handling(self) -> None:
        aggregator = DeckAnalyticsAggregator(registry=self.registry, default_timeout=0.1)
        # mock_slow sleeps 0.3s, default_timeout is 0.1s -> should timeout
        report = aggregator.aggregate(
            self.sample_deck, providers=["mock_fast", "mock_slow"]
        )

        self.assertIn("mock_fast", report.successful_providers)
        self.assertIn("mock_slow", report.failed_providers)

        slow_status = report.provider_statuses["mock_slow"]
        self.assertEqual(slow_status.status, "timeout")
        self.assertIn("timed out", slow_status.error.lower())

        # Successful provider metrics are still fully consolidated
        self.assertIsNotNone(report.consolidated_metrics.meta_scores)
        self.assertEqual(report.consolidated_metrics.meta_scores.salt.score, 20.0)

    def test_error_isolation_on_failing_provider(self) -> None:
        aggregator = DeckAnalyticsAggregator(registry=self.registry)
        report = aggregator.aggregate(
            self.sample_deck, providers=["mock_fast", "mock_failing"]
        )

        self.assertIn("mock_fast", report.successful_providers)
        self.assertIn("mock_failing", report.failed_providers)

        fail_status = report.provider_statuses["mock_failing"]
        self.assertEqual(fail_status.status, "error")
        self.assertIn("503", fail_status.error)

        # The overall aggregation succeeded and consolidated available metrics
        self.assertIsNotNone(report.consolidated_metrics.meta_scores)

    def test_unknown_provider_status_not_found(self) -> None:
        aggregator = DeckAnalyticsAggregator(registry=self.registry)
        report = aggregator.aggregate(
            self.sample_deck, providers=["mock_fast", "non_existent_provider"]
        )

        self.assertIn("mock_fast", report.successful_providers)
        self.assertIn("non_existent_provider", report.failed_providers)
        self.assertEqual(
            report.provider_statuses["non_existent_provider"].status, "not_found"
        )

    def test_all_providers_failing_graceful_response(self) -> None:
        aggregator = DeckAnalyticsAggregator(registry=self.registry)
        report = aggregator.aggregate(
            self.sample_deck, providers=["mock_failing"]
        )

        self.assertEqual(len(report.successful_providers), 0)
        self.assertEqual(report.failed_providers, ["mock_failing"])
        self.assertIsNone(report.consolidated_metrics.meta_scores)
        self.assertEqual(report.consolidated_metrics.summary.total_recommendations, 0)

    def test_concurrent_execution_speed(self) -> None:
        # Two providers each sleeping 0.1s run in parallel in ~0.1s, not 0.2s
        reg = AnalyticsProviderRegistry()
        reg.register(MockSlowProvider(0.1), name="slow_1")
        reg.register(MockSlowProvider(0.1), name="slow_2")

        aggregator = DeckAnalyticsAggregator(registry=reg, default_timeout=1.0)
        start = time.perf_counter()
        report = aggregator.aggregate(self.sample_deck, providers=["slow_1", "slow_2"])
        duration = time.perf_counter() - start

        self.assertEqual(len(report.successful_providers), 2)
        # Should complete in well under 0.25s
        self.assertLess(duration, 0.25)

    def test_async_aggregation(self) -> None:
        aggregator = DeckAnalyticsAggregator(registry=self.registry)

        async def run_async():
            return await aggregator.aggregate_async(
                self.sample_deck, providers=["mock_fast", "mock_salt"]
            )

        report = asyncio.run(run_async())
        self.assertEqual(len(report.successful_providers), 2)
        self.assertEqual(report.consolidated_metrics.summary.salt_score, 25.0)

    def test_functional_convenience_api(self) -> None:
        # Patch default_aggregator's registry with our test registry
        from analytics.aggregator import default_aggregator
        orig_registry = default_aggregator.registry
        try:
            default_aggregator.registry = self.registry
            report = aggregate_deck_analytics(
                self.sample_deck, providers=["mock_fast"]
            )
            self.assertEqual(report.successful_providers, ["mock_fast"])
        finally:
            default_aggregator.registry = orig_registry


if __name__ == "__main__":
    unittest.main()

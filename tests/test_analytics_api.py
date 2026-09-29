"""
API integration tests for deck analytics REST endpoints in app.py:
- POST /api/analytics
- POST /api/decks/analytics
- GET /api/decks/{deck_id}/analytics
- GET /api/analytics/providers
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import patch

# Ensure repository root is in sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from app import app
from analytics.aggregator import (
    AggregatedAnalyticsReport,
    AnalyticsSummary,
    ConsolidatedMetrics,
    DeckAnalyticsAggregator,
    ProviderAnalyticsStatus,
    default_aggregator,
)
from analytics.models import (
    CardRecommendation,
    CardRecommendations,
    CommanderIdentifier,
    DeckAnalyticsResult,
    DeckCardEntry,
    DecklistInput,
    MetaScores,
    PowerScore,
    SaltScore,
)
from analytics.provider import BaseAnalyticsProvider
from analytics.registry import AnalyticsProviderRegistry


class DummyProvider(BaseAnalyticsProvider):
    SUPPORTED_QUERIES = {"meta_scores", "recommendations"}

    @property
    def name(self) -> str:
        return "dummy"

    def get_meta_scores(self, deck: DecklistInput) -> MetaScores:
        return MetaScores(
            salt=SaltScore(score=18.5, salt_sum=22.0, high_salt_cards=[], description=None),
            power=PowerScore(score=7.0, tier="Optimized", breakdown={"ramp": 7.5}, description=None),
            meta_rank=80.0,
            provider_metrics={},
        )

    def get_recommendations(self, deck: DecklistInput) -> CardRecommendations:
        return CardRecommendations(
            items=[
                CardRecommendation(
                    card_name="Heroic Intervention",
                    synergy=0.65,
                    inclusion_rate=0.72,
                    reason="Board protection",
                    oracle_id=None,
                    score=None,
                    categories=[],
                )
            ],
            cuts=[],
            total=1,
        )

    def get_synergy(self, deck: DecklistInput):
        raise NotImplementedError

    def get_popularity(self, deck: DecklistInput):
        raise NotImplementedError


class TestAnalyticsAPI(unittest.TestCase):
    def setUp(self) -> None:
        self.client = TestClient(app)
        self.test_registry = AnalyticsProviderRegistry()
        self.dummy_provider = DummyProvider()
        self.test_registry.register(self.dummy_provider)

        # Point default_aggregator to our test registry for predictable responses
        self.orig_registry = default_aggregator.registry
        default_aggregator.registry = self.test_registry

        self.valid_deck_payload = {
            "name": "API Atraxa Test",
            "format": "commander",
            "commanders": [{"name": "Atraxa, Praetors' Voice"}],
            "cards": [
                {"name": "Sol Ring", "quantity": 1},
                {"name": "Arcane Signet", "quantity": 1},
            ],
        }

    def tearDown(self) -> None:
        default_aggregator.registry = self.orig_registry

    def test_post_analytics_valid_payload_returns_200(self) -> None:
        response = self.client.post("/api/analytics", json=self.valid_deck_payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        report = data.get("report", {})
        self.assertEqual(report.get("deck_name"), "API Atraxa Test")
        self.assertIn("dummy", report.get("successful_providers", []))
        self.assertIn("dummy", report.get("provider_statuses", {}))
        self.assertEqual(
            report["provider_statuses"]["dummy"]["status"], "success"
        )
        self.assertEqual(
            report["consolidated_metrics"]["summary"]["power_tier"], "Optimized"
        )

    def test_post_decks_analytics_alias_endpoint(self) -> None:
        response = self.client.post("/api/decks/analytics", json=self.valid_deck_payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        self.assertIn("report", data)

    def test_post_analytics_wrapped_payload_with_options(self) -> None:
        payload = {
            "deck": self.valid_deck_payload,
            "providers": ["dummy"],
            "timeout": 5.0,
        }
        response = self.client.post("/api/analytics", json=payload)
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        report = data.get("report", {})
        self.assertEqual(report.get("providers_queried"), ["dummy"])

    def test_post_analytics_invalid_json_returns_400(self) -> None:
        response = self.client.post(
            "/api/analytics",
            content="not a json string",
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data.get("success"))
        self.assertIn("Invalid JSON body", data.get("error", ""))

    def test_post_analytics_invalid_timeout_returns_400(self) -> None:
        payload = {
            **self.valid_deck_payload,
            "timeout": "not-a-valid-float",
        }
        response = self.client.post("/api/analytics", json=payload)
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data.get("success"))
        self.assertIn("timeout", data.get("error", "").lower())

    def test_post_analytics_validation_error_returns_422(self) -> None:
        # Empty commanders list violates DecklistInput constraint
        invalid_payload = {
            "name": "Invalid Deck",
            "commanders": [],
            "cards": [{"name": "Sol Ring", "quantity": 1}],
        }
        response = self.client.post("/api/analytics", json=invalid_payload)
        self.assertEqual(response.status_code, 422)
        data = response.json()
        self.assertFalse(data.get("success"))
        self.assertIn("Deck validation error", data.get("error", ""))

    @patch("app.registry.find_deck")
    def test_post_analytics_by_existing_deck_id(self, mock_find_deck) -> None:
        mock_find_deck.return_value = {
            "registry_id": "archidekt:123",
            "name": "Atraxa Stored",
            "commanders": ["Atraxa, Praetors' Voice"],
            "cards": [{"name": "Sol Ring", "quantity": 1}],
        }
        response = self.client.post("/api/analytics", json={"deck_id": "archidekt:123"})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        report = data.get("report", {})
        self.assertEqual(report.get("deck_name"), "Atraxa Stored")

    @patch("app.registry.find_deck")
    def test_post_analytics_by_unknown_deck_id_returns_404(self, mock_find_deck) -> None:
        mock_find_deck.return_value = None
        response = self.client.post("/api/analytics", json={"deck_id": "unknown:999"})
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertFalse(data.get("success"))
        self.assertIn("not found", data.get("error", "").lower())

    @patch("app.registry.find_deck")
    def test_get_deck_analytics_endpoint_success(self, mock_find_deck) -> None:
        mock_find_deck.return_value = {
            "registry_id": "archidekt:123",
            "name": "Atraxa Library",
            "commanders": ["Atraxa, Praetors' Voice"],
            "cards": [{"name": "Sol Ring", "quantity": 1}],
        }
        response = self.client.get("/api/decks/archidekt:123/analytics?timeout=3.0")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        report = data.get("report", {})
        self.assertEqual(report.get("deck_name"), "Atraxa Library")

    @patch("app.registry.find_deck")
    def test_get_deck_analytics_endpoint_not_found(self, mock_find_deck) -> None:
        mock_find_deck.return_value = None
        response = self.client.get("/api/decks/unknown:999/analytics")
        self.assertEqual(response.status_code, 404)
        data = response.json()
        self.assertFalse(data.get("success"))
        self.assertIn("not found", data.get("error", "").lower())

    def test_post_analytics_non_object_json_returns_400(self) -> None:
        response = self.client.post("/api/analytics", json=["invalid", "array", "payload"])
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data.get("success"))
        self.assertIn("JSON object", data.get("error", ""))

    def test_post_analytics_invalid_providers_type_returns_400(self) -> None:
        payload = {
            "deck": self.valid_deck_payload,
            "providers": "dummy",  # string instead of list of strings
        }
        response = self.client.post("/api/analytics", json=payload)
        self.assertEqual(response.status_code, 400)
        data = response.json()
        self.assertFalse(data.get("success"))
        self.assertIn("list of strings", data.get("error", ""))

        # Non-string entries in list
        payload2 = {
            "deck": self.valid_deck_payload,
            "providers": [123, False],
        }
        response2 = self.client.post("/api/analytics", json=payload2)
        self.assertEqual(response2.status_code, 400)

    def test_post_analytics_invalid_quantity_returns_422(self) -> None:
        payload = {
            "name": "Invalid Qty Deck",
            "commanders": ["Atraxa, Praetors' Voice"],
            "cards": [{"name": "Sol Ring", "quantity": 0}],
        }
        response = self.client.post("/api/analytics", json=payload)
        self.assertEqual(response.status_code, 422)
        data = response.json()
        self.assertFalse(data.get("success"))
        self.assertIn("Deck validation error", data.get("error", ""))

    def test_get_analytics_providers_list(self) -> None:
        response = self.client.get("/api/analytics/providers")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data.get("success"))
        providers = data.get("providers", [])
        self.assertGreaterEqual(len(providers), 1)
        dummy_entry = next((p for p in providers if p["name"] == "dummy"), None)
        self.assertIsNotNone(dummy_entry)
        assert dummy_entry is not None
        self.assertIn("meta_scores", dummy_entry.get("supported_queries", []))
        self.assertIn("recommendations", dummy_entry.get("supported_queries", []))


if __name__ == "__main__":
    unittest.main()

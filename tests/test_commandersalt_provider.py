"""
Unit tests for the Commander Salt analytics provider adapter.

Uses a stubbed `requests.Session` (no live network calls) to verify HTTP
retry/rate-limit behavior, response-shape error handling, deck identifier
resolution, and schema mapping onto the unified analytics domain models.

Run: python tests/test_commandersalt_provider.py
"""
import os
import sys
import unittest
from unittest.mock import MagicMock

# Ensure project root is in path for standalone execution
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from analytics.exceptions import UnsupportedAnalyticsQueryError
from analytics.models import CommanderIdentifier, DeckCardEntry, DecklistInput
from analytics.providers.commandersalt import (
    CommanderSaltClient,
    CommanderSaltNotFoundError,
    CommanderSaltNotIngestedError,
    CommanderSaltProvider,
    CommanderSaltRequestError,
    CommanderSaltResponseError,
    resolve_source_url,
)


def _make_response(status_code=200, json_body=None, json_error=False):
    response = MagicMock()
    response.status_code = status_code
    if json_error:
        response.json.side_effect = ValueError("invalid json")
    else:
        response.json.return_value = json_body
    return response


def _make_deck(deck_id="archidekt:6862011"):
    return DecklistInput(
        deck_id=deck_id,
        commanders=[CommanderIdentifier(name="Atraxa, Praetors' Voice")],
        cards=[DeckCardEntry(name="Sol Ring")],
    )


_INGESTED_DECK_PAYLOAD = {
    "status": {"exists": True, "invalid": False},
    "id": "00191862135fdb062a630f2eeaeeee27",
    "saltRating": 197.63229456156887,
    "powerLevelRating": 10.909,
    "bracketRating": 5.99,
    "synergyRating": 1142,
    "threatRating": 664.6,
    "archetypeLabel": "Combo / Control",
    "manuel": {
        "category": "cEDH",
        "listOfResponses": ["This deck is extremely fast and consistent."],
    },
    "cards": {
        "rhystic_study": {"name": "Rhystic Study", "salt": "3.0"},
        "cyclonic_rift": {"name": "Cyclonic Rift", "salt": "2.45"},
        "sol_ring": {"name": "Sol Ring", "salt": "0"},
        "basic_land": {"name": "Island"},
    },
    "details": {
        "powerLevel": {
            "scoring": {
                "stax": {"score": 98},
                "ramp": {"score": 107, "extra": "ignored"},
                "malformed": "not-a-dict",
            }
        },
        "combos": {
            "score": 164,
            "list": [
                {
                    "id": "combo_a",
                    "cards": ["card_a", "card_b"],
                    "type": "wincon",
                    "categories": ["win-game"],
                    "score": 25,
                    "spellbookUri": "https://commanderspellbook.com/combo/1-2/",
                }
            ],
            "profile": {
                "count": 7,
                "winconCount": 7,
                "effectiveLines": 2,
                "redundancy": "redundant",
                "headline": {"summary": "This deck runs 7 combos."},
            },
        },
    },
}

_NOT_INGESTED_PAYLOAD = {
    "status": {"exists": False, "invalid": True},
    "id": "https://archidekt.com/decks/99999999999",
    "cards": [],
}


class TestResolveSourceUrl(unittest.TestCase):
    def test_full_http_url_passed_through(self):
        self.assertEqual(
            resolve_source_url("https://archidekt.com/decks/6862011"),
            "https://archidekt.com/decks/6862011",
        )

    def test_full_https_url_passed_through(self):
        self.assertEqual(
            resolve_source_url("http://archidekt.com/decks/6862011"),
            "http://archidekt.com/decks/6862011",
        )

    def test_archidekt_prefixed_reference_expanded(self):
        self.assertEqual(
            resolve_source_url("archidekt:6862011"),
            "https://archidekt.com/decks/6862011",
        )

    def test_moxfield_prefixed_reference_expanded(self):
        self.assertEqual(
            resolve_source_url("moxfield:Z-Gu4WQtVEaZxbpclY4aJQ"),
            "https://moxfield.com/decks/Z-Gu4WQtVEaZxbpclY4aJQ",
        )

    def test_raw_commandersalt_id_passed_through(self):
        raw_id = "00191862135fdb062a630f2eeaeeee27"
        self.assertEqual(resolve_source_url(raw_id), raw_id)

    def test_unsupported_provider_prefix_raises(self):
        with self.assertRaises(CommanderSaltRequestError):
            resolve_source_url("tappedout:some-deck")

    def test_missing_deck_id_raises(self):
        with self.assertRaises(CommanderSaltRequestError):
            resolve_source_url(None)

    def test_empty_deck_id_raises(self):
        with self.assertRaises(CommanderSaltRequestError):
            resolve_source_url("   ")

    def test_malformed_reference_raises(self):
        with self.assertRaises(CommanderSaltRequestError) as ctx:
            resolve_source_url("archidekt:")
        self.assertIn("missing its reference value", str(ctx.exception))


class TestCommanderSaltClient(unittest.TestCase):
    def _make_client(self, session):
        return CommanderSaltClient(
            session=session,
            max_retries=3,
            min_request_interval=0.0,
            sleep_fn=lambda _: None,
        )

    def test_successful_get_returns_json(self):
        session = MagicMock()
        session.get.return_value = _make_response(200, _INGESTED_DECK_PAYLOAD)
        client = self._make_client(session)

        result = client.get_deck("https://archidekt.com/decks/6862011")

        self.assertEqual(result, _INGESTED_DECK_PAYLOAD)
        session.get.assert_called_once()
        call_kwargs = session.get.call_args.kwargs
        self.assertEqual(call_kwargs["params"], {"id": "https://archidekt.com/decks/6862011"})

    def test_not_found_raises_not_found_error(self):
        session = MagicMock()
        session.get.return_value = _make_response(404)
        client = self._make_client(session)

        with self.assertRaises(CommanderSaltNotFoundError):
            client.get_deck("https://archidekt.com/decks/does-not-exist")

    def test_not_ingested_raises_not_ingested_error(self):
        session = MagicMock()
        session.get.return_value = _make_response(200, _NOT_INGESTED_PAYLOAD)
        client = self._make_client(session)

        with self.assertRaises(CommanderSaltNotIngestedError):
            client.get_deck("https://archidekt.com/decks/99999999999")

    def test_invalid_json_raises_response_error(self):
        session = MagicMock()
        session.get.return_value = _make_response(200, json_error=True)
        client = self._make_client(session)

        with self.assertRaises(CommanderSaltResponseError):
            client.get_deck("https://archidekt.com/decks/6862011")

    def test_non_dict_json_raises_response_error(self):
        session = MagicMock()
        session.get.return_value = _make_response(200, json_body=["not", "a", "dict"])
        client = self._make_client(session)

        with self.assertRaises(CommanderSaltResponseError):
            client.get_deck("https://archidekt.com/decks/6862011")

    def test_5xx_retries_then_succeeds(self):
        session = MagicMock()
        session.get.side_effect = [
            _make_response(503),
            _make_response(200, _INGESTED_DECK_PAYLOAD),
        ]
        client = self._make_client(session)

        result = client.get_deck("https://archidekt.com/decks/6862011")

        self.assertEqual(result, _INGESTED_DECK_PAYLOAD)
        self.assertEqual(session.get.call_count, 2)

    def test_5xx_exhausts_retries_raises_request_error(self):
        session = MagicMock()
        session.get.return_value = _make_response(503)
        client = self._make_client(session)

        with self.assertRaises(CommanderSaltRequestError):
            client.get_deck("https://archidekt.com/decks/6862011")
        self.assertEqual(session.get.call_count, 3)

    def test_unexpected_status_code_raises_immediately(self):
        session = MagicMock()
        session.get.return_value = _make_response(418)
        client = self._make_client(session)

        with self.assertRaises(CommanderSaltRequestError):
            client.get_deck("https://archidekt.com/decks/6862011")
        session.get.assert_called_once()

    def test_missing_status_object_raises_response_error(self):
        session = MagicMock()
        session.get.return_value = _make_response(200, json_body={})
        client = self._make_client(session)

        with self.assertRaises(CommanderSaltResponseError):
            client.get_deck("https://archidekt.com/decks/6862011")

    def test_status_exists_not_true_raises_response_error(self):
        session = MagicMock()
        session.get.return_value = _make_response(200, json_body={"status": {"exists": "maybe"}})
        client = self._make_client(session)

        with self.assertRaises(CommanderSaltResponseError):
            client.get_deck("https://archidekt.com/decks/6862011")

    def test_concurrent_throttling_serialized(self):
        import threading
        import time

        call_times = []
        lock = threading.Lock()
        session = MagicMock()

        def fake_get(*args, **kwargs):
            with lock:
                call_times.append(time.monotonic())
            return _make_response(200, _INGESTED_DECK_PAYLOAD)

        session.get.side_effect = fake_get

        client = CommanderSaltClient(
            session=session,
            min_request_interval=0.04,
            max_retries=1,
        )

        threads = [
            threading.Thread(
                target=client.get_deck,
                args=(f"https://archidekt.com/decks/{i}",),
            )
            for i in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(len(call_times), 4)
        # Check that consecutive calls are spaced out by at least ~0.035s
        for i in range(1, len(call_times)):
            diff = call_times[i] - call_times[i - 1]
            self.assertGreaterEqual(
                diff,
                0.03,
                f"Call {i} occurred {diff:.4f}s after previous, expected >= 0.03s",
            )


class TestCommanderSaltProvider(unittest.TestCase):
    def _make_provider(self, payload=_INGESTED_DECK_PAYLOAD):
        session = MagicMock()
        session.get.return_value = _make_response(200, payload)
        client = CommanderSaltClient(
            session=session, min_request_interval=0.0, sleep_fn=lambda _: None
        )
        return CommanderSaltProvider(client=client), session

    def test_name(self):
        provider, _ = self._make_provider()
        self.assertEqual(provider.name, "commandersalt")

    def test_supported_queries_only_meta_scores(self):
        provider, _ = self._make_provider()
        self.assertTrue(provider.supports_query("meta_scores"))
        self.assertFalse(provider.supports_query("recommendations"))
        self.assertFalse(provider.supports_query("synergy"))
        self.assertFalse(provider.supports_query("popularity"))

    def test_get_meta_scores_salt(self):
        provider, _ = self._make_provider()
        meta = provider.get_meta_scores(_make_deck())

        self.assertIsNotNone(meta.salt)
        self.assertAlmostEqual(meta.salt.score, 197.63229456156887)
        self.assertAlmostEqual(meta.salt.salt_sum, 5.45)  # 3.0 + 2.45 + 0
        self.assertEqual(len(meta.salt.high_salt_cards), 2)  # zero/absent salt excluded
        self.assertEqual(meta.salt.high_salt_cards[0].card_name, "Rhystic Study")
        self.assertEqual(meta.salt.high_salt_cards[0].rank, 1)
        self.assertEqual(meta.salt.high_salt_cards[1].card_name, "Cyclonic Rift")
        self.assertEqual(meta.salt.high_salt_cards[1].rank, 2)

    def test_get_meta_scores_power(self):
        provider, _ = self._make_provider()
        meta = provider.get_meta_scores(_make_deck())

        self.assertIsNotNone(meta.power)
        self.assertAlmostEqual(meta.power.score, 10.0)  # clamped to <= 10.0
        self.assertEqual(meta.power.tier, "cEDH")
        self.assertEqual(meta.power.description, "This deck is extremely fast and consistent.")
        self.assertEqual(meta.power.breakdown.get("stax"), 98)
        self.assertEqual(meta.power.breakdown.get("ramp"), 107)
        self.assertNotIn("malformed", meta.power.breakdown)

    def test_get_meta_scores_provider_metrics_combos(self):
        provider, _ = self._make_provider()
        meta = provider.get_meta_scores(_make_deck())

        combos = meta.provider_metrics["combos"]
        self.assertEqual(combos["count"], 7)
        self.assertEqual(combos["wincon_count"], 7)
        self.assertEqual(combos["effective_lines"], 2)
        self.assertEqual(combos["redundancy"], "redundant")
        self.assertEqual(combos["summary"], "This deck runs 7 combos.")
        self.assertEqual(len(combos["combos"]), 1)
        self.assertEqual(combos["combos"][0]["id"], "combo_a")
        self.assertEqual(meta.provider_metrics["bracket_rating"], 5.99)
        self.assertEqual(meta.provider_metrics["archetype_label"], "Combo / Control")

    def test_get_meta_scores_caches_raw_payload(self):
        provider, session = self._make_provider()
        deck = _make_deck()

        provider.get_meta_scores(deck)
        provider.get_meta_scores(deck)

        session.get.assert_called_once()

    def test_get_recommendations_unsupported(self):
        provider, _ = self._make_provider()
        with self.assertRaises(UnsupportedAnalyticsQueryError):
            provider.get_recommendations(_make_deck())

    def test_get_synergy_unsupported(self):
        provider, _ = self._make_provider()
        with self.assertRaises(UnsupportedAnalyticsQueryError):
            provider.get_synergy(_make_deck())

    def test_get_popularity_unsupported(self):
        provider, _ = self._make_provider()
        with self.assertRaises(UnsupportedAnalyticsQueryError):
            provider.get_popularity(_make_deck())

    def test_analyze_deck_only_populates_meta_scores(self):
        provider, _ = self._make_provider()
        result = provider.analyze_deck(_make_deck())

        self.assertEqual(result.provider_name, "commandersalt")
        self.assertIsNotNone(result.meta_scores)
        self.assertIsNone(result.recommendations)
        self.assertIsNone(result.synergy)
        self.assertIsNone(result.popularity)

    def test_not_ingested_deck_raises(self):
        provider, _ = self._make_provider(payload=_NOT_INGESTED_PAYLOAD)
        with self.assertRaises(CommanderSaltNotIngestedError):
            provider.get_meta_scores(_make_deck())

    def test_missing_salt_rating_yields_none_salt(self):
        payload = dict(_INGESTED_DECK_PAYLOAD)
        payload = {**payload, "saltRating": None}
        provider, _ = self._make_provider(payload=payload)
        meta = provider.get_meta_scores(_make_deck())
        self.assertIsNone(meta.salt)

    def test_missing_power_level_rating_yields_none_power(self):
        payload = dict(_INGESTED_DECK_PAYLOAD)
        payload = {**payload, "powerLevelRating": None}
        provider, _ = self._make_provider(payload=payload)
        meta = provider.get_meta_scores(_make_deck())
        self.assertIsNone(meta.power)

    def test_non_numeric_power_level_rating_yields_none_power(self):
        payload = dict(_INGESTED_DECK_PAYLOAD)
        payload = {**payload, "powerLevelRating": "not-a-number"}
        provider, _ = self._make_provider(payload=payload)
        meta = provider.get_meta_scores(_make_deck())
        self.assertIsNone(meta.power)

    def test_cache_ttl_expiration(self):
        current_time = [1000.0]
        session = MagicMock()
        session.get.return_value = _make_response(200, _INGESTED_DECK_PAYLOAD)
        client = CommanderSaltClient(
            session=session, min_request_interval=0.0, sleep_fn=lambda _: None
        )
        provider = CommanderSaltProvider(
            client=client,
            cache_ttl_seconds=300.0,
            time_fn=lambda: current_time[0],
        )
        deck = _make_deck()
        provider.get_meta_scores(deck)
        self.assertEqual(session.get.call_count, 1)

        # Within TTL: cached
        current_time[0] += 100.0
        provider.get_meta_scores(deck)
        self.assertEqual(session.get.call_count, 1)

        # Past TTL: re-fetched
        current_time[0] += 201.0
        provider.get_meta_scores(deck)
        self.assertEqual(session.get.call_count, 2)

    def test_cache_max_entries_eviction(self):
        session = MagicMock()
        session.get.return_value = _make_response(200, _INGESTED_DECK_PAYLOAD)
        client = CommanderSaltClient(
            session=session, min_request_interval=0.0, sleep_fn=lambda _: None
        )
        provider = CommanderSaltProvider(client=client)
        provider._CACHE_MAX_ENTRIES = 2

        provider.get_meta_scores(_make_deck("archidekt:1"))
        provider.get_meta_scores(_make_deck("archidekt:2"))
        self.assertEqual(len(provider._cache), 2)

        # 3rd deck evicts oldest
        provider.get_meta_scores(_make_deck("archidekt:3"))
        self.assertEqual(len(provider._cache), 2)
        self.assertNotIn("https://archidekt.com/decks/1", provider._cache)
        self.assertIn("https://archidekt.com/decks/3", provider._cache)

    def test_deck_without_deck_id_raises_request_error(self):
        provider, _ = self._make_provider()
        deck = DecklistInput(
            commanders=[CommanderIdentifier(name="Atraxa, Praetors' Voice")],
            cards=[DeckCardEntry(name="Sol Ring")],
        )
        with self.assertRaises(CommanderSaltRequestError):
            provider.get_meta_scores(deck)


class TestProviderRegistration(unittest.TestCase):
    def test_registered_under_commandersalt_name(self):
        import importlib
        from analytics.registry import default_registry
        import analytics.providers.commandersalt as commandersalt_module

        # Verify import-time registration via @register_provider decorator
        default_registry.unregister("commandersalt")
        self.assertNotIn("commandersalt", default_registry.list_providers())

        importlib.reload(commandersalt_module)
        # Keep module-level aliases in sync with reloaded classes
        globals()["CommanderSaltRequestError"] = commandersalt_module.CommanderSaltRequestError
        globals()["CommanderSaltNotFoundError"] = commandersalt_module.CommanderSaltNotFoundError
        globals()["CommanderSaltNotIngestedError"] = commandersalt_module.CommanderSaltNotIngestedError
        globals()["CommanderSaltResponseError"] = commandersalt_module.CommanderSaltResponseError
        globals()["CommanderSaltClient"] = commandersalt_module.CommanderSaltClient
        globals()["CommanderSaltProvider"] = commandersalt_module.CommanderSaltProvider
        globals()["resolve_source_url"] = commandersalt_module.resolve_source_url

        self.assertIn("commandersalt", default_registry.list_providers())
        provider = default_registry.get("commandersalt")
        self.assertIsInstance(provider, commandersalt_module.CommanderSaltProvider)


if __name__ == "__main__":
    unittest.main()

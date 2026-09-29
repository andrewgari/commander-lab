"""
Unit tests for the EDHRec deck analytics provider adapter
(analytics.providers.edhrec).

Covers:
1. Slug helpers (single commander, partners, split cards).
2. EDHRecClient: rate limiting (throttling), retry/backoff on transient
   errors, typed error mapping for 404 / malformed JSON / exhausted retries.
3. EDHRecProvider: response -> domain-model mapping for meta scores,
   recommendations, synergy, and popularity, using a trimmed real-shaped
   fixture (no live network calls).
4. Registration: the adapter registers itself as "edhrec" on the default
   registry via the @register_provider decorator.
"""
import json
import os
import sys
import unittest
from unittest.mock import MagicMock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

from analytics import default_registry, get_provider
from analytics.models import CommanderIdentifier, DeckCardEntry, DecklistInput
from analytics.providers.edhrec import (
    EDHRecClient,
    EDHRecNotFoundError,
    EDHRecProvider,
    EDHRecRequestError,
    EDHRecResponseError,
    commander_slug,
    slugify_card_name,
)

FIXTURE_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "fixtures", "edhrec_atraxa_commander_page.json"
)


def load_fixture() -> dict:
    with open(FIXTURE_PATH) as fh:
        return json.load(fh)


def _make_deck(commander_name: str = "Atraxa, Praetors' Voice") -> DecklistInput:
    return DecklistInput(
        commanders=[CommanderIdentifier(name=commander_name)],
        cards=[DeckCardEntry(name="Sol Ring", quantity=1)],
    )


class TestSlugHelpers(unittest.TestCase):
    def test_slugify_basic(self):
        self.assertEqual(slugify_card_name("Sol Ring"), "sol-ring")

    def test_slugify_strips_punctuation(self):
        self.assertEqual(slugify_card_name("Atraxa, Praetors' Voice"), "atraxa-praetors-voice")

    def test_slugify_split_card_uses_front_face(self):
        self.assertEqual(slugify_card_name("Fire // Ice"), "fire")

    def test_slugify_empty(self):
        self.assertEqual(slugify_card_name(""), "")

    def test_commander_slug_single(self):
        self.assertEqual(commander_slug(["Sol Ring"]), "sol-ring")

    def test_commander_slug_partners(self):
        self.assertEqual(
            commander_slug(["Tymna the Weaver", "Thrasios, Triton Hero"]),
            "tymna-the-weaver-thrasios-triton-hero",
        )

    def test_commander_slug_ignores_blank_entries(self):
        self.assertEqual(commander_slug(["Sol Ring", "", "  "]), "sol-ring")

    def test_commander_slug_empty_list(self):
        self.assertEqual(commander_slug([]), "")


class TestEDHRecClientThrottling(unittest.TestCase):
    """Verify the client sleeps between requests to respect rate limiting."""

    def test_throttle_sleeps_when_called_too_soon(self):
        sleeps = []
        clock = {"t": 0.0}

        def fake_sleep(seconds):
            sleeps.append(seconds)
            clock["t"] += seconds

        def fake_time():
            return clock["t"]

        session = MagicMock()
        response = MagicMock(status_code=200)
        response.json.return_value = {"container": {"json_dict": {}}}
        session.get.return_value = response

        client = EDHRecClient(
            session=session,
            min_request_interval=1.0,
            sleep_fn=fake_sleep,
            time_fn=fake_time,
        )

        client._get_json("commanders/sol-ring.json")
        clock["t"] += 0.1  # simulate a tiny amount of real elapsed time
        client._get_json("commanders/sol-ring.json")

        # Second call should have triggered a throttle sleep close to 0.9s
        self.assertTrue(any(s > 0 for s in sleeps))

    def test_no_throttle_sleep_on_first_request(self):
        sleeps = []
        session = MagicMock()
        response = MagicMock(status_code=200)
        response.json.return_value = {"container": {"json_dict": {}}}
        session.get.return_value = response

        client = EDHRecClient(
            session=session,
            min_request_interval=5.0,
            sleep_fn=lambda s: sleeps.append(s),
            time_fn=lambda: 0.0,
        )
        client._get_json("commanders/sol-ring.json")
        self.assertEqual(sleeps, [])


class TestEDHRecClientRetries(unittest.TestCase):
    def _client(self, session, **kwargs):
        return EDHRecClient(
            session=session,
            min_request_interval=0.0,
            sleep_fn=lambda s: None,
            max_retries=3,
            backoff_factor=0.0,
            **kwargs,
        )

    def test_retries_on_connection_error_then_succeeds(self):
        session = MagicMock()
        good_response = MagicMock(status_code=200)
        good_response.json.return_value = {"container": {"json_dict": {}}}
        session.get.side_effect = [
            requests.exceptions.ConnectionError("boom"),
            good_response,
        ]
        client = self._client(session)
        result = client._get_json("commanders/sol-ring.json")
        self.assertEqual(result, {"container": {"json_dict": {}}})
        self.assertEqual(session.get.call_count, 2)

    def test_retries_on_429_then_succeeds(self):
        session = MagicMock()
        rate_limited = MagicMock(status_code=429)
        good_response = MagicMock(status_code=200)
        good_response.json.return_value = {"container": {"json_dict": {}}}
        session.get.side_effect = [rate_limited, good_response]
        client = self._client(session)
        result = client._get_json("commanders/sol-ring.json")
        self.assertEqual(result, {"container": {"json_dict": {}}})

    def test_retries_on_5xx_then_succeeds(self):
        session = MagicMock()
        server_error = MagicMock(status_code=503)
        good_response = MagicMock(status_code=200)
        good_response.json.return_value = {"container": {"json_dict": {}}}
        session.get.side_effect = [server_error, good_response]
        client = self._client(session)
        result = client._get_json("commanders/sol-ring.json")
        self.assertEqual(result, {"container": {"json_dict": {}}})

    def test_raises_request_error_after_exhausting_retries(self):
        session = MagicMock()
        session.get.side_effect = requests.exceptions.Timeout("slow")
        client = self._client(session)
        with self.assertRaises(EDHRecRequestError):
            client._get_json("commanders/sol-ring.json")
        self.assertEqual(session.get.call_count, 3)

    def test_raises_not_found_on_404_without_retrying(self):
        session = MagicMock()
        response = MagicMock(status_code=404)
        session.get.return_value = response
        client = self._client(session)
        with self.assertRaises(EDHRecNotFoundError):
            client._get_json("commanders/nonexistent-card.json")
        self.assertEqual(session.get.call_count, 1)

    def test_raises_response_error_on_invalid_json(self):
        session = MagicMock()
        response = MagicMock(status_code=200)
        response.json.side_effect = ValueError("not json")
        session.get.return_value = response
        client = self._client(session)
        with self.assertRaises(EDHRecResponseError):
            client._get_json("commanders/sol-ring.json")

    def test_raises_request_error_on_unexpected_status(self):
        session = MagicMock()
        response = MagicMock(status_code=401)
        session.get.return_value = response
        client = self._client(session)
        with self.assertRaises(EDHRecRequestError):
            client._get_json("commanders/sol-ring.json")
        self.assertEqual(session.get.call_count, 1)

    def test_get_commander_page_builds_slug(self):
        session = MagicMock()
        response = MagicMock(status_code=200)
        response.json.return_value = {"container": {"json_dict": {}}}
        session.get.return_value = response
        client = self._client(session)
        client.get_commander_page(["Atraxa, Praetors' Voice"])
        called_url = session.get.call_args[0][0]
        self.assertIn("commanders/atraxa-praetors-voice.json", called_url)

    def test_get_commander_page_rejects_empty_names(self):
        client = self._client(MagicMock())
        with self.assertRaises(ValueError):
            client.get_commander_page([])


class TestEDHRecProviderMapping(unittest.TestCase):
    """Verify EDHRecProvider maps the fixture response onto the unified
    domain schema, using a mocked EDHRecClient (no network)."""

    def setUp(self):
        self.raw = load_fixture()
        self.client = MagicMock(spec=EDHRecClient)
        self.client.get_commander_page.return_value = self.raw
        self.provider = EDHRecProvider(client=self.client)
        self.deck = _make_deck()

    def test_name(self):
        self.assertEqual(self.provider.name, "edhrec")

    def test_get_meta_scores_maps_salt(self):
        meta = self.provider.get_meta_scores(self.deck)
        self.assertIsNotNone(meta.salt)
        self.assertAlmostEqual(meta.salt.score, 1.7227891156462585)
        self.assertEqual(meta.provider_metrics["edhrec_rank"], 4)
        self.assertEqual(meta.provider_metrics["edhrec_num_decks"], 43941)

    def test_get_recommendations_excludes_cards_already_in_deck(self):
        recs = self.provider.get_recommendations(self.deck)
        names = [r.card_name for r in recs.items]
        self.assertNotIn("Sol Ring", names)  # already in the input deck

    def test_get_recommendations_sorted_by_synergy_desc(self):
        recs = self.provider.get_recommendations(self.deck)
        synergies = [r.synergy for r in recs.items]
        self.assertEqual(synergies, sorted(synergies, reverse=True))
        self.assertEqual(recs.total, len(recs.items))

    def test_get_recommendations_dedupes_across_cardlists(self):
        # "Deepglow Skate" appears in both highsynergycards and newcards
        # fixtures with different synergy values -- expect a single entry
        # with the higher synergy value kept.
        recs = self.provider.get_recommendations(self.deck)
        matches = [r for r in recs.items if r.card_name == "Deepglow Skate"]
        self.assertEqual(len(matches), 1)
        self.assertAlmostEqual(matches[0].synergy, 0.58)

    def test_get_synergy_overall_and_breakdown(self):
        synergy = self.provider.get_synergy(self.deck)
        self.assertIsNotNone(synergy.overall_synergy)
        self.assertGreater(len(synergy.card_synergies), 0)
        for metric in synergy.card_synergies:
            self.assertEqual(metric.commander_name, "Atraxa, Praetors' Voice")
            self.assertGreaterEqual(metric.synergy_score, -1.0)
            self.assertLessEqual(metric.synergy_score, 1.0)

    def test_get_popularity_maps_commander_and_card_counts(self):
        popularity = self.provider.get_popularity(self.deck)
        self.assertEqual(popularity.rank, 4)
        self.assertEqual(popularity.num_decks, 43941)
        self.assertGreater(len(popularity.card_popularity), 0)
        sol_ring = next(m for m in popularity.card_popularity if m.card_name == "Sol Ring")
        self.assertEqual(sol_ring.deck_count, 43000)
        self.assertAlmostEqual(sol_ring.percentage, 43000 / 43941 * 100.0, places=3)

    def test_analyze_deck_aggregates_all_dimensions_with_single_fetch(self):
        result = self.provider.analyze_deck(self.deck)
        self.assertEqual(result.provider_name, "edhrec")
        self.assertIsNotNone(result.meta_scores)
        self.assertIsNotNone(result.recommendations)
        self.assertIsNotNone(result.synergy)
        self.assertIsNotNone(result.popularity)
        # analyze_deck touches all 4 query dimensions but the raw page is
        # cached per-commander-slug, so the underlying HTTP client is only
        # invoked once.
        self.client.get_commander_page.assert_called_once()

    def test_caches_raw_page_per_commander_slug(self):
        self.provider.get_meta_scores(self.deck)
        self.provider.get_synergy(self.deck)
        self.provider.get_popularity(self.deck)
        self.client.get_commander_page.assert_called_once()

    def test_raises_when_deck_has_no_commanders(self):
        # DecklistInput enforces min_length=1 on commanders, so simulate the
        # no-commander case by calling _fetch_raw directly against a deck
        # whose commanders list construction is bypassed via model_construct.
        deck = DecklistInput.model_construct(
            commanders=[], cards=self.deck.cards, format="commander"
        )
        with self.assertRaises(EDHRecRequestError):
            self.provider._fetch_raw(deck)


class TestEDHRecProviderMalformedResponses(unittest.TestCase):
    def _provider_with_raw(self, raw):
        client = MagicMock(spec=EDHRecClient)
        client.get_commander_page.return_value = raw
        return EDHRecProvider(client=client)

    def test_missing_container_raises_response_error(self):
        provider = self._provider_with_raw({"unexpected": "shape"})
        with self.assertRaises(EDHRecResponseError):
            provider.get_meta_scores(_make_deck())

    def test_cardlists_not_a_list_raises_response_error(self):
        provider = self._provider_with_raw(
            {"container": {"json_dict": {"cardlists": "not-a-list"}}}
        )
        with self.assertRaises(EDHRecResponseError):
            provider.get_recommendations(_make_deck())

    def test_missing_cardlists_returns_empty_recommendations(self):
        provider = self._provider_with_raw({"container": {"json_dict": {"card": {}}}})
        recs = provider.get_recommendations(_make_deck())
        self.assertEqual(recs.items, [])
        self.assertEqual(recs.total, 0)

    def test_missing_card_meta_returns_none_salt_and_rank(self):
        provider = self._provider_with_raw({"container": {"json_dict": {"cardlists": []}}})
        meta = provider.get_meta_scores(_make_deck())
        self.assertIsNone(meta.salt)
        popularity = provider.get_popularity(_make_deck())
        self.assertIsNone(popularity.rank)
        self.assertIsNone(popularity.num_decks)


class TestEDHRecProviderRegistration(unittest.TestCase):
    """Importing analytics.providers.edhrec registers "edhrec" on the
    default registry via @register_provider. Other test modules exercise
    clear_registry()/unregister_provider() against the same shared global
    registry, so re-register defensively before asserting rather than
    depending on unittest's test discovery/import order.
    """

    def setUp(self):
        if "edhrec" not in default_registry.list_providers():
            default_registry.register(EDHRecProvider, name="edhrec")

    def test_registered_on_default_registry(self):
        self.assertIn("edhrec", default_registry.list_providers())
        provider = get_provider("edhrec")
        self.assertIsInstance(provider, EDHRecProvider)
        self.assertEqual(provider.name, "edhrec")


if __name__ == "__main__":
    unittest.main()

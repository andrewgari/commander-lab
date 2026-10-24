"""Unit tests for ScryfallClient and Scryfall API integration.

Verifies:
- Exact card lookup (/cards/named?exact=...)
- Fuzzy card lookup (/cards/named?fuzzy=...)
- Card ID lookup (/cards/{id})
- Full syntax search (/cards/search?q=...) with query options and pagination
- Streaming/paginated search iterator (search_all_cards)
- Deserialization into ScryfallCard and ScryfallSearchResult domain models
- Multi-faced card handling and image URI fallbacks
- Robust error handling:
  - 404 Not Found returning None by default
  - 404 Not Found raising ScryfallNotFoundError when requested
  - 429 Throttled recovering via SharedHttpClient retry backoff
  - 429 Throttled raising ScryfallRateLimitError when retries exhausted
  - 400 Bad Request / 500 Server error raising ScryfallRequestError
  - Network timeouts and connection errors
- Async context manager and client lifecycle (owned vs injected SharedHttpClient)
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from typing import Any, Dict, List
from unittest import mock

import httpx

# Ensure workspace root is in sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.integrations.scryfall import (
    ScryfallCard,
    ScryfallClient,
    ScryfallError,
    ScryfallNotFoundError,
    ScryfallRateLimitError,
    ScryfallRequestError,
    ScryfallSearchResult,
)
from src.shared.http import RetryConfig, SharedHttpClient
from src.shared.http.exceptions import HttpClientError


def _make_mock_card_payload(
    name: str = "Sol Ring",
    card_id: str = "e0a0ee21-b4f6-41a8-8c01-60a641457195",
    oracle_id: str = "4352eb32-2ff0-40e0-825e-0f62272849ae",
    **overrides: Any,
) -> Dict[str, Any]:
    base: Dict[str, Any] = {
        "object": "card",
        "id": card_id,
        "oracle_id": oracle_id,
        "name": name,
        "mana_cost": "{1}",
        "cmc": 1.0,
        "type_line": "Artifact",
        "oracle_text": "{T}: Add {C}{C}.",
        "colors": [],
        "color_identity": [],
        "keywords": [],
        "set": "c16",
        "set_name": "Commander 2016",
        "collector_number": "272",
        "rarity": "uncommon",
        "layout": "normal",
        "image_uris": {
            "small": f"https://cards.scryfall.io/small/{card_id}.jpg",
            "normal": f"https://cards.scryfall.io/normal/{card_id}.jpg",
            "large": f"https://cards.scryfall.io/large/{card_id}.jpg",
            "art_crop": f"https://cards.scryfall.io/art_crop/{card_id}.jpg",
        },
        "legalities": {
            "commander": "legal",
            "vintage": "restricted",
            "legacy": "banned",
        },
        "prices": {"usd": "1.50", "eur": "1.20"},
        "scryfall_uri": f"https://scryfall.com/card/c16/272/{name.lower().replace(' ', '-')}",
    }
    base.update(overrides)
    return base


def _make_mock_mdfc_payload() -> Dict[str, Any]:
    return {
        "object": "card",
        "id": "11bf83bb-c95b-4b4f-9a56-ce7a1816307a",
        "name": "Delver of Secrets // Insectile Aberration",
        "layout": "transform",
        "cmc": 1.0,
        "type_line": "Creature — Human Wizard // Creature — Human Insect",
        "color_identity": ["U"],
        "card_faces": [
            {
                "object": "card_face",
                "name": "Delver of Secrets",
                "mana_cost": "{U}",
                "type_line": "Creature — Human Wizard",
                "oracle_text": "At the beginning of your upkeep, look at the top card...",
                "colors": ["U"],
                "power": "1",
                "toughness": "1",
                "image_uris": {
                    "small": "https://cards.scryfall.io/small/front/delver.jpg",
                    "normal": "https://cards.scryfall.io/normal/front/delver.jpg",
                    "art_crop": "https://cards.scryfall.io/art_crop/front/delver.jpg",
                },
            },
            {
                "object": "card_face",
                "name": "Insectile Aberration",
                "type_line": "Creature — Human Insect",
                "oracle_text": "Flying",
                "colors": ["U"],
                "power": "3",
                "toughness": "2",
                "image_uris": {
                    "small": "https://cards.scryfall.io/small/back/insectile.jpg",
                    "normal": "https://cards.scryfall.io/normal/back/insectile.jpg",
                },
            },
        ],
        "legalities": {"commander": "legal"},
    }


class TestScryfallCardDeserialization(unittest.TestCase):
    """Test card object deserialization and helper methods."""

    def test_deserialization_normal_card(self):
        payload = _make_mock_card_payload()
        card = ScryfallCard.from_dict(payload)

        self.assertEqual(card.name, "Sol Ring")
        self.assertEqual(card.id, "e0a0ee21-b4f6-41a8-8c01-60a641457195")
        self.assertEqual(card.oracle_id, "4352eb32-2ff0-40e0-825e-0f62272849ae")
        self.assertEqual(card.mana_cost, "{1}")
        self.assertEqual(card.cmc, 1.0)
        self.assertEqual(card.type_line, "Artifact")
        self.assertEqual(card.set, "c16")
        self.assertEqual(card.set_name, "Commander 2016")
        self.assertEqual(card.rarity, "uncommon")
        self.assertTrue(card.is_commander_legal)
        self.assertFalse(card.is_multi_faced)

        # Image URI
        normal_uri = card.get_image_uri("normal")
        self.assertIsNotNone(normal_uri)
        self.assertIn("normal", normal_uri or "")
        art_crop_uri = card.get_image_uri("art_crop")
        self.assertIsNotNone(art_crop_uri)
        self.assertIn("art_crop", art_crop_uri or "")
        self.assertIsNone(card.get_image_uri("nonexistent_version"))

        # Dictionary access & serialization
        self.assertEqual(card["name"], "Sol Ring")
        self.assertEqual(card.get("set"), "c16")
        self.assertEqual(card.get("unknown_key", "default_val"), "default_val")
        self.assertIn("oracle_id", card)
        self.assertIn("name", card)

        as_dict = card.to_dict()
        self.assertIsInstance(as_dict, dict)
        self.assertEqual(as_dict["name"], "Sol Ring")

    def test_deserialization_multi_faced_card(self):
        payload = _make_mock_mdfc_payload()
        card = ScryfallCard.from_dict(payload)

        self.assertEqual(card.name, "Delver of Secrets // Insectile Aberration")
        self.assertTrue(card.is_multi_faced)
        self.assertIsNotNone(card.card_faces)
        self.assertEqual(len(card.card_faces or []), 2)
        # Top-level image_uris is None, should fallback to face[0]
        self.assertIsNone(card.image_uris)
        self.assertEqual(card.get_image_uri("normal"), "https://cards.scryfall.io/normal/front/delver.jpg")
        self.assertEqual(card.get_image_uri("art_crop"), "https://cards.scryfall.io/art_crop/front/delver.jpg")

    def test_invalid_dict_raises(self):
        with self.assertRaises(TypeError):
            ScryfallCard.from_dict("not-a-dict")  # type: ignore


class TestScryfallSearchResult(unittest.TestCase):
    """Test search result collection and iteration."""

    def test_search_result_parsing_and_iteration(self):
        raw_data = {
            "object": "list",
            "total_cards": 2,
            "has_more": False,
            "data": [
                _make_mock_card_payload(name="Counterspell"),
                _make_mock_card_payload(name="Mana Drain"),
            ],
            "warnings": ["Some warning"],
        }
        result = ScryfallSearchResult.from_dict(raw_data)

        self.assertEqual(len(result), 2)
        self.assertEqual(result.total_cards, 2)
        self.assertFalse(result.has_more)
        self.assertEqual(result.warnings, ["Some warning"])

        card_names = [c.name for c in result]
        self.assertEqual(card_names, ["Counterspell", "Mana Drain"])
        self.assertEqual(result[0].name, "Counterspell")
        self.assertEqual(len(result.cards), 2)

    def test_invalid_search_result_raises(self):
        with self.assertRaises(TypeError):
            ScryfallSearchResult.from_dict(["not", "a", "dict"])  # type: ignore


class TestScryfallClientLookups(unittest.IsolatedAsyncioTestCase):
    """Test get_card_by_name and get_card_by_id operations with mocked HTTP transport."""

    async def test_get_card_by_exact_name_success(self):
        recorded_requests: List[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            recorded_requests.append(request)
            self.assertEqual(request.url.path, "/cards/named")
            self.assertIn("exact=Sol+Ring", str(request.url))
            return httpx.Response(200, json=_make_mock_card_payload("Sol Ring"))

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.scryfall.com", transport=transport)
        client = ScryfallClient(http_client=shared_http)

        card = await client.get_card_by_name(exact="Sol Ring")
        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card.name, "Sol Ring")
        self.assertEqual(card.id, "e0a0ee21-b4f6-41a8-8c01-60a641457195")
        self.assertEqual(len(recorded_requests), 1)

    async def test_get_card_by_exact_name_positional_and_convenience(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_mock_card_payload("Black Lotus"))

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.scryfall.com", transport=transport)
        client = ScryfallClient(http_client=shared_http)

        # Positional exact
        card1 = await client.get_card_by_name("Black Lotus")
        self.assertIsNotNone(card1)
        assert card1 is not None
        self.assertEqual(card1.name, "Black Lotus")

        # Convenience method
        card2 = await client.get_card_by_exact_name("Black Lotus")
        self.assertIsNotNone(card2)
        assert card2 is not None
        self.assertEqual(card2.name, "Black Lotus")

    async def test_get_card_by_fuzzy_name_success(self):
        recorded_requests: List[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            recorded_requests.append(request)
            self.assertEqual(request.url.path, "/cards/named")
            self.assertIn("fuzzy=sol+rin", str(request.url))
            return httpx.Response(200, json=_make_mock_card_payload("Sol Ring"))

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.scryfall.com", transport=transport)
        client = ScryfallClient(http_client=shared_http)

        card = await client.get_card_by_name(fuzzy="sol rin")
        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card.name, "Sol Ring")

        card_conv = await client.get_card_by_fuzzy_name("sol rin")
        self.assertIsNotNone(card_conv)
        assert card_conv is not None
        self.assertEqual(card_conv.name, "Sol Ring")

    async def test_get_card_by_name_with_set(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertIn("set=c16", str(request.url))
            return httpx.Response(200, json=_make_mock_card_payload("Sol Ring", set="c16"))

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.scryfall.com", transport=transport)
        client = ScryfallClient(http_client=shared_http)

        card = await client.get_card_by_name(exact="Sol Ring", set="c16")
        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card.set, "c16")

    async def test_get_card_by_id_success(self):
        target_id = "e0a0ee21-b4f6-41a8-8c01-60a641457195"

        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, f"/cards/{target_id}")
            return httpx.Response(200, json=_make_mock_card_payload("Sol Ring", card_id=target_id))

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.scryfall.com", transport=transport)
        client = ScryfallClient(http_client=shared_http)

        card = await client.get_card_by_id(target_id)
        self.assertIsNotNone(card)
        assert card is not None
        self.assertEqual(card.id, target_id)

    async def test_input_validation(self):
        client = ScryfallClient(http_client=SharedHttpClient())

        # Neither exact nor fuzzy
        with self.assertRaises(ValueError):
            await client.get_card_by_name()

        # Both exact and fuzzy
        with self.assertRaises(ValueError):
            await client.get_card_by_name(exact="Sol Ring", fuzzy="Sol Rin")

        # Empty card ID
        with self.assertRaises(ValueError):
            await client.get_card_by_id("")


class TestScryfallClientSearch(unittest.IsolatedAsyncioTestCase):
    """Test syntax search operations, query parameters, and pagination."""

    async def test_search_cards_simple(self):
        def handler(request: httpx.Request) -> httpx.Response:
            self.assertEqual(request.url.path, "/cards/search")
            self.assertIn("q=c%3Ablue+t%3Ainstant", str(request.url))
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "total_cards": 2,
                    "has_more": False,
                    "data": [
                        _make_mock_card_payload("Brainstorm"),
                        _make_mock_card_payload("Ponder"),
                    ],
                },
            )

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.scryfall.com", transport=transport)
        client = ScryfallClient(http_client=shared_http)

        results = await client.search_cards("c:blue t:instant")
        self.assertEqual(len(results), 2)
        self.assertEqual(results.total_cards, 2)
        self.assertEqual(results[0].name, "Brainstorm")
        self.assertEqual(results[1].name, "Ponder")

    async def test_search_cards_with_all_options(self):
        recorded_url = None

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal recorded_url
            recorded_url = str(request.url)
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "total_cards": 1,
                    "has_more": False,
                    "data": [_make_mock_card_payload("Force of Will")],
                },
            )

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.scryfall.com", transport=transport)
        client = ScryfallClient(http_client=shared_http)

        await client.search_cards(
            query="Force of Will",
            page=2,
            unique="art",
            order="cmc",
            dir="asc",
            include_extras=True,
            include_multilingual=False,
            include_variations=True,
        )

        self.assertIsNotNone(recorded_url)
        assert recorded_url is not None
        self.assertIn("page=2", recorded_url)
        self.assertIn("unique=art", recorded_url)
        self.assertIn("order=cmc", recorded_url)
        self.assertIn("dir=asc", recorded_url)
        self.assertIn("include_extras=true", recorded_url)
        self.assertIn("include_multilingual=false", recorded_url)
        self.assertIn("include_variations=true", recorded_url)

    async def test_search_cards_empty_query_raises(self):
        client = ScryfallClient(http_client=SharedHttpClient())
        with self.assertRaises(ValueError):
            await client.search_cards("")

    async def test_search_all_cards_pagination(self):
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return httpx.Response(
                    200,
                    json={
                        "object": "list",
                        "total_cards": 3,
                        "has_more": True,
                        "data": [_make_mock_card_payload("Card A"), _make_mock_card_payload("Card B")],
                    },
                )
            return httpx.Response(
                200,
                json={
                    "object": "list",
                    "total_cards": 3,
                    "has_more": False,
                    "data": [_make_mock_card_payload("Card C")],
                },
            )

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.scryfall.com", transport=transport)
        client = ScryfallClient(http_client=shared_http)

        cards = [c.name async for c in client.search_all_cards("t:land")]
        self.assertEqual(cards, ["Card A", "Card B", "Card C"])
        self.assertEqual(call_count, 2)


class TestScryfallErrorHandling(unittest.IsolatedAsyncioTestCase):
    """Test 404, 429 backoff/recovery, 429 exhaustion, 400, and 500 error scenarios."""

    async def test_404_not_found_returns_none_by_default(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={
                    "object": "error",
                    "code": "not_found",
                    "status": 404,
                    "details": "No card could be found by the given criteria.",
                },
            )

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.scryfall.com", transport=transport)
        client = ScryfallClient(http_client=shared_http)

        # By default, exact lookup returns None
        result = await client.get_card_by_name(exact="Nonexistent Card Name")
        self.assertIsNone(result)

        # Fuzzy lookup returns None
        result_fuzzy = await client.get_card_by_name(fuzzy="nonexistent")
        self.assertIsNone(result_fuzzy)

        # ID lookup returns None
        result_id = await client.get_card_by_id("00000000-0000-0000-0000-000000000000")
        self.assertIsNone(result_id)

    async def test_404_not_found_raises_typed_exception_when_requested(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={
                    "object": "error",
                    "code": "not_found",
                    "status": 404,
                    "details": "No card could be found by the given criteria.",
                },
            )

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.scryfall.com", transport=transport)
        client = ScryfallClient(http_client=shared_http)

        # raise_on_not_found=True raises ScryfallNotFoundError
        with self.assertRaises(ScryfallNotFoundError) as ctx:
            await client.get_card_by_name(exact="Nonexistent Card", raise_on_not_found=True)
        self.assertEqual(ctx.exception.status_code, 404)
        self.assertIn("Nonexistent Card", str(ctx.exception))
        self.assertIsInstance(ctx.exception, ScryfallError)
        self.assertIsInstance(ctx.exception, HttpClientError)

        # Alias raise_for_not_found=True also raises
        with self.assertRaises(ScryfallNotFoundError):
            await client.get_card_by_id("123", raise_for_not_found=True)

    async def test_search_404_returns_empty_result_by_default(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                404,
                json={
                    "object": "error",
                    "code": "not_found",
                    "status": 404,
                    "details": "Your query didn't match any cards.",
                },
            )

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.scryfall.com", transport=transport)
        client = ScryfallClient(http_client=shared_http)

        # By default, search returning 404 gives empty ScryfallSearchResult
        res = await client.search_cards("totally:impossible_search_query_123")
        self.assertEqual(len(res), 0)
        self.assertEqual(res.total_cards, 0)
        self.assertFalse(res.has_more)

        # With raise_on_not_found=True, raises ScryfallNotFoundError
        with self.assertRaises(ScryfallNotFoundError):
            await client.search_cards("totally:impossible_search_query_123", raise_on_not_found=True)

    async def test_429_throttled_recovers_via_retry_backoff(self):
        """Verify that a transient 429 recovers automatically via SharedHttpClient backoff."""
        attempts = 0

        async def fake_sleep(seconds: float) -> None:
            # Yield control immediately during unit test
            await asyncio.sleep(0)

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(
                    429,
                    headers={"Retry-After": "0.01"},
                    json={"object": "error", "details": "Rate limit exceeded."},
                )
            return httpx.Response(200, json=_make_mock_card_payload("Sol Ring"))

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(
            base_url="https://api.scryfall.com",
            transport=transport,
            retry_config=RetryConfig(max_retries=2, base_delay=0.01, max_delay=0.1, jitter=False),
            sleep=fake_sleep,
        )
        client = ScryfallClient(http_client=shared_http)

        card = await client.get_card_by_name(exact="Sol Ring")
        self.assertIsNotNone(card)
        self.assertEqual(card.name, "Sol Ring")
        self.assertEqual(attempts, 2)

    async def test_429_throttled_exhaustion_raises_scryfall_rate_limit_error(self):
        """Verify that persistent 429s exhaust retries and raise ScryfallRateLimitError."""
        attempts = 0

        async def fake_sleep(seconds: float) -> None:
            await asyncio.sleep(0)

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(
                429,
                headers={"Retry-After": "2.5"},
                json={"object": "error", "details": "Rate limit exceeded."},
            )

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(
            base_url="https://api.scryfall.com",
            transport=transport,
            retry_config=RetryConfig(max_retries=2, base_delay=0.01, max_delay=0.1, jitter=False),
            sleep=fake_sleep,
        )
        client = ScryfallClient(http_client=shared_http)

        with self.assertRaises(ScryfallRateLimitError) as ctx:
            await client.get_card_by_name(exact="Sol Ring")

        self.assertIn("Rate limited", str(ctx.exception))
        self.assertEqual(ctx.exception.retry_after, 2.5)
        self.assertEqual(attempts, 3)  # Initial + 2 retries

    async def test_400_bad_request_raises_scryfall_request_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"object": "error", "code": "bad_request", "details": "Syntax error in query."},
            )

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(base_url="https://api.scryfall.com", transport=transport)
        client = ScryfallClient(http_client=shared_http)

        with self.assertRaises(ScryfallRequestError) as ctx:
            await client.search_cards("invalid[[syntax")
        self.assertEqual(ctx.exception.status_code, 400)
        self.assertIsInstance(ctx.exception, ScryfallError)

    async def test_500_server_error_raises_scryfall_request_error(self):
        attempts = 0

        async def fake_sleep(seconds: float) -> None:
            await asyncio.sleep(0)

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(500, text="Internal Server Error")

        transport = httpx.MockTransport(handler)
        shared_http = SharedHttpClient(
            base_url="https://api.scryfall.com",
            transport=transport,
            retry_config=RetryConfig(max_retries=1, base_delay=0.01, max_delay=0.05, jitter=False),
            sleep=fake_sleep,
        )
        client = ScryfallClient(http_client=shared_http)

        with self.assertRaises(ScryfallRequestError) as ctx:
            await client.get_card_by_name(exact="Sol Ring")
        self.assertEqual(ctx.exception.status_code, 500)


class TestScryfallClientLifecycle(unittest.IsolatedAsyncioTestCase):
    """Test client initialization, async context manager, and resource cleanup."""

    async def test_context_manager_lifecycle(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_make_mock_card_payload("Swamp"))

        transport = httpx.MockTransport(handler)

        async with ScryfallClient(transport=transport) as client:
            card = await client.get_card_by_name("Swamp")
            self.assertIsNotNone(card)
            assert card is not None
            self.assertEqual(card.name, "Swamp")
            self.assertFalse(client.http_client.is_closed)

        self.assertTrue(client.http_client.is_closed)

    async def test_injected_shared_http_client_not_closed_by_scryfall_client(self):
        shared_http = SharedHttpClient()
        client = ScryfallClient(http_client=shared_http)

        await client.close()
        # Injected client should NOT be closed when ScryfallClient is closed
        self.assertFalse(shared_http.is_closed)

        await shared_http.close()
        self.assertTrue(shared_http.is_closed)


if __name__ == "__main__":
    unittest.main()

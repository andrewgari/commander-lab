"""Integration-style tests for src.shared.http.client.SharedHttpClient.

Tests verify:
- Response caching and reuse (GET responses cached, cache hit bypasses transport)
- Rate limiting enforcement and delays across requests
- Automatic retries on transient errors (503, 429, timeouts) with backoff
- Standardized error propagation (ApiError, RateLimitError, TimeoutError, HttpClientError)
- HTTP convenience methods (GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS)
- Context manager lifecycle and resource cleanup
- Pre-configured MTG API profiles ('scryfall', 'archidekt', etc.) and factory functions
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import unittest
from typing import Any, Dict, List
from unittest import mock

import httpx

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from src.shared.http import (
    ApiError,
    HttpClientConfig,
    HttpClientError,
    RateLimitConfig,
    RateLimitError,
    RetryConfig,
    SharedHttpClient,
    TimeoutError as HttpTimeoutError,
    create_http_client,
)
from src.shared.http.cache import TTLCache


class FakeClock:
    """Controllable monotonic clock for deterministic rate limiter tests."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


_REAL_SLEEP = asyncio.sleep


def make_sleep_tracker(clock: FakeClock):
    """Return an async sleep function that records slept time and advances clock."""
    records = {"slept_total": 0.0, "sleep_calls": []}

    async def fake_sleep(seconds: float) -> None:
        records["slept_total"] += seconds
        records["sleep_calls"].append(seconds)
        clock.advance(seconds)
        # Yield control once without blocking using unpatched sleep
        await _REAL_SLEEP(0)

    return fake_sleep, records


class TestSharedHttpClientCaching(unittest.IsolatedAsyncioTestCase):
    """Test response caching and cache reuse behavior."""

    async def test_cached_response_reuse_for_identical_get(self):
        """A second identical GET request returns the cached response without hitting the transport."""
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(
                200,
                headers={"Content-Type": "application/json", "X-Custom": "mtg"},
                json={"card": "Black Lotus", "call": request_count},
            )

        transport = httpx.MockTransport(handler)

        async with SharedHttpClient(
            base_url="https://api.scryfall.com",
            transport=transport,
        ) as client:
            # First call -> cache miss, hits transport
            resp1 = await client.get("/cards/named", params={"exact": "Black Lotus"})
            self.assertEqual(resp1.status_code, 200)
            self.assertEqual(resp1.json()["card"], "Black Lotus")
            self.assertEqual(resp1.json()["call"], 1)
            self.assertFalse(getattr(resp1, "from_cache", True))
            self.assertEqual(request_count, 1)

            # Second call -> cache hit, does not hit transport
            resp2 = await client.get("/cards/named", params={"exact": "Black Lotus"})
            self.assertEqual(resp2.status_code, 200)
            self.assertEqual(resp2.json()["card"], "Black Lotus")
            self.assertEqual(resp2.json()["call"], 1)  # returned identical cached data
            self.assertTrue(getattr(resp2, "from_cache", False))
            self.assertEqual(request_count, 1)  # transport was NOT called again

    async def test_different_query_params_or_url_misses_cache(self):
        """Differing URLs or query parameters produce distinct cache keys."""
        request_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal request_count
            request_count += 1
            return httpx.Response(200, json={"url": str(request.url), "count": request_count})

        transport = httpx.MockTransport(handler)

        async with SharedHttpClient(
            base_url="https://api.scryfall.com",
            transport=transport,
        ) as client:
            resp1 = await client.get("/cards/named", params={"exact": "Sol Ring"})
            resp2 = await client.get("/cards/named", params={"exact": "Mana Crypt"})
            resp3 = await client.get("/sets")

            self.assertEqual(request_count, 3)
            self.assertFalse(getattr(resp1, "from_cache", True))
            self.assertFalse(getattr(resp2, "from_cache", True))
            self.assertFalse(getattr(resp3, "from_cache", True))

    async def test_non_get_requests_are_not_cached(self):
        """POST, PUT, DELETE requests must not be cached."""
        post_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal post_count
            post_count += 1
            return httpx.Response(201, json={"created": post_count})

        transport = httpx.MockTransport(handler)

        async with SharedHttpClient(
            base_url="https://archidekt.com/api",
            transport=transport,
        ) as client:
            resp1 = await client.post("/decks", json={"name": "Deck 1"})
            resp2 = await client.post("/decks", json={"name": "Deck 1"})

            self.assertEqual(post_count, 2)
            self.assertFalse(getattr(resp1, "from_cache", True))
            self.assertFalse(getattr(resp2, "from_cache", True))

    async def test_use_cache_false_bypasses_cache(self):
        """Setting use_cache=False bypasses reading from and writing to cache."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json={"call": call_count})

        transport = httpx.MockTransport(handler)

        async with SharedHttpClient(
            base_url="https://api.scryfall.com",
            transport=transport,
        ) as client:
            resp1 = await client.get("/cards/1", use_cache=False)
            resp2 = await client.get("/cards/1", use_cache=False)

            self.assertEqual(call_count, 2)
            self.assertFalse(getattr(resp1, "from_cache", True))
            self.assertFalse(getattr(resp2, "from_cache", True))

    async def test_cache_ttl_expiration(self):
        """After cache TTL expires, request hits transport again."""
        call_count = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal call_count
            call_count += 1
            return httpx.Response(200, json={"call": call_count})

        transport = httpx.MockTransport(handler)
        cache = TTLCache(default_ttl=1)

        async with SharedHttpClient(
            base_url="https://api.scryfall.com",
            cache=cache,
            transport=transport,
        ) as client:
            resp1 = await client.get("/cards/1")
            self.assertEqual(call_count, 1)

            # Advance cache clock forward past TTL
            real_now = cache._now
            cache._now = lambda: real_now() + 10.0

            resp2 = await client.get("/cards/1")
            self.assertEqual(call_count, 2)
            self.assertFalse(getattr(resp2, "from_cache", True))


class TestSharedHttpClientRateLimiting(unittest.IsolatedAsyncioTestCase):
    """Test rate limiting enforcement and delays."""

    async def test_rate_limiting_delays_excess_requests(self):
        """Requests exceeding rate limit burst are delayed according to token refill."""
        clock = FakeClock()
        fake_sleep, sleep_records = make_sleep_tracker(clock)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)

        rate_limit = RateLimitConfig(requests_per_second=2.0, burst=2.0)

        with mock.patch("asyncio.sleep", fake_sleep):
            async with SharedHttpClient(
                base_url="https://api.scryfall.com",
                rate_limit=rate_limit,
                enable_cache=False,
                clock=clock,
                sleep=fake_sleep,
                transport=transport,
            ) as client:
                # 1st and 2nd request use burst capacity -> no sleep
                await client.get("/cards/1")
                await client.get("/cards/2")
                self.assertEqual(sleep_records["slept_total"], 0.0)

                # 3rd request exceeds burst capacity -> must delay
                await client.get("/cards/3")
                self.assertGreater(sleep_records["slept_total"], 0.0)

    async def test_cached_requests_do_not_consume_rate_limit_slot(self):
        """Cache hits return before rate limiter acquisition, saving rate limit slots."""
        clock = FakeClock()
        fake_sleep, sleep_records = make_sleep_tracker(clock)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"card": "Sol Ring"})

        transport = httpx.MockTransport(handler)

        rate_limit = RateLimitConfig(requests_per_second=1.0, burst=1.0)

        with mock.patch("asyncio.sleep", fake_sleep):
            async with SharedHttpClient(
                base_url="https://api.scryfall.com",
                rate_limit=rate_limit,
                enable_cache=True,
                clock=clock,
                sleep=fake_sleep,
                transport=transport,
            ) as client:
                # 1st request uses the 1 token
                await client.get("/cards/sol-ring")
                self.assertEqual(sleep_records["slept_total"], 0.0)

                # 2nd and 3rd requests are identical -> cache hits, no rate limit delay
                for _ in range(5):
                    resp = await client.get("/cards/sol-ring")
                    self.assertTrue(getattr(resp, "from_cache", False))

                self.assertEqual(sleep_records["slept_total"], 0.0)

    async def test_per_host_rate_limiter_isolation(self):
        """Rate limiting on one host does not throttle another host."""
        clock = FakeClock()
        fake_sleep, sleep_records = make_sleep_tracker(clock)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"host": request.url.host})

        transport = httpx.MockTransport(handler)

        limits = {
            "api.scryfall.com": {"requests_per_second": 1.0, "burst": 1.0},
            "archidekt.com": {"requests_per_second": 100.0, "burst": 100.0},
        }

        with mock.patch("asyncio.sleep", fake_sleep):
            async with SharedHttpClient(
                rate_limit=limits,
                enable_cache=False,
                clock=clock,
                sleep=fake_sleep,
                transport=transport,
            ) as client:
                # Scryfall: 1st consumes burst
                await client.get("https://api.scryfall.com/cards/1")
                slept_before = sleep_records["slept_total"]

                # Archidekt: fast bucket, should not sleep
                await client.get("https://archidekt.com/api/decks/1")
                self.assertEqual(sleep_records["slept_total"], slept_before)


class TestSharedHttpClientRetry(unittest.IsolatedAsyncioTestCase):
    """Test retry on transient failures (503, 429, timeouts)."""

    async def test_retry_on_503_succeeds_eventually(self):
        """Client retries on 503 Service Unavailable and succeeds on subsequent try."""
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts < 3:
                return httpx.Response(503, text="Service Unavailable")
            return httpx.Response(200, json={"status": "ok", "attempt": attempts})

        transport = httpx.MockTransport(handler)

        async def noop_sleep(_delay: float) -> None:
            return None

        async with SharedHttpClient(
            base_url="https://api.scryfall.com",
            retry_config=RetryConfig(max_retries=3, base_delay=0.01),
            sleep=noop_sleep,
            transport=transport,
        ) as client:
            resp = await client.get("/status")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json()["status"], "ok")
            self.assertEqual(attempts, 3)

    async def test_retry_on_503_exhausted_raises_api_error(self):
        """When retries are exhausted on 503, ApiError is raised with status_code=503."""
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(503, text="Server Down")

        transport = httpx.MockTransport(handler)

        async def noop_sleep(_delay: float) -> None:
            return None

        async with SharedHttpClient(
            base_url="https://api.scryfall.com",
            retry_config=RetryConfig(max_retries=2, base_delay=0.01),
            sleep=noop_sleep,
            transport=transport,
        ) as client:
            with self.assertRaises(ApiError) as ctx:
                await client.get("/status")

            self.assertEqual(ctx.exception.status_code, 503)
            self.assertIn("Server Down", str(ctx.exception.body))
            # Initial attempt + 2 retries = 3 attempts
            self.assertEqual(attempts, 3)

    async def test_retry_respects_retry_after_on_429(self):
        """When server returns 429 with Retry-After, backoff sleeps for the header value."""
        attempts = 0
        slept_durations = []

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                return httpx.Response(429, headers={"Retry-After": "4.5"}, text="Too Many Requests")
            return httpx.Response(200, json={"ok": True})

        transport = httpx.MockTransport(handler)

        async def record_sleep(delay: float) -> None:
            slept_durations.append(delay)

        async with SharedHttpClient(
            base_url="https://api.scryfall.com",
            retry_config=RetryConfig(max_retries=2, base_delay=0.01),
            sleep=record_sleep,
            transport=transport,
        ) as client:
            resp = await client.get("/cards")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(attempts, 2)
            self.assertIn(4.5, slept_durations)

    async def test_retry_on_timeout(self):
        """HTTP timeouts trigger retries and raise TimeoutError on exhaustion."""
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ReadTimeout("Read timed out")

        transport = httpx.MockTransport(handler)

        async def noop_sleep(_delay: float) -> None:
            return None

        async with SharedHttpClient(
            base_url="https://api.scryfall.com",
            retry_config=RetryConfig(max_retries=2, base_delay=0.01),
            sleep=noop_sleep,
            transport=transport,
        ) as client:
            with self.assertRaises(HttpTimeoutError) as ctx:
                await client.get("/cards/slow")

            self.assertEqual(attempts, 3)
            self.assertIn("timed out after 3 attempts", str(ctx.exception))


class TestSharedHttpClientErrorPropagation(unittest.IsolatedAsyncioTestCase):
    """Test standardized error propagation for non-retryable and fatal errors."""

    async def test_non_retryable_404_raises_api_error_immediately(self):
        """A 404 Not Found error raises ApiError immediately without retrying."""
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(404, json={"details": "Card not found"})

        transport = httpx.MockTransport(handler)

        async with SharedHttpClient(
            base_url="https://api.scryfall.com",
            transport=transport,
        ) as client:
            with self.assertRaises(ApiError) as ctx:
                await client.get("/cards/named", params={"exact": "Not A Card"})

            self.assertEqual(ctx.exception.status_code, 404)
            self.assertEqual(attempts, 1)

    async def test_exhausted_429_raises_rate_limit_error(self):
        """When retries for 429 exhaust, RateLimitError is raised."""
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            return httpx.Response(429, headers={"Retry-After": "2"}, text="Throttled")

        transport = httpx.MockTransport(handler)

        async def noop_sleep(_delay: float) -> None:
            return None

        async with SharedHttpClient(
            base_url="https://api.scryfall.com",
            retry_config=RetryConfig(max_retries=1, base_delay=0.01),
            sleep=noop_sleep,
            transport=transport,
        ) as client:
            with self.assertRaises(RateLimitError) as ctx:
                await client.get("/throttled")

            self.assertEqual(ctx.exception.retry_after, 2.0)
            self.assertEqual(attempts, 2)

    async def test_connection_failure_raises_http_client_error(self):
        """Connection errors raise HttpClientError upon retry exhaustion."""
        attempts = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            raise httpx.ConnectError("Connection refused")

        transport = httpx.MockTransport(handler)

        async def noop_sleep(_delay: float) -> None:
            return None

        async with SharedHttpClient(
            base_url="https://api.scryfall.com",
            retry_config=RetryConfig(max_retries=1, base_delay=0.01),
            sleep=noop_sleep,
            transport=transport,
        ) as client:
            with self.assertRaises(HttpClientError) as ctx:
                await client.get("/unreachable")

            self.assertIn("Connection refused", str(ctx.exception))
            self.assertEqual(attempts, 2)


class TestSharedHttpClientVerbsAndContextManager(unittest.IsolatedAsyncioTestCase):
    """Test HTTP verbs and context manager lifecycle."""

    async def test_all_convenience_methods(self):
        """Test get, post, put, patch, delete, head, options."""
        received_methods: List[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            received_methods.append(request.method)
            return httpx.Response(200, json={"method": request.method})

        transport = httpx.MockTransport(handler)

        async with SharedHttpClient(
            base_url="https://api.example.com",
            transport=transport,
        ) as client:
            await client.get("/test", use_cache=False)
            await client.post("/test", json={"data": 1})
            await client.put("/test", json={"data": 2})
            await client.patch("/test", json={"data": 3})
            await client.delete("/test")
            await client.head("/test")
            await client.options("/test")

        self.assertEqual(
            received_methods,
            ["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
        )

    async def test_context_manager_closes_underlying_client(self):
        """Exiting the async context manager closes the underlying httpx client."""
        client = SharedHttpClient(base_url="https://api.example.com")
        self.assertFalse(client.is_closed)

        async with client:
            self.assertFalse(client.is_closed)

        self.assertTrue(client.is_closed)

    async def test_manual_close(self):
        """Calling aclose() or close() closes the client."""
        client = SharedHttpClient(base_url="https://api.example.com")
        await client.close()
        self.assertTrue(client.is_closed)

    async def test_injected_client_lifecycle_not_closed_by_wrapper(self):
        """A caller-owned injected client is NOT entered, exited, or closed by the wrapper."""
        injected_client = httpx.AsyncClient(base_url="https://api.example.com")
        self.assertFalse(injected_client.is_closed)

        wrapper = SharedHttpClient(client=injected_client)
        async with wrapper:
            self.assertFalse(injected_client.is_closed)

        self.assertFalse(injected_client.is_closed)
        await wrapper.aclose()
        self.assertFalse(injected_client.is_closed)

        # Caller closes their own client
        await injected_client.aclose()
        self.assertTrue(injected_client.is_closed)


class TestSharedHttpClientProfilesAndFactory(unittest.IsolatedAsyncioTestCase):
    """Test MTG API predefined profiles and factory functions."""

    def test_predefined_profiles_exist(self):
        """Verify all major MTG API profiles are registered."""
        for name in ("scryfall", "archidekt", "edhrec", "commandersalt", "moxfield", "default"):
            profile = SharedHttpClient.get_profile(name)
            self.assertIsInstance(profile, HttpClientConfig)
            self.assertIsNotNone(profile.headers)
            self.assertIn("User-Agent", profile.headers)

    def test_unknown_profile_raises_value_error(self):
        """Requesting an unknown profile name raises ValueError."""
        with self.assertRaises(ValueError) as ctx:
            SharedHttpClient.from_config("nonexistent_mtg_api")
        self.assertIn("Unknown HTTP client profile", str(ctx.exception))

    def test_from_config_profile_overrides(self):
        """Profile parameters can be overridden via keyword arguments in from_config."""
        client = SharedHttpClient.from_config(
            "scryfall",
            cache_ttl=999,
            headers={"Authorization": "Bearer secret"},
        )
        self.assertEqual(str(client.base_url), "https://api.scryfall.com")
        self.assertIsNotNone(client.cache)
        assert client.cache is not None
        self.assertEqual(client.cache.default_ttl, 999)
        self.assertEqual(client.client.headers.get("Authorization"), "Bearer secret")
        self.assertEqual(
            client.client.headers.get("User-Agent"),
            "commander-lab/1.0 (+https://github.com/andrewgari/commander-lab)",
        )

    def test_register_custom_profile(self):
        """Users can register new profiles at runtime."""
        SharedHttpClient.register_profile(
            "custom_service",
            HttpClientConfig(
                base_url="https://custom.service.test",
                rate_limit={"requests_per_second": 3.0},
                cache_ttl=45,
            ),
        )

        client = SharedHttpClient.from_config("custom_service")
        self.assertEqual(str(client.base_url), "https://custom.service.test")
        self.assertIsNotNone(client.cache)
        assert client.cache is not None
        self.assertEqual(client.cache.default_ttl, 45)

    def test_create_http_client_factory_function(self):
        """create_http_client factory creates valid SharedHttpClient."""
        client = create_http_client("edhrec")
        self.assertEqual(str(client.base_url), "https://json.edhrec.com")
        self.assertIsNotNone(client.cache)
        assert client.cache is not None
        self.assertEqual(client.cache.default_ttl, 600)

    async def test_acceptance_criteria_workflow(self):
        """Verify: async with SharedHttpClient.from_config('scryfall') as client: resp = await client.get(url)."""
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if request.url.path == "/cards/random":
                return httpx.Response(
                    200,
                    json={"id": "abc-123", "name": "Atraxa, Praetors' Voice"},
                )
            return httpx.Response(404, json={"error": "not found"})

        transport = httpx.MockTransport(handler)

        async with SharedHttpClient.from_config("scryfall", transport=transport) as client:
            resp = await client.get("/cards/random")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(data["name"], "Atraxa, Praetors' Voice")
            self.assertFalse(getattr(resp, "from_cache", True))
            self.assertEqual(calls, 1)

            # Re-fetch same URL -> served automatically from cache
            cached_resp = await client.get("/cards/random")
            self.assertEqual(cached_resp.status_code, 200)
            self.assertEqual(cached_resp.json()["name"], "Atraxa, Praetors' Voice")
            self.assertTrue(getattr(cached_resp, "from_cache", False))
            self.assertEqual(calls, 1)  # No network call


if __name__ == "__main__":
    unittest.main()

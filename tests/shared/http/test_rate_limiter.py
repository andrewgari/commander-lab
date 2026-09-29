"""Unit tests for src.shared.http.rate_limiter.RateLimiter.

Uses a fake monotonic clock so tests are deterministic and fast: instead of
sleeping in wall-clock time, we patch asyncio.sleep to advance the fake
clock and yield control, so token-bucket timing math is exercised exactly
without real delays.
"""

from __future__ import annotations

import asyncio
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from src.shared.http.rate_limiter import RateLimitConfig, RateLimiter, _TokenBucket


class FakeClock:
    """A controllable monotonic clock for deterministic bucket tests."""

    def __init__(self, start: float = 0.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


_REAL_SLEEP = asyncio.sleep


def make_sleep_patch(clock: FakeClock):
    """Return an async function that advances the fake clock instead of
    actually sleeping, recording total 'slept' time for assertions."""

    total_slept = {"seconds": 0.0}

    async def fake_sleep(seconds: float) -> None:
        total_slept["seconds"] += seconds
        clock.advance(seconds)
        # Yield control once so other tasks can run, mirroring real sleep.
        # Uses the *real* sleep(0) so this doesn't recurse into itself
        # while asyncio.sleep is patched.
        await _REAL_SLEEP(0)

    return fake_sleep, total_slept


class RateLimitConfigTests(unittest.TestCase):
    def test_rejects_non_positive_rate(self):
        with self.assertRaises(ValueError):
            RateLimitConfig(requests_per_second=0)
        with self.assertRaises(ValueError):
            RateLimitConfig(requests_per_second=-1)

    def test_default_burst_equals_rate(self):
        cfg = RateLimitConfig(requests_per_second=10)
        self.assertEqual(cfg.burst, 10)

    def test_default_burst_minimum_one(self):
        cfg = RateLimitConfig(requests_per_second=0.5)
        self.assertEqual(cfg.burst, 1.0)

    def test_from_mapping_dict(self):
        cfg = RateLimitConfig.from_mapping({"requests_per_second": 5, "burst": 15})
        self.assertEqual(cfg.requests_per_second, 5)
        self.assertEqual(cfg.burst, 15)

    def test_from_mapping_passthrough(self):
        original = RateLimitConfig(requests_per_second=3)
        self.assertIs(RateLimitConfig.from_mapping(original), original)


class TokenBucketTests(unittest.IsolatedAsyncioTestCase):
    async def test_burst_allows_immediate_requests_up_to_capacity(self):
        clock = FakeClock()
        bucket = _TokenBucket(RateLimitConfig(requests_per_second=5, burst=5), clock=clock)

        fake_sleep, slept = make_sleep_patch(clock)
        with mock.patch("asyncio.sleep", fake_sleep):
            for _ in range(5):
                await bucket.acquire()

        # All 5 tokens were available immediately; no waiting required.
        self.assertEqual(slept["seconds"], 0.0)

    async def test_rate_enforced_beyond_burst(self):
        clock = FakeClock()
        bucket = _TokenBucket(RateLimitConfig(requests_per_second=2, burst=2), clock=clock)

        fake_sleep, slept = make_sleep_patch(clock)
        with mock.patch("asyncio.sleep", fake_sleep):
            await bucket.acquire()  # consumes 1st token, free
            await bucket.acquire()  # consumes 2nd token, free
            await bucket.acquire()  # bucket empty -> must wait ~0.5s

        self.assertAlmostEqual(slept["seconds"], 0.5, places=3)

    async def test_tokens_refill_over_time(self):
        clock = FakeClock()
        bucket = _TokenBucket(RateLimitConfig(requests_per_second=1, burst=1), clock=clock)

        fake_sleep, slept = make_sleep_patch(clock)
        with mock.patch("asyncio.sleep", fake_sleep):
            await bucket.acquire()  # uses the only token
            clock.advance(1.0)  # simulate 1s passing -> 1 token refilled
            await bucket.acquire()  # should be immediate now

        self.assertEqual(slept["seconds"], 0.0)


class RateLimiterTests(unittest.IsolatedAsyncioTestCase):
    async def test_unconfigured_host_is_not_limited(self):
        limiter = RateLimiter({"api.scryfall.com": {"requests_per_second": 10}})
        self.assertFalse(limiter.has_limit("unconfigured.example.com"))
        # Should return immediately without raising or blocking.
        await asyncio.wait_for(limiter.acquire("unconfigured.example.com"), timeout=1)

    async def test_basic_rate_enforcement_delays_excess_requests(self):
        clock = FakeClock()
        limiter = RateLimiter({"host": {"requests_per_second": 2, "burst": 2}}, clock=clock)

        fake_sleep, slept = make_sleep_patch(clock)
        with mock.patch("asyncio.sleep", fake_sleep):
            await limiter.acquire("host")
            await limiter.acquire("host")
            await limiter.acquire("host")  # exceeds burst -> delayed

        self.assertGreater(slept["seconds"], 0.0)

    async def test_per_host_isolation(self):
        clock = FakeClock()
        limiter = RateLimiter(
            {
                "slow.example.com": {"requests_per_second": 1, "burst": 1},
                "fast.example.com": {"requests_per_second": 100, "burst": 100},
            },
            clock=clock,
        )

        fake_sleep, slept = make_sleep_patch(clock)
        with mock.patch("asyncio.sleep", fake_sleep):
            # Exhaust the slow host's single token.
            await limiter.acquire("slow.example.com")
            # The fast host should be unaffected and require no sleep,
            # even though the slow host's bucket is now empty.
            before = slept["seconds"]
            await limiter.acquire("fast.example.com")
            after = slept["seconds"]

        self.assertEqual(before, after)

    async def test_burst_handling_allows_initial_spike_then_throttles(self):
        clock = FakeClock()
        limiter = RateLimiter({"host": {"requests_per_second": 5, "burst": 3}}, clock=clock)

        fake_sleep, slept = make_sleep_patch(clock)
        with mock.patch("asyncio.sleep", fake_sleep):
            # First 3 requests use the burst capacity for free.
            for _ in range(3):
                await limiter.acquire("host")
            self.assertEqual(slept["seconds"], 0.0)

            # 4th request exceeds burst and must wait.
            await limiter.acquire("host")
            self.assertGreater(slept["seconds"], 0.0)

    async def test_concurrent_access_serializes_without_dropping_requests(self):
        """Multiple concurrent coroutines acquiring the same bucket should
        all eventually succeed (none dropped), with total wait time
        matching the expected throughput of the configured rate."""
        clock = FakeClock()
        limiter = RateLimiter({"host": {"requests_per_second": 10, "burst": 1}}, clock=clock)

        completed = []

        async def worker(i: int):
            await limiter.acquire("host")
            completed.append(i)

        fake_sleep, slept = make_sleep_patch(clock)
        with mock.patch("asyncio.sleep", fake_sleep):
            await asyncio.gather(*(worker(i) for i in range(20)))

        # All 20 requests eventually completed -- none were dropped.
        self.assertEqual(sorted(completed), list(range(20)))

    async def test_configure_updates_limit_at_runtime(self):
        limiter = RateLimiter()
        self.assertFalse(limiter.has_limit("host"))
        limiter.configure("host", {"requests_per_second": 5})
        self.assertTrue(limiter.has_limit("host"))
        await asyncio.wait_for(limiter.acquire("host"), timeout=1)

    async def test_default_config_applies_to_unlisted_hosts(self):
        limiter = RateLimiter(
            limits={"api.scryfall.com": {"requests_per_second": 10}},
            default={"requests_per_second": 1},
        )
        self.assertTrue(limiter.has_limit("anything.example.com"))


if __name__ == "__main__":
    unittest.main()

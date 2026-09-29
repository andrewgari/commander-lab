"""Unit tests for src.shared.http.cache.TTLCache."""

import asyncio
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.shared.http.cache import CachedResponse, TTLCache, make_cache_key


def run(coro):
    return asyncio.run(coro)


class MakeCacheKeyTests(unittest.TestCase):
    def test_key_normalization_sorts_params(self):
        key_a = make_cache_key("get", "https://api.example.com/cards", {"b": 2, "a": 1})
        key_b = make_cache_key("GET", "https://api.example.com/cards", {"a": 1, "b": 2})
        self.assertEqual(key_a, key_b)

    def test_key_normalization_merges_url_query_string(self):
        key_from_query_string = make_cache_key("GET", "https://api.example.com/cards?a=1&b=2")
        key_from_params = make_cache_key("GET", "https://api.example.com/cards", {"b": 2, "a": 1})
        self.assertEqual(key_from_query_string, key_from_params)

    def test_key_differs_on_method(self):
        get_key = make_cache_key("GET", "https://api.example.com/cards")
        post_key = make_cache_key("POST", "https://api.example.com/cards")
        self.assertNotEqual(get_key, post_key)

    def test_key_differs_on_params(self):
        key_a = make_cache_key("GET", "https://api.example.com/cards", {"page": 1})
        key_b = make_cache_key("GET", "https://api.example.com/cards", {"page": 2})
        self.assertNotEqual(key_a, key_b)


class TTLCacheTests(unittest.TestCase):
    def test_cache_hit_returns_stored_response(self):
        async def scenario():
            cache = TTLCache()
            key = make_cache_key("GET", "https://api.example.com/cards")
            response = CachedResponse(status_code=200, headers={"content-type": "application/json"}, body=b'{"ok":true}')

            await cache.set(key, response)
            result = await cache.get(key)

            self.assertIsNotNone(result)
            self.assertEqual(result.status_code, 200)
            self.assertEqual(result.headers["content-type"], "application/json")
            self.assertEqual(result.body, b'{"ok":true}')

        run(scenario())

    def test_cache_miss_returns_none(self):
        async def scenario():
            cache = TTLCache()
            key = make_cache_key("GET", "https://api.example.com/missing")
            result = await cache.get(key)
            self.assertIsNone(result)

        run(scenario())

    def test_ttl_expiry_evicts_entry(self):
        async def scenario():
            cache = TTLCache(default_ttl=300)
            key = make_cache_key("GET", "https://api.example.com/cards")
            response = CachedResponse(status_code=200)

            # Use a very short TTL for this entry specifically.
            await cache.set(key, response, ttl=1)
            fresh = await cache.get(key)
            self.assertIsNotNone(fresh)

            # Force the clock forward by monkeypatching _now on the instance.
            real_now = cache._now
            cache._now = lambda: real_now() + 10

            expired = await cache.get(key)
            self.assertIsNone(expired)

            # Confirm it was actually evicted, not just reported missing.
            self.assertEqual(await cache.size(), 0)

        run(scenario())

    def test_lru_eviction_bounds_size(self):
        async def scenario():
            cache = TTLCache(max_size=2, default_ttl=300)
            key1 = make_cache_key("GET", "https://api.example.com/a")
            key2 = make_cache_key("GET", "https://api.example.com/b")
            key3 = make_cache_key("GET", "https://api.example.com/c")

            await cache.set(key1, CachedResponse(status_code=200, body="a"))
            await cache.set(key2, CachedResponse(status_code=200, body="b"))

            # Touch key1 so it becomes most-recently-used, key2 becomes LRU.
            await cache.get(key1)

            await cache.set(key3, CachedResponse(status_code=200, body="c"))

            self.assertEqual(await cache.size(), 2)
            self.assertIsNone(await cache.get(key2))
            self.assertIsNotNone(await cache.get(key1))
            self.assertIsNotNone(await cache.get(key3))

        run(scenario())

    def test_key_normalization_reuses_cache_entry(self):
        async def scenario():
            cache = TTLCache()
            key_a = make_cache_key("GET", "https://api.example.com/cards", {"b": 2, "a": 1})
            key_b = make_cache_key("GET", "https://api.example.com/cards?a=1&b=2")

            await cache.set(key_a, CachedResponse(status_code=200, body="cached"))
            result = await cache.get(key_b)

            self.assertIsNotNone(result)
            self.assertEqual(result.body, "cached")

        run(scenario())

    def test_set_overwrites_existing_key(self):
        async def scenario():
            cache = TTLCache()
            key = make_cache_key("GET", "https://api.example.com/cards")

            await cache.set(key, CachedResponse(status_code=200, body="first"))
            await cache.set(key, CachedResponse(status_code=200, body="second"))

            result = await cache.get(key)
            self.assertEqual(result.body, "second")
            self.assertEqual(await cache.size(), 1)

        run(scenario())

    def test_constructor_validates_arguments(self):
        with self.assertRaises(ValueError):
            TTLCache(max_size=0)
        with self.assertRaises(ValueError):
            TTLCache(default_ttl=0)

    def test_clear_removes_all_entries(self):
        async def scenario():
            cache = TTLCache()
            key = make_cache_key("GET", "https://api.example.com/cards")
            await cache.set(key, CachedResponse(status_code=200))
            await cache.clear()
            self.assertEqual(await cache.size(), 0)
            self.assertIsNone(await cache.get(key))

        run(scenario())


if __name__ == "__main__":
    unittest.main()

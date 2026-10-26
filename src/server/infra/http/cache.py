"""In-memory TTL cache for HTTP responses.

Provides an async-compatible cache keyed by (method, URL, sorted query
params) with per-entry TTL expiration and LRU eviction to bound memory
usage. Intended for use by shared HTTP client utilities that want to
avoid duplicate requests to third-party APIs.
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional
from urllib.parse import urlsplit, parse_qsl

DEFAULT_TTL_SECONDS = 300


@dataclass(frozen=True)
class CachedResponse:
    """A minimal response-like object stored in the cache."""

    status_code: int
    headers: Mapping[str, str] = field(default_factory=dict)
    body: Any = None

    def __post_init__(self) -> None:
        # Normalize headers to a plain, immutable-ish dict for safety.
        object.__setattr__(self, "headers", dict(self.headers))


def make_cache_key(method: str, url: str, params: Optional[Mapping[str, Any]] = None) -> tuple:
    """Build a normalized cache key from method, URL, and query params.

    Query params passed explicitly via `params` override same-named
    params already present in `url`'s query string (matching typical
    HTTP client semantics), while repeated params for a given name are
    preserved rather than collapsed, so `?color=U&color=R` is kept
    distinct from `?color=R`. The resulting (key, value) pairs are
    sorted so that differing param order produces the same key. The URL
    itself is stored without its query string since params are captured
    separately.
    """
    normalized_method = method.upper()

    split = urlsplit(url)
    base_url = f"{split.scheme}://{split.netloc}{split.path}"

    url_pairs = parse_qsl(split.query, keep_blank_values=True)
    override_names = {str(k) for k in params} if params else set()

    combined_pairs: list[tuple[str, str]] = [
        (k, v) for k, v in url_pairs if k not in override_names
    ]
    if params:
        combined_pairs.extend((str(k), str(v)) for k, v in params.items())

    sorted_params = tuple(sorted(combined_pairs))

    return (normalized_method, base_url, sorted_params)


class TTLCache:
    """Async in-memory cache with per-entry TTL and LRU eviction.

    Not process-shared: state lives only in this instance's memory.
    Safe for concurrent async use within a single event loop via an
    internal asyncio.Lock.
    """

    def __init__(self, max_size: int = 256, default_ttl: int = DEFAULT_TTL_SECONDS) -> None:
        if max_size <= 0:
            raise ValueError("max_size must be a positive integer")
        if default_ttl <= 0:
            raise ValueError("default_ttl must be a positive integer")

        self._max_size = max_size
        self._default_ttl = default_ttl
        self._store: "OrderedDict[tuple, tuple[float, CachedResponse]]" = OrderedDict()
        self._lock = asyncio.Lock()

    @property
    def max_size(self) -> int:
        return self._max_size

    @property
    def default_ttl(self) -> int:
        return self._default_ttl

    def _now(self) -> float:
        return time.monotonic()

    async def get(self, key: tuple) -> Optional[CachedResponse]:
        """Return the cached response for `key`, or None on miss/expired.

        A stale entry is evicted from the store on access.
        """
        async with self._lock:
            entry = self._store.get(key)
            if entry is None:
                return None

            expires_at, response = entry
            if self._now() >= expires_at:
                del self._store[key]
                return None

            # Mark as most-recently-used.
            self._store.move_to_end(key)
            return response

    async def set(self, key: tuple, response: CachedResponse, ttl: Optional[int] = None) -> None:
        """Store `response` under `key` with the given TTL (seconds)."""
        effective_ttl = self._default_ttl if ttl is None else ttl
        if effective_ttl <= 0:
            raise ValueError("ttl must be a positive integer")

        expires_at = self._now() + effective_ttl

        async with self._lock:
            if key in self._store:
                del self._store[key]
            self._store[key] = (expires_at, response)
            self._store.move_to_end(key)

            while len(self._store) > self._max_size:
                self._store.popitem(last=False)

    async def delete(self, key: tuple) -> None:
        """Remove `key` from the cache if present."""
        async with self._lock:
            self._store.pop(key, None)

    async def clear(self) -> None:
        """Remove all entries from the cache."""
        async with self._lock:
            self._store.clear()

    async def size(self) -> int:
        """Return the current number of stored entries (including any
        expired-but-not-yet-accessed entries)."""
        async with self._lock:
            return len(self._store)

"""Configurable async rate limiter for outbound HTTP requests.

Implements a token-bucket algorithm with per-host (and optionally
per-endpoint) configuration so it can be shared across multiple third-party
API integrations (e.g. Scryfall, magicthegathering.io) without one noisy
client starving another.

Example
-------
    limiter = RateLimiter({
        "api.scryfall.com": {"requests_per_second": 10},
        "api.magicthegathering.io": {"requests_per_second": 5, "burst": 5},
    })

    async def fetch(url: str, host: str) -> None:
        await limiter.acquire(host)
        # ... perform the HTTP request ...

The limiter is safe to share across coroutines/tasks: each host gets its
own independent token bucket guarded by an ``asyncio.Lock``, so a burst
against one host never delays requests to another.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class RateLimitConfig:
    """Configuration for a single host/endpoint bucket."""

    requests_per_second: float
    burst: Optional[float] = None

    def __post_init__(self) -> None:
        if self.requests_per_second <= 0:
            raise ValueError("requests_per_second must be > 0")
        if self.burst is None:
            # Default burst capacity: at least 1 token, otherwise equal to
            # the per-second rate (i.e. up to one second's worth of
            # requests may be issued back-to-back).
            self.burst = max(1.0, float(self.requests_per_second))
        if self.burst <= 0:
            raise ValueError("burst must be > 0")

    @classmethod
    def from_mapping(cls, value: "RateLimitConfig | Dict[str, float]") -> "RateLimitConfig":
        if isinstance(value, RateLimitConfig):
            return value
        if isinstance(value, dict):
            return cls(
                requests_per_second=float(value["requests_per_second"]),
                burst=(
                    float(value["burst"]) if value.get("burst") is not None else None
                ),
            )
        raise TypeError(f"Unsupported rate limit config type: {type(value)!r}")


class _TokenBucket:
    """A single asyncio-safe token bucket.

    Tokens refill continuously at ``requests_per_second`` up to a maximum
    of ``burst`` tokens. ``acquire`` blocks (via ``asyncio.sleep``) until a
    token is available, then consumes one. This delays excess requests
    instead of dropping them.
    """

    def __init__(self, config: RateLimitConfig, *, clock=time.monotonic):
        self._rate = float(config.requests_per_second)
        self._capacity = float(config.burst if config.burst is not None else config.requests_per_second)
        self._tokens = self._capacity
        self._clock = clock
        self._last_refill = self._clock()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = self._clock()
        elapsed = now - self._last_refill
        if elapsed > 0:
            self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
            self._last_refill = now

    async def acquire(self, tokens: float = 1.0) -> None:
        if tokens <= 0:
            raise ValueError("tokens must be > 0")
        while True:
            async with self._lock:
                self._refill()
                # A small epsilon absorbs floating-point rounding so the
                # bucket doesn't spin forever chasing an infinitesimal
                # deficit that never quite reaches zero.
                if self._tokens >= tokens - 1e-9:
                    self._tokens = max(0.0, self._tokens - tokens)
                    return
                deficit = tokens - self._tokens
                wait_time = deficit / self._rate
            # Sleep outside the lock so other coroutines can check/refill
            # (and so a long wait on one bucket never blocks other hosts,
            # which have their own bucket/lock anyway).
            await asyncio.sleep(wait_time)


class RateLimiter:
    """Per-host (or per-endpoint-key) async rate limiter.

    Parameters
    ----------
    limits:
        Mapping of key (typically a hostname, but any string identifier
        works — including "host+path" composites for per-endpoint limits)
        to a :class:`RateLimitConfig` or an equivalent dict, e.g.
        ``{"requests_per_second": 10, "burst": 20}``.
    default:
        Optional fallback config used for keys not present in ``limits``.
        If omitted, unconfigured keys are not rate limited at all (calls
        to ``acquire`` return immediately).
    """

    def __init__(
        self,
        limits: Optional[Dict[str, "RateLimitConfig | Dict[str, float]"]] = None,
        default: Optional["RateLimitConfig | Dict[str, float]"] = None,
        *,
        clock=time.monotonic,
    ):
        self._configs: Dict[str, RateLimitConfig] = {
            key: RateLimitConfig.from_mapping(value)
            for key, value in (limits or {}).items()
        }
        self._default_config = (
            RateLimitConfig.from_mapping(default) if default is not None else None
        )
        self._clock = clock
        self._buckets: Dict[str, _TokenBucket] = {}
        self._buckets_lock = asyncio.Lock()

    def configure(self, key: str, config: "RateLimitConfig | Dict[str, float]") -> None:
        """Add or replace the configuration for a key at runtime."""
        self._configs[key] = RateLimitConfig.from_mapping(config)
        self._buckets.pop(key, None)

    async def _get_bucket(self, key: str) -> Optional[_TokenBucket]:
        if key in self._buckets:
            return self._buckets[key]

        config = self._configs.get(key, self._default_config)
        if config is None:
            return None

        async with self._buckets_lock:
            # Re-check after acquiring the lock in case of a race.
            if key not in self._buckets:
                self._buckets[key] = _TokenBucket(config, clock=self._clock)
            return self._buckets[key]

    async def acquire(self, key: str, tokens: float = 1.0) -> None:
        """Block until a request slot for ``key`` is available.

        ``key`` is typically a hostname (e.g. ``"api.scryfall.com"``) but
        may be any string identifying the bucket, such as
        ``"api.scryfall.com:/cards/search"`` for per-endpoint limits.
        """
        bucket = await self._get_bucket(key)
        if bucket is None:
            return
        await bucket.acquire(tokens)

    def has_limit(self, key: str) -> bool:
        return key in self._configs or self._default_config is not None

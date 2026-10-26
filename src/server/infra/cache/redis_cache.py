"""Thread-safe Redis cache layer.

All keys live under ``cache:<namespace>:<generation>:<key>`` so the cache never
touches durable data (decks, card:*, composition store, ...). Invalidation is
always scoped to that prefix and uses SCAN (never KEYS/FLUSH*).

Values are JSON-encoded in a small envelope so a cached ``None`` is
distinguishable from a miss. The cache fails open: Redis errors are logged and
treated as misses / no-ops unless ``fail_open=False``.

Thread safety: redis-py clients are safe to share across threads; pipelines
are created per call. ``get_or_set`` uses a per-key in-process lock to avoid
stampedes within a process.
"""
from __future__ import annotations

import json
import logging
import os
import random
import threading
from typing import Any, Callable, Iterable, Optional

import redis

log = logging.getLogger(__name__)

ROOT_PREFIX = "cache"
_MISS = object()
_GEN_SUFFIX = "__gen"


def _escape_glob(s: str) -> str:
    out = []
    for ch in s:
        if ch in "*?[]\\":
            out.append("\\")
        out.append(ch)
    return "".join(out)


class RedisCache:
    def __init__(self, client=None, namespace: str = "default",
                 default_ttl: Optional[int] = None, jitter: float = 0.0,
                 fail_open: bool = True, scan_count: int = 500):
        if not namespace or ":" in namespace:
            raise ValueError("namespace must be non-empty and contain no ':'")
        self._client = client
        self._client_lock = threading.Lock()
        self.namespace = namespace
        self.default_ttl = self._check_ttl(default_ttl)
        self.jitter = max(0.0, jitter)
        self.fail_open = fail_open
        self.scan_count = scan_count
        self._locks: dict[str, list] = {}  # key -> [Lock, refcount]
        self._locks_guard = threading.Lock()

    # -- plumbing ---------------------------------------------------------
    @property
    def client(self):
        if self._client is None:
            with self._client_lock:
                if self._client is None:
                    self._client = redis.from_url(
                        os.getenv("REDIS_URL", "redis://localhost:6379/0"),
                        decode_responses=True,
                        socket_timeout=2, socket_connect_timeout=2)
        return self._client

    @staticmethod
    def _check_ttl(ttl):
        if ttl is None:
            return None
        if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or ttl <= 0:
            raise ValueError("ttl must be a positive number of seconds or None")
        return ttl

    def _guard(self, fn, default):
        try:
            return fn()
        except redis.exceptions.RedisError as e:
            if not self.fail_open:
                raise
            log.warning("redis cache error (%s): %s", self.namespace, e)
            return default

    @property
    def _gen_key(self) -> str:
        return f"{ROOT_PREFIX}:{self.namespace}:{_GEN_SUFFIX}"

    def _generation(self) -> str:
        v = self.client.get(self._gen_key)
        if isinstance(v, bytes):
            v = v.decode()
        return v or "0"

    def _key(self, key: str) -> str:
        if not isinstance(key, str) or not key:
            raise ValueError("key must be a non-empty string")
        return f"{ROOT_PREFIX}:{self.namespace}:{self._generation()}:{key}"

    def _ttl_ms(self, ttl) -> Optional[int]:
        ttl = self.default_ttl if ttl is None else self._check_ttl(ttl)
        if ttl is None:
            return None
        if self.jitter:
            ttl = ttl * (1 + random.uniform(0, self.jitter))
        return max(1, int(ttl * 1000))

    @staticmethod
    def _dump(value) -> str:
        return json.dumps({"v": value}, separators=(",", ":"))

    @staticmethod
    def _load(raw):
        if raw is None:
            return _MISS
        if isinstance(raw, bytes):
            raw = raw.decode()
        try:
            return json.loads(raw)["v"]
        except (ValueError, KeyError, TypeError):
            return _MISS

    # -- key/value --------------------------------------------------------
    def get(self, key: str, default=None):
        def op():
            k = self._key(key)
            val = self._load(self.client.get(k))
            if val is _MISS:
                return default
            return val
        return self._guard(op, default)

    def set(self, key: str, value: Any, ttl: Optional[float] = None) -> bool:
        def op():
            px = self._ttl_ms(ttl)
            self.client.set(self._key(key), self._dump(value), px=px)
            return True
        return bool(self._guard(op, False))

    def delete(self, *keys: str) -> int:
        def op():
            if not keys:
                return 0
            return int(self.client.delete(*[self._key(k) for k in keys]))
        return self._guard(op, 0)

    def exists(self, key: str) -> bool:
        return bool(self._guard(lambda: self.client.exists(self._key(key)), 0))

    def get_many(self, keys: Iterable[str]) -> dict:
        keys = list(keys)

        def op():
            if not keys:
                return {}
            raws = self.client.mget([self._key(k) for k in keys])
            out = {}
            for k, raw in zip(keys, raws):
                v = self._load(raw)
                if v is not _MISS:
                    out[k] = v
            return out
        return self._guard(op, {})

    def set_many(self, items: dict, ttl: Optional[float] = None) -> bool:
        def op():
            pipe = self.client.pipeline()  # per call: pipelines aren't thread-safe
            for k, v in items.items():
                pipe.set(self._key(k), self._dump(v), px=self._ttl_ms(ttl))
            pipe.execute()
            return True
        return bool(self._guard(op, False)) if items else True

    # -- expiration -------------------------------------------------------
    def expire(self, key: str, ttl: float) -> bool:
        ttl = self._check_ttl(ttl)
        if ttl is None:
            raise ValueError("ttl required")
        return bool(self._guard(
            lambda: self.client.pexpire(self._key(key), int(ttl * 1000)), False))

    def persist(self, key: str) -> bool:
        return bool(self._guard(lambda: self.client.persist(self._key(key)), False))

    def ttl(self, key: str) -> int:
        """Seconds remaining; -1 no expiry; -2 missing (Redis semantics)."""
        return self._guard(lambda: self.client.ttl(self._key(key)), -2)

    # -- invalidation -----------------------------------------------------
    def invalidate_prefix(self, key_prefix: str = "") -> int:
        """Delete entries in this namespace whose key starts with key_prefix."""
        def op():
            match = (f"{_escape_glob(ROOT_PREFIX)}:{_escape_glob(self.namespace)}:"
                     f"{_escape_glob(self._generation())}:{_escape_glob(key_prefix)}*")
            total, batch = 0, []
            for k in self.client.scan_iter(match=match, count=self.scan_count):
                batch.append(k)
                if len(batch) >= self.scan_count:
                    total += int(self.client.unlink(*batch))
                    batch = []
            if batch:
                total += int(self.client.unlink(*batch))
            return total
        return self._guard(op, 0)

    def clear(self) -> bool:
        """O(1) namespace invalidation: bump the generation; old keys are
        orphaned and removed by their TTL (or by invalidate_prefix first)."""
        return bool(self._guard(lambda: self.client.incr(self._gen_key), False))

    # -- cache-aside ------------------------------------------------------
    def _key_lock(self, key):
        with self._locks_guard:
            ent = self._locks.setdefault(key, [threading.Lock(), 0])
            ent[1] += 1
            return ent

    def _release(self, key, ent):
        with self._locks_guard:
            ent[1] -= 1
            if ent[1] == 0:
                self._locks.pop(key, None)

    def get_or_set(self, key: str, loader: Callable[[], Any],
                   ttl: Optional[float] = None):
        val = self.get(key, _MISS)
        if val is not _MISS:
            return val
        ent = self._key_lock(key)
        try:
            with ent[0]:
                val = self.get(key, _MISS)  # re-check after acquiring
                if val is not _MISS:
                    return val
                val = loader()
                self.set(key, val, ttl)
                return val
        finally:
            self._release(key, ent)


_default: Optional[RedisCache] = None
_default_lock = threading.Lock()


def get_cache(namespace: str = "default") -> RedisCache:
    """Process-wide cache for ``namespace`` (uses REDIS_URL)."""
    global _default
    with _default_lock:
        if _default is None or _default.namespace != namespace:
            _default = RedisCache(namespace=namespace)
        return _default


try:  # adapter onto the storage-neutral Cache interface
    from src.server.domain.analytics.repository_interfaces import Cache as _CacheABC
except Exception:  # pragma: no cover
    _CacheABC = None

if _CacheABC is not None:
    class RedisCacheAdapter(_CacheABC):
        """Expose a RedisCache through the ``Cache`` ABC (get/put/delete)."""

        def __init__(self, cache: "RedisCache"):
            self._c = cache

        def get(self, key):
            return self._c.get(key)

        def put(self, key, value, ttl_seconds=None):
            self._c.set(key, value, ttl=ttl_seconds)

        def delete(self, key):
            return self._c.delete(key) > 0

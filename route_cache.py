"""Opt-in response cache middleware for read-only JSON routes.

Only GET 200 responses on allow-listed path prefixes are cached; any successful
mutating request under an invalidation prefix bumps the namespace generation
(O(1) via RedisCache.clear). Fails open: any cache error falls through to the
handler. Disable with ROUTE_CACHE_ENABLED=0; TTL via ROUTE_CACHE_TTL_SECONDS.
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Callable, Sequence

from fastapi import Request
from starlette.responses import Response

from redis_cache import RedisCache

log = logging.getLogger(__name__)

NAMESPACE = "routes"
CACHED_PATHS = ("/api/composition/presets",)
INVALIDATE_PREFIXES = ("/api/composition/", "/api/decks/")
SAFE = ("GET", "HEAD", "OPTIONS")


def enabled() -> bool:
    return os.getenv("ROUTE_CACHE_ENABLED", "1").strip().lower() not in ("0", "false", "no", "off")


def ttl() -> int:
    try:
        return max(1, int(os.getenv("ROUTE_CACHE_TTL_SECONDS", "30")))
    except ValueError:
        return 30


def make_route_cache_middleware(get_client: Callable[[], object],
                                cached_paths: Sequence[str] = CACHED_PATHS,
                                invalidate_prefixes: Sequence[str] = INVALIDATE_PREFIXES):
    def _cache():
        return RedisCache(client=get_client(), namespace=NAMESPACE, default_ttl=ttl())

    def _cacheable(request: Request) -> bool:
        p = request.url.path
        return request.method == "GET" and any(p == c or p.startswith(c + "/") for c in cached_paths)

    async def route_cache_middleware(request: Request, call_next):
        if not enabled():
            return await call_next(request)
        if _cacheable(request):
            key = request.url.path + "?" + "&".join(sorted(request.url.query.split("&"))) if request.url.query else request.url.path
            try:
                hit = _cache().get(key)
            except Exception:  # noqa: BLE001
                hit = None
            if isinstance(hit, dict) and "body" in hit:
                return Response(content=base64.b64decode(hit["body"]), status_code=hit["status"],
                                media_type=hit.get("ct"), headers={"X-Route-Cache": "HIT"})
            resp = await call_next(request)
            if resp.status_code != 200 or "set-cookie" in resp.headers:
                return resp
            body = b"".join([chunk async for chunk in resp.body_iterator])
            try:
                _cache().set(key, {"status": 200, "ct": resp.headers.get("content-type"),
                                   "body": base64.b64encode(body).decode()})
            except Exception:  # noqa: BLE001
                log.debug("route cache store failed", exc_info=True)
            return Response(content=body, status_code=200, media_type=resp.headers.get("content-type"),
                            headers={"X-Route-Cache": "MISS"})
        resp = await call_next(request)
        if request.method not in SAFE and resp.status_code < 400 and \
                any(request.url.path.startswith(p) for p in invalidate_prefixes):
            try:
                _cache().clear()
            except Exception:  # noqa: BLE001
                log.debug("route cache invalidate failed", exc_info=True)
        return resp

    return route_cache_middleware

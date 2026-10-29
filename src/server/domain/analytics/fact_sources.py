"""Live, cached fetchers for the fact pack (Scryfall, Commander Spellbook, EDHREC, Commander Salt).

Behaviour of each source (endpoints, limits, headers) is documented with links in
``docs/reviewer/EXTERNAL_APIS.md``. Every fetcher raises on failure; ``build_fact_pack``
turns that into an explicit ``unavailable`` marker. CI never calls these: tests inject
recorded fixtures.

Cache TTLs (hours) come from env, same style as ``EDHREC_MAX_AGE_DAYS`` /
``SPELLBOOK_MAX_AGE_DAYS``:

    FACT_PACK_SCRYFALL_TTL_HOURS   default 24   (Scryfall: prices update once a day)
    FACT_PACK_SPELLBOOK_TTL_HOURS  default 24
    FACT_PACK_EDHREC_TTL_HOURS     default 24   (unofficial endpoint: cache aggressively)
    FACT_PACK_SALT_TTL_HOURS       default 24

The cache is in-process and pluggable (``TTLCache(store=...)``); persistent caching of
provider payloads belongs in Postgres (DECISIONS binding section), which a later card wires in.
"""
from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

import requests

from src.server.domain.analytics.fact_sections import _front

SCRYFALL_UA = {"User-Agent": "commander-lab/1.0 (+https://github.com/andrewgari/commander-lab)",
               "Accept": "application/json"}
SCRYFALL_COLLECTION = "https://api.scryfall.com/cards/collection"
SCRYFALL_SEARCH = "https://api.scryfall.com/cards/search"
SPELLBOOK_URL = os.getenv("SPELLBOOK_BASE_URL", "https://backend.commanderspellbook.com").rstrip("/") + "/find-my-combos"
SCRYFALL_MIN_INTERVAL = 0.55  # Scryfall: /cards/search and /cards/collection are limited to 2/s
DEFAULT_TTL_HOURS = 24.0


def ttl_seconds(name: str, default_hours: float = DEFAULT_TTL_HOURS) -> float:
    """TTL from env var `name` (hours). Bad/negative values fall back to the default."""
    try:
        v = float(os.getenv(name, default_hours))
    except (TypeError, ValueError):
        v = default_hours
    return (v if v >= 0 else default_hours) * 3600.0


class TTLCache:
    def __init__(self, clock: Callable[[], float] = time.time):
        self._clock = clock
        self._data: dict[Any, tuple[float, Any]] = {}
        self._lock = threading.Lock()

    def get_or_fetch(self, key: Any, ttl: float, fetch: Callable[[], Any], refresh: bool = False) -> Any:
        now = self._clock()
        with self._lock:
            hit = self._data.get(key)
        if hit and not refresh and now - hit[0] < ttl:
            return hit[1]
        value = fetch()  # exceptions propagate: failures are never cached
        with self._lock:
            self._data[key] = (self._clock(), value)
        return value

    def clear(self) -> None:
        with self._lock:
            self._data.clear()


CACHE = TTLCache()
_last_scryfall = 0.0
_throttle_lock = threading.Lock()


def _throttle_scryfall() -> None:
    global _last_scryfall
    with _throttle_lock:
        wait = SCRYFALL_MIN_INTERVAL - (time.monotonic() - _last_scryfall)
        if wait > 0:
            time.sleep(wait)
        _last_scryfall = time.monotonic()


def scryfall_collection(names: list[str], session: Any = requests) -> dict[str, dict]:
    """{name_lower: card} via POST /cards/collection, 75 names per request."""
    out: dict[str, dict] = {}
    for i in range(0, len(names), 75):
        _throttle_scryfall()
        resp = session.post(SCRYFALL_COLLECTION, headers=SCRYFALL_UA, timeout=20,
                            json={"identifiers": [{"name": _front(n).strip()} for n in names[i:i + 75]]})
        resp.raise_for_status()
        for card in resp.json().get("data", []):
            full = (card.get("name") or "").lower()
            out.setdefault(full, card)
            for face in full.split(" // "):
                out.setdefault(face.strip(), card)
    return out


def scryfall_search(query: str, *, limit: int = 40, session: Any = requests) -> list[dict]:
    """First page(s) of GET /cards/search ordered by edhrec rank, at most `limit` cards."""
    out: list[dict] = []
    url, params = SCRYFALL_SEARCH, {"q": query, "order": "edhrec"}
    while url and len(out) < limit:
        _throttle_scryfall()
        resp = session.get(url, headers=SCRYFALL_UA, params=params, timeout=20)
        if resp.status_code == 404:  # Scryfall: 404 = no results
            return out
        resp.raise_for_status()
        page = resp.json()
        out.extend(page.get("data", []))
        url, params = (page.get("next_page") if page.get("has_more") else None), None
    return out[:limit]


def _commander_names(deck: Any) -> list[str]:
    group = deck.get("commanders") if isinstance(deck, Mapping) else getattr(deck, "commanders", None)
    out = []
    for e in group or []:
        n = e if isinstance(e, str) else (e.get("name") if isinstance(e, Mapping) else getattr(e, "name", None))
        if isinstance(n, str) and n.strip():
            out.append(n)
    return out


def _names(deck: Any) -> list[str]:
    from src.server.domain.analytics.fact_pack import _names as n

    return n(deck)


def default_fetchers(refresh: bool = False, cache: TTLCache | None = None) -> dict[str, Callable[..., Any]]:
    """Cached live fetchers (deck-level). `scryfall_batch(names)` -> {name_lower: card}."""
    cache = cache or CACHE

    def scryfall_batch(names: list[str]):
        ttl = ttl_seconds("FACT_PACK_SCRYFALL_TTL_HOURS")
        return cache.get_or_fetch(("scryfall", tuple(sorted(names))), ttl, lambda: scryfall_collection(names), refresh)

    def edhrec(deck: Any):
        from src.server.domain.analytics.providers.edhrec import EDHRecClient, commander_slug

        cmd = _commander_names(deck)
        return cache.get_or_fetch(("edhrec", commander_slug(cmd)), ttl_seconds("FACT_PACK_EDHREC_TTL_HOURS"),
                                  lambda: EDHRecClient().get_commander_page(cmd), refresh)

    def spellbook(deck: Any):
        cmd = _commander_names(deck)
        main = [n for n in _names(deck) if n not in cmd]

        def fetch():
            resp = requests.post(SPELLBOOK_URL, timeout=60, headers={"User-Agent": SCRYFALL_UA["User-Agent"]},
                                 json={"commanders": [{"card": c} for c in cmd], "main": [{"card": c} for c in main]})
            resp.raise_for_status()
            return resp.json()

        return cache.get_or_fetch(("spellbook", tuple(sorted(cmd)), tuple(sorted(main))),
                                  ttl_seconds("FACT_PACK_SPELLBOOK_TTL_HOURS"), fetch, refresh)

    def salt(deck: Any):
        from src.server.domain.analytics.providers.commandersalt import CommanderSaltClient, resolve_source_url

        ref = deck.get("registry_id") or deck.get("id") if isinstance(deck, Mapping) else None
        if not ref:
            raise RuntimeError("deck has no registry id / source url for Commander Salt")

        def fetch():
            raw = CommanderSaltClient().get_deck(resolve_source_url(str(ref)))
            cards = {c["name"]: c.get("salt") for c in (raw.get("cards") or {}).values() if isinstance(c, dict) and c.get("name")}
            nums = [v for v in cards.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
            return {"salt_score": sum(nums) if nums else None, "cards": cards}

        return cache.get_or_fetch(("salt", str(ref)), ttl_seconds("FACT_PACK_SALT_TTL_HOURS"), fetch, refresh)

    return {"scryfall_batch": scryfall_batch, "edhrec": edhrec, "spellbook": spellbook, "salt": salt}

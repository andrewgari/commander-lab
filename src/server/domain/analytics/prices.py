"""
Per-card USD prices from Scryfall (AN-5). Used by the Journal (deck total,
cards over the per-card caps) and later by the Tuner (AN-6).

Redis: price:card:{name} -> {"usd": float|null, "at": epoch seconds}
Prices are refreshed when older than MAX_AGE (one day). Unknown cards are
cached as null too, so they don't cost a request on every page load. If
Scryfall is down, the last cached value is used (even when stale).

Price = Scryfall `usd` (nonfoil), falling back to `usd_foil`, then
`usd_etched`, for Scryfall's default printing; when that printing has no
price at all, the cheapest priced printing. Owned cards still have a price;
"owned costs $0" is a Tuner rule, not a data rule.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Callable, Dict, Iterable, List, Mapping, Optional

log = logging.getLogger("commander_lab.prices")

PREFIX = "price:card:"
MAX_AGE = 24 * 3600
SCRYFALL_COLLECTION = "https://api.scryfall.com/cards/collection"
SCRYFALL_SEARCH = "https://api.scryfall.com/cards/search"
HEADERS = {"User-Agent": "commander-lab/1.0 (prices)", "Accept": "application/json"}

Fetch = Callable[[List[str]], Mapping[str, Optional[float]]]


def usd_of(card: Mapping) -> Optional[float]:
    p = (card or {}).get("prices") or {}
    for k in ("usd", "usd_foil", "usd_etched"):
        v = p.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def scryfall_fetch(names: List[str], session=None, timeout: float = 30.0) -> Dict[str, Optional[float]]:
    """POST /cards/collection in chunks of 75. Matches by full name or front face."""
    import requests

    http = session or requests.Session()
    out: Dict[str, Optional[float]] = {}
    for i in range(0, len(names), 75):
        chunk = names[i : i + 75]
        resp = http.post(SCRYFALL_COLLECTION, headers=HEADERS, timeout=timeout,
                         json={"identifiers": [{"name": n} for n in chunk]})
        resp.raise_for_status()
        by_lower: Dict[str, Optional[float]] = {}
        for c in resp.json().get("data", []):
            name = c.get("name", "")
            by_lower[name.lower()] = usd_of(c)
            for face in name.split(" // "):
                by_lower.setdefault(face.lower(), usd_of(c))
        for n in chunk:
            key = n.lower()
            out[n] = by_lower.get(key, by_lower.get(n.split(" // ")[0].lower()))
        if i + 75 < len(names):
            time.sleep(0.1)
    # The default printing can be unpriced (new or promo sets): use the
    # cheapest priced printing instead. One search per such card.
    for n in [n for n in names if out.get(n) is None]:
        try:
            resp = http.get(SCRYFALL_SEARCH, headers=HEADERS, timeout=timeout,
                            params={"q": f'!"{n.split(" // ")[0]}" (usd>0 or usdfoil>0)', "unique": "prints",
                                    "order": "usd", "dir": "asc"})
            if resp.status_code == 200:
                for c in resp.json().get("data", []):
                    v = usd_of(c)
                    if v is not None:
                        out[n] = v
                        break
        except Exception as exc:  # noqa: BLE001
            log.info("price search failed for %s: %s", n, exc)
        time.sleep(0.1)
    return out


def card_prices(r, names: Iterable[str], fetch: Optional[Fetch] = None,
                now: Optional[float] = None) -> Dict[str, Optional[float]]:
    names = sorted({n for n in names if n})
    if not names:
        return {}
    now = time.time() if now is None else now
    raws = r.mget([PREFIX + n for n in names])
    cached: Dict[str, dict] = {}
    for n, raw in zip(names, raws):
        if not raw:
            continue
        try:
            cached[n] = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
    stale = [n for n in names if n not in cached or now - (cached[n].get("at") or 0) > MAX_AGE]
    if stale:
        try:
            got = (fetch or scryfall_fetch)(stale)
        except Exception as exc:  # noqa: BLE001 - prices are context, never fatal
            log.warning("price fetch failed for %d cards: %s", len(stale), exc)
            got = None
        if got is not None:
            for n in stale:
                rec = {"usd": got.get(n), "at": now}
                cached[n] = rec
                r.set(PREFIX + n, json.dumps(rec))
    return {n: (cached.get(n) or {}).get("usd") for n in names}


def deck_entries(deck: Mapping) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for c in deck.get("cards") or []:
        name = c.get("name")
        if name:
            out[name] = out.get(name, 0) + int(c.get("quantity") or 1)
    return out


def deck_total(deck: Mapping, prices: Mapping[str, Optional[float]]) -> dict:
    total, unpriced = 0.0, 0
    for name, qty in deck_entries(deck).items():
        p = prices.get(name)
        if p is None:
            unpriced += qty
        else:
            total += p * qty
    return {"price": round(total, 2), "unpriced": unpriced}

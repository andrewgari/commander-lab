"""
EDHREC data for card evaluation: slot-aware commander page, cached in Redis.

Reuses analytics.providers.edhrec.EDHRecClient (throttling, retry/backoff on 429/5xx, typed
errors). Unlike analytics.edhrec_inclusion this keeps the cardlist tag ("slot") of each card.

Cache `card_eval:edhrec:{slug}`: {fetched_at, missing, decks_sampled, cards: {name_key: {...}}}.
Fresh (< EDHREC_MAX_AGE_DAYS, default 7) entries are served without a request. A 404 is
negative-cached for one day. On a fetch failure a stale copy is served (stale=True); with no
copy the error propagates as EDHRecError for the caller to degrade on.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Sequence

from src.server.domain.analytics.edhrec_inclusion import _max_age, name_key
from src.server.domain.analytics.providers.edhrec import EDHRecClient, EDHRecError, EDHRecNotFoundError, commander_slug

CACHE_PREFIX = "card_eval:edhrec:"
MISSING_TTL = timedelta(days=1)


class CommanderPageMissing(EDHRecError):
    """EDHREC has no page for this commander (404, possibly from the negative cache)."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def parse_page(raw: dict) -> dict:
    jd = (raw or {}).get("container", {}).get("json_dict") if isinstance(raw, dict) else None
    if not isinstance(jd, dict):
        raise EDHRecError("unexpected EDHREC response shape")
    sampled = int((jd.get("card") or {}).get("num_decks") or 0)
    cards: Dict[str, dict] = {}
    for cl in jd.get("cardlists") or []:
        tag = str(cl.get("tag") or "").lower()
        for v in cl.get("cardviews") or []:
            name = v.get("name")
            try:
                num, pot = float(v.get("num_decks")), float(v.get("potential_decks"))
            except (TypeError, ValueError):
                continue
            if not name or pot <= 0:
                continue
            sampled = max(sampled, int(pot))
            inc = round(max(0.0, min(1.0, num / pot)), 4)
            syn = v.get("synergy")
            syn = float(syn) if isinstance(syn, (int, float)) else None
            k = name_key(name)
            cur = cards.get(k)
            if cur is None:
                cards[k] = {"name": name, "inclusion": inc, "synergy": syn, "num_decks": int(num), "tags": [tag]}
            else:
                if tag not in cur["tags"]:
                    cur["tags"].append(tag)
                if inc > cur["inclusion"]:
                    cur.update(inclusion=inc, num_decks=int(num))
                if syn is not None and (cur["synergy"] is None or syn > cur["synergy"]):
                    cur["synergy"] = syn
    return {"decks_sampled": sampled, "cards": cards}


def get_page(r, commanders: Sequence[str], client: Optional[EDHRecClient] = None) -> dict:
    """Parsed page {slug, fetched_at, decks_sampled, cards, stale}. Raises CommanderPageMissing
    (no page) or EDHRecError (fetch failed and nothing cached)."""
    names = [c for c in commanders if c]
    slug = commander_slug(names)
    if not slug:
        raise EDHRecError("deck has no commander")
    key = CACHE_PREFIX + slug
    cached = None
    try:
        raw = r.get(key)
        cached = json.loads(raw) if raw else None
    except (ValueError, TypeError):
        cached = None
    if isinstance(cached, dict):
        try:
            age = _now() - datetime.fromisoformat(cached["fetched_at"])
        except (KeyError, ValueError):
            age = timedelta.max
        if cached.get("missing"):
            if age < MISSING_TTL:
                raise CommanderPageMissing(f"EDHREC has no page for {slug}")
            cached = None
        elif age < _max_age():
            return {**cached, "slug": slug, "stale": False}
    else:
        cached = None
    try:
        page = parse_page((client or EDHRecClient()).get_commander_page(names))
    except EDHRecNotFoundError:
        r.set(key, json.dumps({"fetched_at": _now().isoformat(timespec="seconds"), "missing": True}))
        raise CommanderPageMissing(f"EDHREC has no page for {slug}")
    except EDHRecError as exc:
        if cached:
            return {**cached, "slug": slug, "stale": True, "error": str(exc)[:200]}
        raise
    rec = {"fetched_at": _now().isoformat(timespec="seconds"), **page}
    r.set(key, json.dumps(rec))
    return {**rec, "slug": slug, "stale": False}


def lookup(page: dict, card_name: str) -> Optional[dict]:
    return (page.get("cards") or {}).get(name_key(card_name))


def slot_peers(page: dict, entry: dict, limit: int = 8) -> List[str]:
    """Top cards (by synergy then inclusion) sharing the entry's first EDHREC slot tag."""
    tags = set(entry.get("tags") or [])
    peers = [c for c in page["cards"].values() if tags & set(c.get("tags") or []) and c["name"] != entry["name"]]
    peers.sort(key=lambda c: (-(c.get("synergy") or 0.0), -c["inclusion"], c["name"]))
    return [c["name"] for c in peers[:limit]]

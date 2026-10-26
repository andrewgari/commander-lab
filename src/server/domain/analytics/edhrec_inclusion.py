"""
EDHREC inclusion per card (AN-3), from the commander's EDHREC page.

inclusion = num_decks / potential_decks for every card EDHREC lists on the
commander page (json.edhrec.com/pages/commanders/{slug}.json). Cards the
page doesn't list get inclusion None / listed False: EDHREC only lists
cards above a small threshold, so "not listed" means "very rare with this
commander" and the weak-performer score treats it as 0%.

Cache: edhrec:commander:{slug} (JSON {fetched_at, decks_sampled, cards:
{name_key: {name, inclusion, synergy, num_decks}}}, no TTL). Entries younger
than EDHREC_MAX_AGE_DAYS (default 7) are served without a request; older
ones are refetched, and if EDHREC is down the stale copy is served with
stale: true.

Histogram buckets (UX_SPEC §3.1.9): <5, 5–14, 15–29, 30–49, 50–74, 75+ (%),
over the deck's non-land cards.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Mapping, Optional

from .providers.edhrec import EDHRecClient, EDHRecError, commander_slug

CACHE_PREFIX = "edhrec:commander:"
DEFAULT_MAX_AGE_DAYS = 7
BASICS = {"Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes", "Snow-Covered Plains",
          "Snow-Covered Island", "Snow-Covered Swamp", "Snow-Covered Mountain", "Snow-Covered Forest"}
BUCKETS = (("<5", 0.0, 0.05), ("5–14", 0.05, 0.15), ("15–29", 0.15, 0.30), ("30–49", 0.30, 0.50),
           ("50–74", 0.50, 0.75), ("75+", 0.75, 1.01))

__all__ = ["EDHRecError", "deck_inclusion", "cache_key", "bucket_of", "BUCKETS"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


def name_key(name: str) -> str:
    return (name or "").split(" // ")[0].strip().lower()


def cache_key(slug: str) -> str:
    return CACHE_PREFIX + slug


def bucket_of(inclusion: Optional[float]) -> str:
    v = inclusion or 0.0
    for label, lo, hi in BUCKETS:
        if lo <= v < hi:
            return label
    return BUCKETS[-1][0]


def parse_page(raw: dict) -> dict:
    jd = (raw or {}).get("container", {}).get("json_dict") or {}
    card = jd.get("card") or {}
    cards: Dict[str, dict] = {}
    sampled = card.get("num_decks") or 0
    for cl in jd.get("cardlists") or []:
        for v in cl.get("cardviews") or []:
            name = v.get("name")
            try:
                num, pot = float(v.get("num_decks")), float(v.get("potential_decks"))
            except (TypeError, ValueError):
                continue
            if not name or pot <= 0:
                continue
            k = name_key(name)
            inc = max(0.0, min(1.0, num / pot))
            if k not in cards or inc > cards[k]["inclusion"]:
                cards[k] = {"name": name, "inclusion": round(inc, 4), "num_decks": int(num),
                            "synergy": v.get("synergy"), "salt": v.get("salt")}
            sampled = max(sampled, int(pot))
    return {"decks_sampled": int(sampled), "commander_salt": card.get("salt"), "cards": cards}


def _max_age() -> timedelta:
    try:
        return timedelta(days=float(os.getenv("EDHREC_MAX_AGE_DAYS", DEFAULT_MAX_AGE_DAYS)))
    except ValueError:
        return timedelta(days=DEFAULT_MAX_AGE_DAYS)


def commander_page(r, commanders: List[str], client=None, refresh: bool = False) -> dict:
    """Cached, parsed commander page: {slug, fetched_at, decks_sampled, cards, stale?, error?}."""
    slug = commander_slug(commanders)
    if not slug:
        raise EDHRecError("deck has no commander")
    key = cache_key(slug)
    cached = None
    raw = r.get(key)
    if raw:
        try:
            cached = json.loads(raw)
        except json.JSONDecodeError:
            cached = None
    if cached and not refresh:
        try:
            age = _now() - datetime.fromisoformat(cached["fetched_at"])
        except (KeyError, ValueError):
            age = timedelta.max
        if age < _max_age():
            return {**cached, "cached": True, "stale": False}
    client = client or EDHRecClient()
    try:
        page = parse_page(client.get_commander_page(commanders))
    except EDHRecError as exc:
        if cached:
            return {**cached, "cached": True, "stale": True, "error": str(exc)[:200]}
        raise
    rec = {"slug": slug, "fetched_at": _now().isoformat(timespec="seconds"), **page}
    r.set(key, json.dumps(rec))
    return {**rec, "cached": False, "stale": False}


def _is_land(name: str, card_texts: Optional[Mapping[str, dict]]) -> bool:
    if name in BASICS:
        return True
    c = (card_texts or {}).get(name) or {}
    tl = (c.get("type_line") or "").split("//")[0]
    return "Land" in tl and "Creature" not in tl


def deck_inclusion(r, deck: dict, client=None, card_texts: Optional[Mapping[str, dict]] = None,
                   refresh: bool = False) -> dict:
    commanders = [c for c in deck.get("commanders") or [] if c]
    base = {"commanders": commanders, "commander_slug": commander_slug(commanders)}
    try:
        page = commander_page(r, commanders, client=client, refresh=refresh)
    except EDHRecError as exc:
        return {**base, "status": "unavailable", "error": str(exc)[:200], "decks_sampled": None,
                "per_card": [], "buckets": [], "rarest": []}
    cmd = set(commanders)
    names = sorted({c.get("name") for c in deck.get("cards") or [] if c.get("name")} - cmd - BASICS)
    per_card = []
    for n in names:
        hit = page["cards"].get(name_key(n))
        per_card.append({"name": n, "inclusion": hit["inclusion"] if hit else None, "listed": bool(hit),
                         "synergy": round(hit["synergy"], 4) if hit and hit.get("synergy") is not None else None,
                         "land": _is_land(n, card_texts)})
    nonland = [row for row in per_card if not row["land"]]
    counts = {label: 0 for label, _, _ in BUCKETS}
    for row in nonland:
        counts[bucket_of(row["inclusion"])] += 1
    buckets = [{"label": label, "count": counts[label]} for label, _, _ in BUCKETS]
    rarest = sorted(nonland, key=lambda row: (row["inclusion"] or 0.0, row["name"]))[:4]
    listed = [row["inclusion"] for row in nonland if row["inclusion"] is not None]
    return {**base, "status": "ok", "decks_sampled": page.get("decks_sampled"),
            "fetched_at": page.get("fetched_at"), "cached": page.get("cached"), "stale": page.get("stale"),
            "error": page.get("error"), "per_card": per_card, "buckets": buckets,
            "rarest": [{"name": row["name"], "inclusion": row["inclusion"]} for row in rarest],
            "overlap_pct": round(100 * sum(listed) / len(nonland)) if nonland else None,
            "source": f"https://edhrec.com/commanders/{base['commander_slug']}"}

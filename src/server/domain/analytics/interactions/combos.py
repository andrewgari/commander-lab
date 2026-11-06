"""
Commander Spellbook integration (AN-2): combos fully in a deck, and combos
one card away with the missing card flagged when it's owned.

Data source: POST https://backend.commanderspellbook.com/find-my-combos
with {"commanders": [{"card": name}], "main": [{"card": name, "quantity": n}]}.
The response buckets variants into `included` (every card in the list),
`almostIncluded` (one card missing, within the commander's identity),
`almostIncludedByAddingColors` (one card missing, outside the identity), and
two "by changing commanders" buckets that we report as counts only.

Cache: `spellbook:fmc:{sha1(decklist)}` holds the normalized result with a
`fetched_at` stamp. A fresh entry (younger than SPELLBOOK_MAX_AGE_DAYS,
default 7) is served without a request; an older one is refetched, and if
Spellbook is down the stale entry is served with `stale: true`. The cache key
is the sorted decklist, so any card change refetches. No TTL on the key: the
stale copy is the fallback.

Owned flags come from instance:* records (in_collection / in_mail /
in_deck), the same ownership rule as the AN-1 collection graph.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, Iterable, List, Optional

BASE_URL = os.getenv("SPELLBOOK_BASE_URL", "https://backend.commanderspellbook.com")
SITE_URL = "https://commanderspellbook.com/combo/{id}/"
CACHE_PREFIX = "spellbook:fmc:"
DEFAULT_MAX_AGE_DAYS = 7
MAX_PAGES = 10
OWNED_STATUSES = ("in_collection", "in_mail", "in_deck")
FREE_STATUSES = ("in_collection", "in_mail")


class SpellbookError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc)


def name_key(name: str) -> str:
    """Case-insensitive match key; double-faced cards match on the front face."""
    return (name or "").split(" // ")[0].strip().lower()


def _default_post(url, json=None, headers=None, timeout=None):
    import requests

    return requests.post(url, json=json, headers=headers, timeout=timeout)


def normalize_variant(v: dict) -> dict:
    uses = v.get("uses") or []
    cards = [{
        "name": (u.get("card") or {}).get("name"),
        "oracle_id": (u.get("card") or {}).get("oracleId"),
        "type_line": (u.get("card") or {}).get("typeLine"),
        "zones": u.get("zoneLocations") or [],
        "must_be_commander": bool(u.get("mustBeCommander")),
        "quantity": u.get("quantity") or 1,
    } for u in uses]
    produces = [(p.get("feature") or {}).get("name") for p in v.get("produces") or []]
    requires = [{"template": (t.get("template") or {}).get("name"), "zones": t.get("zoneLocations") or [],
                 "quantity": t.get("quantity") or 1} for t in v.get("requires") or []]
    try:
        popularity = int(v.get("popularity") or 0)
    except (TypeError, ValueError):
        popularity = 0
    return {
        "id": v.get("id"),
        "url": SITE_URL.format(id=v.get("id")),
        "cards": cards,
        "card_names": [c["name"] for c in cards if c["name"]],
        "requires": requires,
        "produces": [p for p in produces if p],
        "description": v.get("description") or "",
        "prerequisites": {
            "easy": v.get("easyPrerequisites") or "",
            "notable": v.get("notablePrerequisites") or "",
            "mana": v.get("manaNeeded") or "",
            "mana_value": v.get("manaValueNeeded") or 0,
        },
        "identity": v.get("identity") or "",
        "bracket_tag": v.get("bracketTag"),
        "popularity": popularity,
        "salt": v.get("salt"),
        "prices": v.get("prices") or {},
    }


_BUCKETS = {
    "included": "included",
    "almostIncluded": "almost_included",
    "almostIncludedByAddingColors": "almost_included_off_identity",
    "includedByChangingCommanders": "included_changing_commanders",
    "almostIncludedByChangingCommanders": "almost_included_changing_commanders",
    "almostIncludedByAddingColorsAndChangingCommanders": "almost_included_off_identity_changing_commanders",
}
_COUNT_ONLY = ("included_changing_commanders", "almost_included_changing_commanders",
               "almost_included_off_identity_changing_commanders")


def decklist_key(commanders: Iterable[str], main: Iterable[str]) -> str:
    cmd = sorted({name_key(c) for c in commanders if c})
    rest = sorted({name_key(c) for c in main if c} - set(cmd))
    digest = hashlib.sha1(json.dumps([cmd, rest]).encode()).hexdigest()
    return CACHE_PREFIX + digest


class SpellbookClient:
    def __init__(self, r, http_post: Optional[Callable] = None, base_url: str = BASE_URL,
                 max_age_days: Optional[float] = None, timeout: float = 60.0):
        self.r = r
        self._post = http_post or _default_post
        self.base_url = base_url.rstrip("/")
        if max_age_days is None:
            max_age_days = float(os.getenv("SPELLBOOK_MAX_AGE_DAYS", DEFAULT_MAX_AGE_DAYS))
        self.max_age = timedelta(days=max_age_days)
        self.timeout = timeout
        self.requests = 0

    def _fetch(self, commanders: List[str], main: List[str]) -> dict:
        cmd_keys = {name_key(c) for c in commanders}
        body = {
            "commanders": [{"card": c} for c in commanders],
            "main": [{"card": c} for c in main if name_key(c) not in cmd_keys],
        }
        out = {v: [] for v in _BUCKETS.values()}
        identity = ""
        url = f"{self.base_url}/find-my-combos"
        for _ in range(MAX_PAGES):
            try:
                self.requests += 1
                resp = self._post(url, json=body, headers={"Content-Type": "application/json",
                                                            "User-Agent": "commander-lab"}, timeout=self.timeout)
                resp.raise_for_status()
                page = resp.json()
            except Exception as exc:  # noqa: BLE001
                raise SpellbookError(f"spellbook request failed: {exc}") from exc
            res = page.get("results") or {}
            identity = res.get("identity") or identity
            for src, dst in _BUCKETS.items():
                out[dst].extend(normalize_variant(v) for v in res.get(src) or [])
            url = page.get("next")
            if not url:
                break
        out["identity"] = identity
        return out

    def find_my_combos(self, commanders: Iterable[str], main: Iterable[str], refresh: bool = False) -> dict:
        commanders = sorted({c for c in commanders if c})
        main = sorted({c for c in main if c})
        key = decklist_key(commanders, main)
        cached = None
        raw = self.r.get(key)
        if raw:
            try:
                cached = json.loads(raw)
            except json.JSONDecodeError:
                cached = None
        if cached and not refresh:
            try:
                age = _now() - datetime.fromisoformat(cached["fetched_at"])
            except (KeyError, ValueError):
                age = self.max_age + timedelta(seconds=1)
            if age <= self.max_age:
                return {**cached, "cached": True, "stale": False}
        try:
            data = self._fetch(commanders, main)
        except SpellbookError as exc:
            if cached:
                return {**cached, "cached": True, "stale": True, "error": str(exc)[:300]}
            raise
        rec = {**data, "fetched_at": _now().isoformat(timespec="seconds"), "cache_key": key}
        self.r.set(key, json.dumps(rec))  # no TTL: an old copy is the outage fallback
        return {**rec, "cached": False, "stale": False}


# ---------------------------------------------------------------------------
# Ownership
# ---------------------------------------------------------------------------


def owned_index(r) -> Dict[str, dict]:
    """{name_key: {name, copies, available, decks}} for owned instances.
    `available` = copies not currently in a deck (in_collection / in_mail)."""
    keys = list(r.keys("instance:*"))
    out: Dict[str, dict] = {}
    if not keys:
        return out
    mget = r.mget if hasattr(r, "mget") else (lambda ks: [r.get(k) for k in ks])
    for i in range(0, len(keys), 1000):
        for raw in mget(keys[i:i + 1000]):
            if not raw:
                continue
            try:
                inst = json.loads(raw)
            except json.JSONDecodeError:
                continue
            status = inst.get("ownership_status")
            name = inst.get("card_name")
            if status not in OWNED_STATUSES or not name:
                continue
            o = out.setdefault(name_key(name), {"name": name, "copies": 0, "available": 0, "decks": []})
            o["copies"] += 1
            if status in FREE_STATUSES:
                o["available"] += 1
            elif inst.get("deck_name") and inst["deck_name"] not in o["decks"]:
                o["decks"].append(inst["deck_name"])
    return out


# ---------------------------------------------------------------------------
# Deck view
# ---------------------------------------------------------------------------


def _deck_names(deck: dict) -> List[str]:
    names = {c.get("name") for c in deck.get("cards") or [] if c.get("name")}
    names.update(n for n in deck.get("commanders") or [] if n)
    return sorted(names)


def _annotate_missing(combo: dict, in_deck: set, owned: Dict[str, dict]) -> dict:
    missing = [n for n in combo["card_names"] if name_key(n) not in in_deck]
    owned_rec = {n: {k: owned[name_key(n)][k] for k in ("copies", "available", "decks")}
                 for n in missing if name_key(n) in owned}
    return {**combo, "missing": missing, "missing_owned": bool(missing) and len(owned_rec) == len(missing),
            "owned": owned_rec}


def _sort_one_away(combos: List[dict]) -> List[dict]:
    return sorted(combos, key=lambda c: (not c["missing_owned"],
                                         -max([o["available"] for o in c["owned"].values()] or [0]),
                                         -c.get("popularity", 0), c.get("id") or ""))


def deck_combos(r, deck: dict, client: Optional[SpellbookClient] = None, refresh: bool = False,
                owned_only: bool = False, limit: Optional[int] = None) -> dict:
    client = client or SpellbookClient(r)
    names = _deck_names(deck)
    commanders = [c for c in deck.get("commanders") or [] if c]
    data = client.find_my_combos(commanders, names, refresh=refresh)
    in_deck_keys = {name_key(n) for n in names}
    owned = owned_index(r)

    included = sorted(data.get("included") or [], key=lambda c: (-c.get("popularity", 0), c.get("id") or ""))
    one_away = _sort_one_away([_annotate_missing(c, in_deck_keys, owned) for c in data.get("almost_included") or []])
    off = _sort_one_away([_annotate_missing(c, in_deck_keys, owned)
                          for c in data.get("almost_included_off_identity") or []])

    counts = {
        "in_deck": len(included),
        "one_away": len(one_away),
        "one_away_owned": sum(1 for c in one_away if c["missing_owned"]),
        "one_away_owned_available": sum(1 for c in one_away if c["missing_owned"]
                                        and all(o["available"] > 0 for o in c["owned"].values())),
        "off_identity_one_away": len(off),
        "off_identity_one_away_owned": sum(1 for c in off if c["missing_owned"]),
    }
    for k in _COUNT_ONLY:
        counts[k] = len(data.get(k) or [])

    if owned_only:
        one_away = [c for c in one_away if c["missing_owned"]]
        off = [c for c in off if c["missing_owned"]]
    if limit is not None and limit >= 0:
        one_away = one_away[:limit]
        off = off[:limit]

    # missing cards that complete the most combos (owned first)
    by_card: Dict[str, dict] = {}
    for c in one_away:
        for m in c["missing"]:
            row = by_card.setdefault(m, {"name": m, "combos": 0, "owned": c["owned"].get(m)})
            row["combos"] += 1
    top_missing = sorted(by_card.values(), key=lambda x: (x["owned"] is None, -x["combos"], x["name"]))[:15]

    return {
        "source": "commanderspellbook",
        "deck": {"registry_id": deck.get("registry_id"), "id": deck.get("id"), "name": deck.get("name"),
                 "commanders": commanders},
        "identity": data.get("identity"),
        "fetched_at": data.get("fetched_at"),
        "cached": data.get("cached", False),
        "stale": data.get("stale", False),
        **({"error": data["error"]} if data.get("error") else {}),
        "counts": counts,
        "in_deck": included,
        "one_away": one_away,
        "off_identity_one_away": off,
        "top_missing_cards": top_missing,
    }

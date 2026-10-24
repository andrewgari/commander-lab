"""
Card facts (type line, oracle text, colour identity, Commander legality) from Scryfall via
/cards/collection, cached per card in Redis (`card_eval:card:{name_key}`, 7 days).
Unknown names are absent from the result; a failed request raises CardSourceError.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional

import requests

from deck_slots import TYPE_CLASS_TO_SLOT

from analytics.edhrec_inclusion import name_key

CACHE_PREFIX = "card_eval:card:"
TTL = timedelta(days=7)
UA = {"User-Agent": "commander-lab/1.0 (card-eval)", "Accept": "application/json"}

_TYPE_TOKEN_SPLIT = re.compile(r"[\s\u2014/]+")


class CardSourceError(Exception):
    pass


def variant_type_class(type_line: str) -> Optional[str]:
    """Variant-deck type class (plane/phenomenon/scheme/attraction) found in a
    type line, else None.

    Scans the WHOLE type line, subtypes included: Attraction is a subtype
    ('Artifact — Attraction'), while Plane/Phenomenon/Scheme are types.
    """
    words = {w.casefold() for w in _TYPE_TOKEN_SPLIT.split(type_line or "") if w}
    for type_class in sorted(TYPE_CLASS_TO_SLOT):
        if type_class in words:
            return type_class
    return None


def mainboard_gate(rec: Mapping[str, Any]) -> Dict[str, Any]:
    """Full mainboard slot-gate result for a card record: Commander-legal AND
    no variant type class. `reason` is ALWAYS populated (pass or fail) so
    every suggestion can carry it. Fails closed on unknown legality.

    Tolerates records cached before the slot model (no 'type_class' key):
    the class is re-derived from 'type_line'.
    """
    type_class = rec.get("type_class")
    if type_class is None:
        type_class = variant_type_class(rec.get("type_line") or "")
    if type_class is not None:
        return {"slot": "mainboard", "legal": False, "type_class": type_class,
                "reason": f"{type_class} not legal in mainboard slot "
                          f"(belongs in {TYPE_CLASS_TO_SLOT[type_class]})"}
    legal = rec.get("legal")
    if legal is False:
        return {"slot": "mainboard", "legal": False, "type_class": None,
                "reason": "not legal in Commander"}
    if legal is not True:
        return {"slot": "mainboard", "legal": False, "type_class": None,
                "reason": "Commander legality unknown (unsynced card data); "
                          "treating it as not legal in the mainboard"}
    return {"slot": "mainboard", "legal": True, "type_class": None,
            "reason": "Commander-legal with no variant type class; legal in "
                      "the mainboard slot"}


def _record(c: dict) -> dict:
    faces = c.get("card_faces") or []
    text = c.get("oracle_text") or "\n".join(f.get("oracle_text", "") for f in faces)
    rec = {"name": c.get("name", ""), "oracle_id": c.get("oracle_id"),
           "type_line": c.get("type_line") or "", "oracle_text": text,
           "color_identity": "".join(c.get("color_identity") or []),
           # Commander legality ONLY. Never read this as mainboard legality;
           # use 'mainboard_gate' (the full mainboard slot gate) instead.
           "legal": (c.get("legalities") or {}).get("commander") == "legal"}
    rec["type_class"] = variant_type_class(rec["type_line"])
    rec["mainboard_gate"] = mainboard_gate(rec)
    return rec


def fetch_cards(r, names: Iterable[str], session=None, timeout: float = 20.0) -> Dict[str, dict]:
    """{name_key: record} for the names Scryfall knows. Cached records are reused."""
    wanted = {name_key(n): n for n in names if n}
    out: Dict[str, dict] = {}
    todo: List[str] = []
    now = datetime.now(timezone.utc)
    for k, n in wanted.items():
        try:
            raw = r.get(CACHE_PREFIX + k)
            rec = json.loads(raw) if raw else None
        except (ValueError, TypeError):
            rec = None
        if rec and rec.get("missing") is None:
            try:
                if now - datetime.fromisoformat(rec["fetched_at"]) < TTL:
                    out[k] = rec["card"]
                    continue
            except (KeyError, ValueError):
                pass
        todo.append(n)
    http = session or requests.Session()
    for i in range(0, len(todo), 75):
        chunk = todo[i:i + 75]
        resp = None
        for attempt in range(3):
            try:
                resp = http.post("https://api.scryfall.com/cards/collection", headers=UA, timeout=timeout,
                                 json={"identifiers": [{"name": n.split(" // ")[0]} for n in chunk]})
            except requests.exceptions.RequestException as exc:
                raise CardSourceError(f"Scryfall request failed: {exc}") from exc
            if resp.status_code == 429 or resp.status_code >= 500:
                time.sleep(2 ** attempt)
                resp = None
                continue
            break
        if resp is None or resp.status_code != 200:
            raise CardSourceError(f"Scryfall returned {getattr(resp, 'status_code', 'no response')}")
        for c in resp.json().get("data", []):
            rec = _record(c)
            stamp = {"fetched_at": now.isoformat(timespec="seconds"), "card": rec}
            for face in {name_key(rec["name"]), *(name_key(f) for f in rec["name"].split(" // "))}:
                if face in wanted:
                    out[face] = rec
                    r.set(CACHE_PREFIX + face, json.dumps(stamp))
        time.sleep(0.1)
    return out

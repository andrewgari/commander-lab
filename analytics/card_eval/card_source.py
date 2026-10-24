"""
Card facts (type line, oracle text, colour identity, Commander legality) from Scryfall via
/cards/collection, cached per card in Redis (`card_eval:card:{name_key}`, 7 days).
Unknown names are absent from the result; a failed request raises CardSourceError.
"""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

import requests

from analytics.edhrec_inclusion import name_key

CACHE_PREFIX = "card_eval:card:"
TTL = timedelta(days=7)
UA = {"User-Agent": "commander-lab/1.0 (card-eval)", "Accept": "application/json"}


class CardSourceError(Exception):
    pass


def _record(c: dict) -> dict:
    faces = c.get("card_faces") or []
    text = c.get("oracle_text") or "\n".join(f.get("oracle_text", "") for f in faces)
    return {"name": c.get("name", ""), "oracle_id": c.get("oracle_id"),
            "type_line": c.get("type_line") or "", "oracle_text": text,
            "color_identity": "".join(c.get("color_identity") or []),
            "legal": (c.get("legalities") or {}).get("commander") == "legal"}


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

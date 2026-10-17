"""
Saved Tune recommendations, archive-on-recompute.

Tuner output (`analytics.tuner.compute`) used to be computed per request and
thrown away. This module stores it per deck so it survives navigation and
reloads. Each deck has at most ONE `active` record. Recomputing saves the new
result as the active record and, in the same Redis MULTI/EXEC, flips the
previous active record to `archived` (with `archived_at`). Archived records are
never deleted.

Record shape (JSON):
    {id, deck_id, status: "active"|"archived", created_at, archived_at|null,
     params: {mode, in_deck, buy}, payload: <tuner.compute() output>}

Redis keys (the project's storage is Redis; there is no SQL):
    tunerec:rec:{id}            one record
    tunerec:active:{deck_id}    id of the deck's active record
    tunerec:index:{deck_id}     list of record ids, newest first (active + archived)
    tunerec:lock:{deck_id}      short NX lock around the swap (generation runs outside it)
    tunerec:schema_version      set by scripts/migrate_tune_recommendations.py

`deck_id` is the deck's registry id (e.g. "archidekt:417707").
"""
from __future__ import annotations

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, List, Mapping, Optional

REC_PREFIX = "tunerec:rec:"
ACTIVE_PREFIX = "tunerec:active:"
INDEX_PREFIX = "tunerec:index:"
LOCK_PREFIX = "tunerec:lock:"
SCHEMA_KEY = "tunerec:schema_version"
SCHEMA_VERSION = 1

ACTIVE = "active"
ARCHIVED = "archived"

LOCK_TTL_S = 10
LOCK_WAIT_S = 5.0


class TuneStoreError(RuntimeError):
    pass


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def deck_key(deck: Mapping) -> str:
    from .tuner import _rid

    return _rid(deck)


def _load(r, rec_id: Optional[str]) -> Optional[dict]:
    if not rec_id:
        return None
    raw = r.get(REC_PREFIX + rec_id)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


def get_active(r, deck_id: str) -> Optional[dict]:
    """The deck's active record, or None."""
    return _load(r, r.get(ACTIVE_PREFIX + deck_id))


def list_history(r, deck_id: str, limit: int = 20, include_payload: bool = False) -> List[dict]:
    """Archived records, newest first (the active record is not included)."""
    out: List[dict] = []
    active_id = r.get(ACTIVE_PREFIX + deck_id)
    for rec_id in r.lrange(INDEX_PREFIX + deck_id, 0, -1):
        if rec_id == active_id:
            continue
        rec = _load(r, rec_id)
        if not rec or rec.get("status") != ARCHIVED:
            continue
        if not include_payload:
            rec = {k: v for k, v in rec.items() if k != "payload"}
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def _acquire(r, deck_id: str) -> str:
    token = uuid.uuid4().hex
    deadline = time.monotonic() + LOCK_WAIT_S
    while not r.set(LOCK_PREFIX + deck_id, token, nx=True, ex=LOCK_TTL_S):
        if time.monotonic() >= deadline:
            raise TuneStoreError("Another recompute is saving for this deck; try again.")
        time.sleep(0.05)
    return token


def _release(r, deck_id: str, token: str) -> None:
    try:
        if r.get(LOCK_PREFIX + deck_id) == token:
            r.delete(LOCK_PREFIX + deck_id)
    except Exception:  # noqa: BLE001
        pass


def save_active(r, deck_id: str, payload: Mapping, params: Optional[Mapping] = None) -> tuple:
    """Store `payload` as the deck's new active record and archive the previous
    one in the same MULTI/EXEC. Returns (new_record, archived_record|None)."""
    token = _acquire(r, deck_id)
    try:
        now = _now()
        new = {"id": uuid.uuid4().hex, "deck_id": deck_id, "status": ACTIVE, "created_at": now,
               "archived_at": None, "params": dict(params or {}), "payload": payload}
        old = get_active(r, deck_id)
        pipe = r.pipeline()
        if old:
            old = dict(old, status=ARCHIVED, archived_at=now)
            pipe.set(REC_PREFIX + old["id"], json.dumps(old))
        pipe.set(REC_PREFIX + new["id"], json.dumps(new))
        pipe.set(ACTIVE_PREFIX + deck_id, new["id"])
        pipe.lpush(INDEX_PREFIX + deck_id, new["id"])
        pipe.execute()
        return new, old
    finally:
        _release(r, deck_id, token)


def recompute(r, deck: Mapping, generate: Callable[..., Any], mode: str = "flex",
              in_deck: bool = True, buy: bool = True) -> tuple:
    """Run `generate(r, deck, mode, in_deck, buy)` (normally `tuner.compute`) and save
    the result as the deck's active record. Nothing is saved if generation raises,
    so the previous active record stays active."""
    payload = generate(r, deck, mode, in_deck, buy)
    return save_active(r, deck_key(deck), payload, {"mode": mode, "in_deck": in_deck, "buy": buy})

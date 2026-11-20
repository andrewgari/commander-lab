"""
Stored personality scores (PS-3).

    recompute_all(r)        # one pass over every deck; unchanged decks skipped
    load_record(r, rid)     # the stored record for one deck
    load_all(r, decks)      # (scored entries, failed entries, decks not scored yet)
    request(r, rid)         # force a recompute on the next pass

The pass runs in the separate worker process (`analytics.bracket_worker`,
compose service `bracket-worker`), right after each bracket tick, never in
the web request path: `personality.score_deck` costs a few hundred ms per
deck. The worker already ticks on a timer and is woken by every sync path
and every Deck Journal save, so scores follow both within seconds.

Input hash = PERSONALITY_VERSION + decklist (commanders + card names and
quantities) + Deck Journal content (minus `_journal` bookkeeping) + the
fetch/build stamps of the caches the scorer reads (interaction graph
`built_at`, Spellbook `fetched_at`, EDHREC `fetched_at`, Game Changers
`fetched_at`). A deck is rescored when that hash changes, so a new graph
build or a Spellbook refresh is picked up too, but an idle collection costs
one hash per deck per pass.

Redis keys:
    personality:deck:{registry_id}  {registry_id, deck_name, status: ok|error, version,
                                     input_hash, computed_at, scores (PersonalityScores
                                     model_dump, incl. computed_at), error, error_at,
                                     duration_ms}
    personality:pending             set of registry_ids requested for a recompute
    personality:heartbeat           last pass summary

Failure isolation: a deck whose inputs or scorer raise gets status=error
(keeping its last good scores, and the failed input hash so it isn't
retried every pass until something changes or it's requested); the pass
moves on to the next deck.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

from . import personality as ps

log = logging.getLogger("commander_lab.personality_store")

KEY_PREFIX = "personality:deck:"
PENDING_KEY = "personality:pending"
HEARTBEAT_KEY = "personality:heartbeat"
DECKS_KEY = "decks"
JOURNAL_PREFIX = "journal:deck:"

Scorer = Callable[[Any, Mapping[str, Any]], Any]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def registry_id(deck: Mapping[str, Any]) -> str:
    if deck.get("registry_id"):
        return str(deck["registry_id"])
    return f"{deck.get('source', 'archidekt')}:{deck.get('source_id', deck.get('id'))}"


def _get_json(r, key: str):
    raw = r.get(key)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Input hash
# ---------------------------------------------------------------------------


def _decklist(deck: Mapping[str, Any]) -> dict:
    cards: Dict[str, int] = {}
    for c in deck.get("cards") or []:
        name = c.get("name") if isinstance(c, Mapping) else c
        if not name:
            continue
        qty = c.get("quantity") if isinstance(c, Mapping) else 1
        try:
            qty = int(qty or 1)
        except (TypeError, ValueError):
            qty = 1
        cards[str(name)] = cards.get(str(name), 0) + qty
    commanders = sorted(str(c) for c in deck.get("commanders") or [] if c)
    return {"commanders": commanders, "cards": sorted(cards.items())}


def _stamp(r, key: str, field: str) -> Optional[str]:
    rec = _get_json(r, key)
    return rec.get(field) if isinstance(rec, Mapping) else None


def cache_stamps(r, deck: Mapping[str, Any]) -> dict:
    """The build/fetch times of every cache the scorer reads (None when missing)."""
    from . import brackets, edhrec_inclusion
    from .interactions import combos, service
    from .providers.edhrec import commander_slug

    commanders = [c for c in deck.get("commanders") or [] if c]
    slug = commander_slug(commanders) if commanders else ""
    return {
        "graph": _stamp(r, service.DECK_GRAPH_PREFIX + registry_id(deck), "built_at"),
        "combos": _stamp(r, combos.decklist_key(commanders, combos._deck_names(dict(deck))), "fetched_at"),
        "edhrec": _stamp(r, edhrec_inclusion.cache_key(slug), "fetched_at") if slug else None,
        "game_changers": _stamp(r, brackets.CACHE_KEY, "fetched_at"),
    }


def input_hash(deck: Mapping[str, Any], journal: Optional[Mapping[str, Any]], stamps: Mapping[str, Any]) -> str:
    j = None if journal is None else {k: v for k, v in journal.items() if k != "_journal"}
    payload = {"v": ps.PERSONALITY_VERSION, "deck": _decklist(deck), "journal": j, "caches": dict(stamps)}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def current_input_hash(r, deck: Mapping[str, Any]) -> str:
    raw = r.get(JOURNAL_PREFIX + registry_id(deck))
    journal = json.loads(raw) if raw else None  # bad JSON raises: the caller records the deck as failed
    return input_hash(deck, journal, cache_stamps(r, deck))


# ---------------------------------------------------------------------------
# Stored records + requests
# ---------------------------------------------------------------------------


def load_record(r, rid: str) -> Optional[dict]:
    rec = _get_json(r, KEY_PREFIX + rid)
    return rec if isinstance(rec, dict) else None


def pending(r) -> set:
    return set(r.smembers(PENDING_KEY) or ())


def request(r, rid: str) -> None:
    """Force a recompute of `rid` on the next pass and wake the worker."""
    r.sadd(PENDING_KEY, rid)
    from . import bracket_worker
    bracket_worker.wake(r, rid)


def _store(r, rid: str, rec: dict) -> bool:
    try:
        r.set(KEY_PREFIX + rid, json.dumps(rec, default=str))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("could not store personality scores for %s: %s", rid, exc)
        return False


def _default_scorer(r, deck: Mapping[str, Any]):
    return ps.score_deck(r, deck)


def _dump(scores: Any) -> dict:
    return scores.model_dump() if hasattr(scores, "model_dump") else dict(scores)


def score_and_store(r, deck: Mapping[str, Any], h: str, scorer: Optional[Scorer] = None) -> bool:
    """Score one deck and store the record. Never raises."""
    scorer = scorer or _default_scorer
    rid = registry_id(deck)
    try:
        prev = load_record(r, rid) or {}
    except Exception:  # noqa: BLE001
        prev = {}
    t0 = time.monotonic()
    try:
        scores = _dump(scorer(r, deck))
    except Exception as exc:  # noqa: BLE001 - one deck must never stop the pass
        log.warning("personality scoring failed for %s (%s): %s", rid, deck.get("name"), exc)
        _store(r, rid, {
            "registry_id": rid, "deck_name": deck.get("name"), "status": "error", "version": prev.get("version"),
            "input_hash": h, "error": f"{type(exc).__name__}: {exc}"[:500], "error_at": _now(),
            # the last good scores stay readable; computed_at/result_hash describe them
            "scores": prev.get("scores"), "computed_at": prev.get("computed_at"),
            "result_hash": prev.get("result_hash") if prev.get("scores") else None,
        })
        return False
    at = _now()
    scores["computed_at"] = at
    return _store(r, rid, {
        "registry_id": rid, "deck_name": deck.get("name"), "status": "ok", "version": scores.get("version"),
        "input_hash": h, "result_hash": h, "computed_at": at, "scores": scores, "error": None,
        "duration_ms": int((time.monotonic() - t0) * 1000),
    })


def recompute_all(r, scorer: Optional[Scorer] = None) -> dict:
    """One pass over every deck. Unchanged input hash -> skipped; requested
    decks always rescored. Never raises."""
    stats: Dict[str, Any] = {"at": _now(), "decks": 0, "computed": 0, "skipped": 0, "failed": 0}
    try:
        decks = _get_json(r, DECKS_KEY) or []
        requested = pending(r)
    except Exception as exc:  # noqa: BLE001 - Redis down: report, next pass retries
        log.warning("personality pass could not read decks: %s", exc)
        stats["error"] = str(exc)[:200]
        return stats
    stats["decks"] = len(decks)
    t0 = time.monotonic()
    for deck in decks:
        if not isinstance(deck, Mapping):
            continue
        rid = registry_id(deck)
        try:
            h = current_input_hash(r, deck)
            prev = load_record(r, rid)
        except Exception as exc:  # noqa: BLE001
            log.warning("personality pass could not read inputs for %s: %s", rid, exc)
            try:
                old = load_record(r, rid) or {}
            except Exception:  # noqa: BLE001
                old = {}
            _store(r, rid, {"registry_id": rid, "deck_name": deck.get("name"), "status": "error",
                            "version": old.get("version"), "input_hash": None,
                            "error": f"inputs unreadable: {exc}"[:500], "error_at": _now(),
                            "scores": old.get("scores"), "computed_at": old.get("computed_at"),
                            "result_hash": old.get("result_hash")})
            stats["failed"] += 1
            continue
        forced = rid in requested
        if not forced and prev and prev.get("input_hash") == h:  # PERSONALITY_VERSION is in the hash
            stats["skipped"] += 1
            continue
        if forced:
            try:
                r.srem(PENDING_KEY, rid)
            except Exception:  # noqa: BLE001
                pass
        ok = score_and_store(r, deck, h, scorer)
        stats["computed" if ok else "failed"] += 1
    known = {registry_id(d) for d in decks if isinstance(d, Mapping)}
    for rid in requested - known:
        try:
            r.srem(PENDING_KEY, rid)
        except Exception:  # noqa: BLE001
            pass
    stats["duration_ms"] = int((time.monotonic() - t0) * 1000)
    try:
        r.set(HEARTBEAT_KEY, json.dumps({**stats, "pid": os.getpid()}))
    except Exception:  # noqa: BLE001
        pass
    return stats


def load_all(r, decks: List[Mapping[str, Any]]) -> Tuple[List[dict], List[dict], List[Mapping]]:
    """Stored records for `decks`: (scored entries for build_player_profile,
    failed entries, decks with no record yet). A deck whose last pass failed
    but has older good scores counts as scored (with its error kept)."""
    rids = [registry_id(d) for d in decks]
    keys = [KEY_PREFIX + rid for rid in rids]
    raws = r.mget(keys) if hasattr(r, "mget") else [r.get(k) for k in keys]
    scored, failed, missing = [], [], []
    for d, rid, raw in zip(decks, rids, raws):
        try:
            rec = json.loads(raw) if raw else None
        except (TypeError, ValueError):
            rec = None
        if not isinstance(rec, dict):
            missing.append(d)
            continue
        base = {"id": rid, "name": d.get("name"), "status": d.get("status")}
        if rec.get("scores"):
            scored.append({**base, "commanders": list(d.get("commanders") or []),
                           "commander_uids": list(d.get("commander_uids") or []),
                           "scores": rec["scores"], "computed_at": rec.get("computed_at")})
        else:
            failed.append({**base, "reason": rec.get("error") or "scoring failed"})
    return scored, failed, missing

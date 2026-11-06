"""
Bracket-evaluation worker (BR-5): runs the BR-4 evaluator OUTSIDE the web
request path and stores the result per deck.

    python -m analytics.bracket_worker          # compose service `bracket-worker`
    python -m analytics.bracket_worker --once   # one pass, then exit

Each tick also runs the PS-3 personality pass
(analytics.personality_store.recompute_all) after the bracket evaluations,
so deck personality scores follow syncs and journal saves the same way.

Every pass (a "tick") hashes each deck's input = its decklist (commanders +
card names/quantities) + its Deck Journal (without the _journal bookkeeping).
A deck is evaluated when that hash differs from the stored one, or when it was
explicitly requested (POST /api/deck/{id}/bracket). Unchanged decks are
skipped, so a tick over every deck is cheap. Decklist changes from any sync
path (sync.py, per-deck sync, linked accounts, import) are caught by the hash;
the web app also pushes a wake-up so journal saves and requests are picked up
within seconds instead of at the next tick.

Redis keys:
    bracket:eval:{registry_id}   {registry_id, deck_name, status: ok|error,
                                  evaluated_at, input_hash, result (BR-4
                                  BracketEvaluation.model_dump(), incl.
                                  estimated_bracket, estimated_power_level,
                                  explanation), error, error_at, duration_ms}
    bracket:eval:pending         set of registry_ids requested for re-evaluation
    bracket:eval:wake            list the worker BLPOPs on (wake-up only)
    bracket:eval:heartbeat       last tick summary (worker liveness)

Failure isolation: one deck failing records status=error (keeping the last
good result and recording the failed input hash so it isn't retried every
tick) and the tick moves on. Nothing here is imported by the deck page or the
sync code paths except the tiny request/wake helpers, which callers wrap so a
Redis hiccup can't fail the save or sync.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Mapping, Optional

log = logging.getLogger("commander_lab.bracket_worker")

EVAL_PREFIX = "bracket:eval:"
PENDING_KEY = "bracket:eval:pending"
WAKE_KEY = "bracket:eval:wake"
HEARTBEAT_KEY = "bracket:eval:heartbeat"
DECKS_KEY = "decks"
JOURNAL_PREFIX = "journal:deck:"

# Bump when the hashed input shape or the stored result shape changes, so
# every deck is re-evaluated once after the deploy.
INPUT_VERSION = 1

Evaluator = Callable[[Any, Mapping[str, Any]], Awaitable[Any]]


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
    commanders = sorted(str(c.get("name") if isinstance(c, Mapping) else c)
                        for c in deck.get("commanders") or [] if c)
    return {"commanders": commanders, "cards": sorted(cards.items())}


def input_hash(deck: Mapping[str, Any], journal: Optional[Mapping[str, Any]]) -> str:
    """sha256 over decklist + journal (minus _journal bookkeeping such as
    updated_at, so a no-op save doesn't force a re-run)."""
    j = None if journal is None else {k: v for k, v in journal.items() if k != "_journal"}
    payload = {"v": INPUT_VERSION, "deck": _decklist(deck), "journal": j}
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def current_input_hash(r, deck: Mapping[str, Any]) -> str:
    return input_hash(deck, _get_json(r, JOURNAL_PREFIX + registry_id(deck)))


# ---------------------------------------------------------------------------
# Stored results + requests (the only part the web app uses)
# ---------------------------------------------------------------------------


def load_result(r, rid: str) -> Optional[dict]:
    rec = _get_json(r, EVAL_PREFIX + rid)
    return rec if isinstance(rec, dict) else None


def pending(r) -> set:
    return set(r.smembers(PENDING_KEY) or ())


def is_pending(r, rid: str) -> bool:
    return bool(r.sismember(PENDING_KEY, rid))


def wake(r, rid: str = "") -> None:
    """Nudge the worker to run a tick now. Purely a latency hint: if it's
    lost, the next scheduled tick catches the change via the input hash."""
    r.lpush(WAKE_KEY, rid or "*")
    try:
        r.ltrim(WAKE_KEY, 0, 99)
    except Exception:  # noqa: BLE001 - trimming is housekeeping
        pass


def request_evaluation(r, rid: str) -> None:
    """Force a re-evaluation of `rid` on the next tick, even if unchanged."""
    r.sadd(PENDING_KEY, rid)
    wake(r, rid)


# ---------------------------------------------------------------------------
# Worker
# ---------------------------------------------------------------------------


async def _default_evaluator(r, deck: Mapping[str, Any]):
    from src.server.domain.analytics import bracket_eval

    commandersalt = os.getenv("BRACKET_EVAL_COMMANDERSALT", "1") != "0"
    return await bracket_eval.evaluate_bracket_live(r, deck, commandersalt=commandersalt)


def _dump(result: Any) -> dict:
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return dict(result)


async def evaluate_deck(r, deck: Mapping[str, Any], h: str, evaluator: Evaluator) -> bool:
    """Evaluate one deck and store the record. Never raises."""
    rid = registry_id(deck)
    prev = load_result(r, rid) or {}
    t0 = time.monotonic()
    try:
        result = _dump(await evaluator(r, deck))
    except Exception as exc:  # noqa: BLE001 - one deck must never stop the tick
        log.warning("bracket eval failed for %s (%s): %s", rid, deck.get("name"), exc)
        rec = {
            "registry_id": rid, "deck_name": deck.get("name"), "status": "error",
            "error": f"{type(exc).__name__}: {exc}"[:500], "error_at": _now(), "input_hash": h,
            # The last good evaluation stays readable; evaluated_at/result_hash describe it.
            "result": prev.get("result"), "evaluated_at": prev.get("evaluated_at"),
            "result_hash": prev.get("result_hash") if prev.get("result") else None,
        }
        _store(r, rid, rec)
        return False
    rec = {
        "registry_id": rid, "deck_name": deck.get("name"), "status": "ok", "evaluated_at": _now(),
        "input_hash": h, "result_hash": h, "result": result, "error": None,
        "duration_ms": int((time.monotonic() - t0) * 1000),
    }
    _store(r, rid, rec)
    log.info("bracket eval %s (%s): bracket %s, power %s, %d conflict(s), %dms", rid, deck.get("name"),
             result.get("estimated_bracket"), result.get("estimated_power_level"),
             len(result.get("conflicts") or []), rec["duration_ms"])
    return True


def _store(r, rid: str, rec: dict) -> None:
    try:
        r.set(EVAL_PREFIX + rid, json.dumps(rec, default=str))
    except Exception as exc:  # noqa: BLE001
        log.warning("could not store bracket eval for %s: %s", rid, exc)


async def run_once_async(r, evaluator: Optional[Evaluator] = None) -> dict:
    evaluator = evaluator or _default_evaluator
    stats: Dict[str, Any] = {"at": _now(), "decks": 0, "evaluated": 0, "skipped": 0, "failed": 0}
    try:
        decks = _get_json(r, DECKS_KEY) or []
        requested = pending(r)
    except Exception as exc:  # noqa: BLE001 - Redis down: report and try next tick
        log.warning("bracket worker tick could not read decks: %s", exc)
        stats["error"] = str(exc)[:200]
        return stats
    stats["decks"] = len(decks)
    for deck in decks:
        if not isinstance(deck, Mapping):
            continue
        rid = registry_id(deck)
        try:
            h = current_input_hash(r, deck)
            prev = load_result(r, rid)
        except Exception as exc:  # noqa: BLE001
            log.warning("bracket worker could not read inputs for %s: %s", rid, exc)
            stats["failed"] += 1
            continue
        forced = rid in requested
        if not forced and prev and prev.get("input_hash") == h:
            stats["skipped"] += 1
            continue
        if forced:
            try:
                r.srem(PENDING_KEY, rid)
            except Exception:  # noqa: BLE001
                pass
        ok = await evaluate_deck(r, deck, h, evaluator)
        stats["evaluated" if ok else "failed"] += 1
    known = {registry_id(d) for d in decks if isinstance(d, Mapping)}
    for rid in requested - known:  # requested deck vanished: don't keep it pending forever
        try:
            r.srem(PENDING_KEY, rid)
        except Exception:  # noqa: BLE001
            pass
    try:
        r.set(HEARTBEAT_KEY, json.dumps({**stats, "pid": os.getpid()}))
    except Exception:  # noqa: BLE001
        pass
    return stats


def run_once(r, evaluator: Optional[Evaluator] = None) -> dict:
    return asyncio.run(run_once_async(r, evaluator))


# Each BLPOP must return before the client's socket read timeout (redis-py
# 8 defaults to 5s), or it raises "Timeout reading from socket". So the
# wait is a series of short BLPOPs.
WAIT_CHUNK_S = 3


def _wait_for_wake(r, timeout: int, clock: Callable[[], float] = time.monotonic) -> bool:
    """Block until a wake-up arrives or `timeout` seconds pass, then drain
    the rest of the wake list so a burst of saves costs one tick. Returns
    True when woken."""
    deadline = clock() + max(1, timeout)
    while True:
        remaining = deadline - clock()
        if remaining <= 0:
            return False
        try:
            got = r.blpop(WAKE_KEY, timeout=max(1, min(WAIT_CHUNK_S, int(remaining))))
            if got:
                r.delete(WAKE_KEY)
                return True
        except Exception as exc:  # noqa: BLE001 - Redis down: back off, next tick retries
            log.warning("bracket worker wait failed: %s", exc)
            time.sleep(min(max(remaining, 0), 10))
            return False


def run_personality(r) -> dict:
    """PS-3: rescore deck personalities whose input hash changed. Runs in this
    process for the same reason bracket evaluation does (a few hundred ms
    per deck). Never raises, so it can't stop a bracket tick."""
    try:
        from src.server.domain.analytics import personality_store
        return personality_store.recompute_all(r)
    except Exception as exc:  # noqa: BLE001
        log.warning("personality pass failed: %s", exc)
        return {"error": str(exc)[:200]}


def run_tick(r, evaluator: Optional[Evaluator] = None) -> dict:
    """One worker tick: bracket evaluations, then the personality pass."""
    stats = run_once(r, evaluator)
    p = run_personality(r)
    stats["personality"] = p
    log.info("bracket worker tick: %d decks, %d evaluated, %d skipped, %d failed; personality %s computed, "
             "%s skipped, %s failed", stats.get("decks", 0), stats.get("evaluated", 0), stats.get("skipped", 0),
             stats.get("failed", 0), p.get("computed"), p.get("skipped"), p.get("failed"))
    return stats


def run_forever(r, interval: int, evaluator: Optional[Evaluator] = None) -> None:
    log.info("bracket worker started (pid %s, interval %ss)", os.getpid(), interval)
    while True:
        run_tick(r, evaluator)
        _wait_for_wake(r, interval)


def main(argv: Optional[List[str]] = None) -> None:
    import redis

    p = argparse.ArgumentParser(description="Commander Lab bracket-evaluation worker (BR-5)")
    p.add_argument("--once", action="store_true", help="run one tick and exit")
    p.add_argument("--interval", type=int, default=int(os.getenv("BRACKET_EVAL_INTERVAL_S", "300")),
                   help="seconds between ticks when nothing wakes the worker (default 300)")
    args = p.parse_args(argv)
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    r = redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"), decode_responses=True)
    if args.once:
        print(json.dumps(run_tick(r)))
        return
    run_forever(r, args.interval)


if __name__ == "__main__":
    main()

"""
Interaction graph service: rebuild orchestration, storage, run log, nightly job.

Rebuild triggers (no polling anywhere):
    sync     -- app.py calls start_rebuild after deck sync / linked-account sync / import
    manual   -- POST /api/interactions/refresh (and per-deck refresh)
    nightly  -- run_nightly_loop: sleeps until the next INTERACTIONS_NIGHTLY_HOUR_UTC,
                rebuilds, repeats. One sleep per day, not a poll.

Redis keys:
    card_interactions:{name}            per-card tags, no TTL (tagger.py)
    interactions:otag_index             Scryfall Tagger index, weekly TTL (tagger.py)
    interaction_graph:deck:{registry_id} materialized deck graph
    interaction_graph:collection        collection graph (nodes + resource index)
    interactions:runs                   last 200 run logs (JSON, newest first)
    interactions:engine_names           LLM engine names per signature, no TTL (AN-2)
    spellbook:fmc:{sha1}                Commander Spellbook find-my-combos per decklist (combos.py)
    interactions:rebuild_lock           single-flight lock (TTL)
    interactions:rebuild_rerun          set when a trigger arrives during a rebuild
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional

from . import engines as engines_mod
from . import graph as graph_mod
from .llm import LLMChain, default_chain
from .tagger import InteractionTagger, ScryfallSource
from .vocab import VOCAB_VERSION

log = logging.getLogger("commander_lab.interactions")

DECK_GRAPH_PREFIX = "interaction_graph:deck:"
COLLECTION_GRAPH_KEY = "interaction_graph:collection"
ENGINE_NAMES_KEY = "interactions:engine_names"
RUNS_KEY = "interactions:runs"
LOCK_KEY = "interactions:rebuild_lock"
RERUN_KEY = "interactions:rebuild_rerun"
LOCK_TTL = 2 * 3600
MAX_RUNS = 200
OWNED_STATUSES = {"in_deck", "in_collection", "in_mail"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class RunLog:
    """Per-run call/cost log. Persisted to interactions:runs by rebuild()."""

    def __init__(self, trigger: str):
        self.run_id = uuid.uuid4().hex[:12]
        self.trigger = trigger
        self.started_at = _now()
        self._t0 = time.monotonic()
        self.cache_hits = 0
        self.newly_tagged = 0
        self.new_by_source: Dict[str, int] = {}
        self.llm_pending = 0
        self.not_found: List[str] = []
        self.scryfall_requests = 0
        self.llm_calls: List[dict] = []
        self.graphs_built = 0
        self.status = "running"
        self.error: Optional[str] = None
        self.extra: dict = {}

    def count_new(self, source: str) -> None:
        self.newly_tagged += 1
        self.new_by_source[source] = self.new_by_source.get(source, 0) + 1

    def add_llm_call(self, call: dict) -> None:
        self.llm_calls.append({**call, "at": _now()})

    def to_dict(self) -> dict:
        ok_calls = [c for c in self.llm_calls if c.get("ok")]
        return {
            "run_id": self.run_id,
            "trigger": self.trigger,
            "status": self.status,
            "error": self.error,
            "started_at": self.started_at,
            "duration_s": round(time.monotonic() - self._t0, 2),
            "vocab_version": VOCAB_VERSION,
            "cards_seen": self.cache_hits + self.newly_tagged + self.llm_pending,
            "cache_hits": self.cache_hits,
            "newly_tagged": self.newly_tagged,
            "new_by_source": dict(self.new_by_source),
            "llm_pending": self.llm_pending,
            "not_found": self.not_found[:50],
            "not_found_count": len(self.not_found),
            "scryfall_requests": self.scryfall_requests,
            "llm_call_count": len(self.llm_calls),
            "llm_failed_calls": len(self.llm_calls) - len(ok_calls),
            "llm_input_tokens": sum(c.get("input_tokens", 0) for c in self.llm_calls),
            "llm_output_tokens": sum(c.get("output_tokens", 0) for c in self.llm_calls),
            "llm_cost_usd": round(sum(c.get("cost_usd", 0.0) for c in self.llm_calls), 6),
            "llm_calls": self.llm_calls,
            "graphs_built": self.graphs_built,
            **self.extra,
        }


# ---------------------------------------------------------------------------
# Library access
# ---------------------------------------------------------------------------


def _load_decks(r) -> List[dict]:
    raw = r.get("decks")
    return json.loads(raw) if raw else []


def _registry_id(deck: dict) -> str:
    if deck.get("registry_id"):
        return deck["registry_id"]
    return f"{deck.get('source', 'archidekt')}:{deck.get('source_id', deck.get('id'))}"


registry_id_of = _registry_id


def deck_card_names(deck: dict) -> List[str]:
    names = {c.get("name") for c in deck.get("cards") or [] if c.get("name")}
    names.update(n for n in deck.get("commanders") or [] if n)
    return sorted(names)


def owned_card_names(r) -> List[str]:
    keys = list(r.keys("instance:*"))
    if not keys:
        return []
    mget = r.mget if hasattr(r, "mget") else (lambda ks: [r.get(k) for k in ks])
    names = set()
    for i in range(0, len(keys), 1000):
        for raw in mget(keys[i : i + 1000]):
            if not raw:
                continue
            try:
                inst = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if inst.get("ownership_status") in OWNED_STATUSES and inst.get("card_name"):
                names.add(inst["card_name"])
    return sorted(names)


def _deck_meta(deck: dict) -> dict:
    return {
        "deck": {
            "registry_id": _registry_id(deck),
            "id": deck.get("id"),
            "name": deck.get("name"),
            "commanders": deck.get("commanders") or [],
            "status": deck.get("status"),
        },
        "built_at": _now(),
        "vocab_version": VOCAB_VERSION,
    }


def build_deck_graph(deck: dict, tags: Dict[str, dict]) -> dict:
    return graph_mod.build_graph(deck_card_names(deck), tags, meta=_deck_meta(deck),
                                 commanders=deck.get("commanders") or [])


# ---------------------------------------------------------------------------
# Rebuild
# ---------------------------------------------------------------------------


def _make_tagger(r, scryfall=None, llm: Optional[LLMChain] = None) -> InteractionTagger:
    return InteractionTagger(r, scryfall=scryfall if scryfall is not None else ScryfallSource(r),
                             llm=llm if llm is not None else default_chain())


# Edge sink (card dossier spec §2): the app/infra layer registers a callable
# (deck_graphs, collection_graph, vocab_version) -> summary that persists
# rebuild edges into Postgres card_interaction_edges. Kept as an injected
# callback so domain/ never imports infra/ (AGENT_RULES.md §2).
_EDGE_SINK = None


def register_edge_sink(sink) -> None:
    global _EDGE_SINK
    _EDGE_SINK = sink


def _rebuild_once(r, trigger: str, scryfall=None, llm=None, deck_ids: Optional[List[str]] = None) -> dict:
    run = RunLog(trigger)
    try:
        decks = _load_decks(r)
        if deck_ids:
            wanted = {str(d) for d in deck_ids}
            decks = [d for d in decks if _registry_id(d) in wanted or str(d.get("id")) in wanted]
        owned = owned_card_names(r) if not deck_ids else []
        all_names = set(owned)
        for d in decks:
            all_names.update(deck_card_names(d))

        tagger = _make_tagger(r, scryfall, llm)
        tags = tagger.tag_cards(all_names, run)

        deck_graphs = []
        for d in decks:
            g = build_deck_graph(d, tags)
            g["run_id"] = run.run_id
            r.set(DECK_GRAPH_PREFIX + _registry_id(d), json.dumps(g))
            run.graphs_built += 1
            deck_graphs.append(g)

        coll = None
        if not deck_ids:
            coll = graph_mod.build_collection_graph(owned, tags, meta={
                "scope": "collection", "built_at": _now(), "vocab_version": VOCAB_VERSION,
                "run_id": run.run_id,
            })
            r.set(COLLECTION_GRAPH_KEY, json.dumps(coll))
            run.graphs_built += 1
            run.extra["collection_counts"] = coll["counts"]
        run.extra["engine_naming"] = _name_new_engines(r, deck_graphs, tagger.llm, run)
        run.extra["identity"] = _draft_identities(r, decks, tags, tagger.llm, run)
        # Edge persistence runs on FULL rebuilds only: the sink replaces the
        # whole derived edge table, and a partial (per-deck) rebuild would
        # wipe every edge outside the rebuilt decks.
        if _EDGE_SINK is not None and not deck_ids:
            try:
                run.extra["edge_persistence"] = _EDGE_SINK(
                    deck_graphs, coll, VOCAB_VERSION)
            except Exception as exc:  # noqa: BLE001 - PG edge persistence is best-effort
                log.exception("interaction edge persistence failed")
                run.extra["edge_persistence"] = {"error": str(exc)[:200]}
        run.status = "ok"
    except Exception as exc:  # noqa: BLE001 - the run log must record the failure
        log.exception("interaction graph rebuild failed")
        run.status = "error"
        run.error = str(exc)[:500]
    out = run.to_dict()
    _record_run(r, out)
    return out


def _record_run(r, run: dict) -> None:
    r.lpush(RUNS_KEY, json.dumps(run))
    r.ltrim(RUNS_KEY, 0, MAX_RUNS - 1)


def list_runs(r, limit: int = 20) -> List[dict]:
    return [json.loads(x) for x in r.lrange(RUNS_KEY, 0, max(0, limit - 1))]


def rebuild(r, trigger: str = "manual", scryfall=None, llm=None,
            deck_ids: Optional[List[str]] = None) -> dict:
    """Single-flight rebuild. If another rebuild holds the lock, flags a rerun
    and returns a 'deferred' record; the holder reruns (full) when done."""
    token = uuid.uuid4().hex
    if not r.set(LOCK_KEY, token, nx=True, ex=LOCK_TTL):
        r.set(RERUN_KEY, "1")
        return {"status": "deferred", "trigger": trigger, "reason": "a rebuild is already running"}
    try:
        r.delete(RERUN_KEY)
        result = _rebuild_once(r, trigger=trigger, scryfall=scryfall, llm=llm, deck_ids=deck_ids)
        reruns = 0
        while r.get(RERUN_KEY) and reruns < 3:
            r.delete(RERUN_KEY)
            reruns += 1
            result = _rebuild_once(r, trigger=f"{trigger}+rerun", scryfall=scryfall, llm=llm)
        return result
    finally:
        if r.get(LOCK_KEY) == token:
            r.delete(LOCK_KEY)


def start_rebuild(r, trigger: str, deck_ids: Optional[List[str]] = None) -> dict:
    """Fire-and-forget rebuild in a daemon thread (used by HTTP triggers)."""
    if os.getenv("INTERACTIONS_DISABLED") == "1":
        return {"started": False, "trigger": trigger, "reason": "INTERACTIONS_DISABLED=1"}
    def _target():
        try:
            rebuild(r, trigger=trigger, deck_ids=deck_ids)
        except Exception:  # noqa: BLE001 - background trigger must never crash the app
            log.exception("background interaction rebuild (%s) failed", trigger)

    t = threading.Thread(target=_target, name=f"interactions-{trigger}", daemon=True)
    t.start()
    return {"started": True, "trigger": trigger, "deck_ids": deck_ids}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------


def get_deck_graph(r, deck: dict) -> dict:
    """Stored graph for the deck if it is current (same cardlist + vocab);
    otherwise rebuilt from cached tags only (no Scryfall/LLM calls). Cards
    with no cached tags show up as untagged nodes until the next rebuild."""
    raw = r.get(DECK_GRAPH_PREFIX + _registry_id(deck))
    names = deck_card_names(deck)
    if raw:
        g = json.loads(raw)
        if g.get("vocab_version") == VOCAB_VERSION and sorted(n["name"] for n in g.get("nodes", [])) == names:
            return g
    tagger = InteractionTagger(r, scryfall=_NoScryfall(), llm=LLMChain([]))
    tags = tagger.load_cached(names)
    g = build_deck_graph(deck, tags)
    g["stale"] = bool(g["counts"]["untagged"])
    return g


def get_collection_graph(r) -> Optional[dict]:
    raw = r.get(COLLECTION_GRAPH_KEY)
    return json.loads(raw) if raw else None


# ---------------------------------------------------------------------------
# Engines (AN-2) -- see analytics/interactions/engines.py
# ---------------------------------------------------------------------------


def load_engine_names(r) -> Dict[str, dict]:
    raw = r.get(ENGINE_NAMES_KEY)
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _name_new_engines(r, deck_graphs: List[dict], chain: Optional[LLMChain], run: "RunLog") -> dict:
    """Detect engines on the freshly built deck graphs and ask the LLM chain to
    name signatures neither the catalog nor the name cache covers. One call
    per rebuild at most; names are cached forever, so a signature costs one
    call ever. A failed call leaves the signature generic (retried next run)."""
    cache = load_engine_names(r)
    engines = []
    for g in deck_graphs:
        engines.extend(engines_mod.detect_engines(g))
    todo_sigs = engines_mod.unnamed_signatures(engines, cache)
    stats = {"engines": len(engines), "unnamed": len(todo_sigs), "named": 0, "provider": None}
    if not todo_sigs or chain is None or not chain.available():
        return stats
    todo = [next(e for e in engines if e["signature"] == s) for s in todo_sigs]
    try:
        names = engines_mod.llm_name_signatures(chain, todo, on_call=run.add_llm_call)
    except Exception as exc:  # noqa: BLE001 - naming is best effort
        log.warning("engine naming failed: %s", exc)
        stats["error"] = str(exc)[:200]
        return stats
    if names:
        stamp = _now()
        for rec in names.values():
            rec["named_at"] = stamp
        cache.update(names)
        r.set(ENGINE_NAMES_KEY, json.dumps(cache))
        stats["named"] = len(names)
        stats["provider"] = next(iter(names.values())).get("provider")
    return stats


def _draft_identities(r, decks: List[dict], tags: Dict[str, dict], chain: Optional[LLMChain], run: "RunLog") -> dict:
    """AN-3: draft thesis + pillars for decks whose cards changed since their
    last draft (re-syncs only ever ADD draft pillars). Capped per rebuild;
    every call lands in this run's llm_calls with purpose "pillars"."""
    if chain is None:
        return {"skipped": "no llm chain"}
    try:
        from src.server.domain.analytics import insights

        return insights.rebuild_hook(r, decks, tags, chain, run)
    except Exception as exc:  # noqa: BLE001 - identity drafting is best effort
        log.warning("identity drafting failed: %s", exc)
        return {"error": str(exc)[:200]}


def get_deck_engines(r, deck: dict, graph: Optional[dict] = None) -> dict:
    """Engines for one deck, from its (current) interaction graph. Pure math
    on the graph plus cached names; never calls the LLM."""
    g = graph or get_deck_graph(r, deck)
    engines = engines_mod.name_engines(engines_mod.detect_engines(g), load_engine_names(r))
    in_engines = sorted({c for e in engines for c in e["cards"]})
    names = [n["name"] for n in g.get("nodes", [])]
    return {
        "deck": g.get("deck") or {"registry_id": _registry_id(deck), "name": deck.get("name")},
        "graph_built_at": g.get("built_at"),
        "graph_stale": bool(g.get("stale")),
        "counts": {
            "engines": len(engines),
            "chains": sum(1 for e in engines if e["kind"] == "chain"),
            "loops": sum(1 for e in engines if e["kind"] == "loop"),
            "cards_in_engines": len(in_engines),
            "cards": len(names),
        },
        "engines": engines,
        "cards_outside_engines": sorted(set(names) - set(in_engines)),
    }


class _NoScryfall:
    requests = 0

    def fetch_cards(self, names):
        return {}, list(names)

    def otag_index(self):
        return {}


# ---------------------------------------------------------------------------
# Nightly job
# ---------------------------------------------------------------------------


def seconds_until_next_run(now: datetime, hour_utc: int) -> float:
    target = now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return (target - now).total_seconds()


def run_nightly_loop(r, hour_utc: Optional[int] = None, stop: Optional[threading.Event] = None) -> None:
    hour = int(os.getenv("INTERACTIONS_NIGHTLY_HOUR_UTC", "8")) if hour_utc is None else hour_utc
    stop = stop or threading.Event()
    while not stop.is_set():
        wait = seconds_until_next_run(datetime.now(timezone.utc), hour)
        if stop.wait(wait):
            return
        try:
            rebuild(r, trigger="nightly")
        except Exception:  # noqa: BLE001
            log.exception("nightly interaction rebuild failed")


def start_nightly_thread(r) -> Optional[threading.Thread]:
    if os.getenv("INTERACTIONS_NIGHTLY", "1") == "0" or os.getenv("INTERACTIONS_DISABLED") == "1":
        return None
    t = threading.Thread(target=run_nightly_loop, args=(r,), name="interactions-nightly", daemon=True)
    t.start()
    return t

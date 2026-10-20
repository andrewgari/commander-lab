"""SM-3: background simulation jobs + one-off Oracle simulation review.

Flow (POST /api/decks/{id}/sim/run):
    validate params -> export .dck for the deck (and user-deck opponents) -> submit to the Forge
    daemon (sim_client.start_job) -> background thread polls sim_client.get_job until done ->
    sim_diff.build_sim_comparison vs the static review -> Oracle `deck-sim-review` prose (or
    deterministic fallback) -> sim_store.save_completed (deck:sim:{registry_id}).

The previous saved report is only replaced by a run that completed; queued, running and failed jobs
never touch it. Job state (what the UI polls) lives in `deck:sim:run:{job_id}`.
"""
from __future__ import annotations

import json
import logging
import random
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional

from . import review_metrics, sim_client, sim_diff, sim_store
from .sim_client import SimBusy, SimClientError, SimUnavailable

log = logging.getLogger("commander_lab.sim_service")

SKILL_NAME = "deck-sim-review"
RUN_PREFIX = "deck:sim:run:"
JOB_TTL_S = 7 * 24 * 3600
OPPONENT_MODES = ("standard", "random", "specific", "rapid1v1")
GAME_PRESETS = (20, 60, 5)
MAX_TEST_GAMES = 100
MAX_NOTES = 1000
SPECIFIC_COUNT = 3
MAX_USER_DECK_EXPORTS = 40
POLL_INTERVAL_S = 3.0
MAX_POLL_S = 6 * 3600
PROSE_MAX = 900
TWEAK_MAX = 300
TWEAK_RANGE = (2, 4)
ACTIVE_STATES = ("queued", "running", "writing_review")

_INFLIGHT: Dict[str, str] = {}
_INFLIGHT_LOCK = threading.Lock()


class SimParamError(ValueError):
    """Invalid run parameters (HTTP 422)."""


class SimRunBusy(Exception):
    """A simulation is already running (HTTP 409)."""

    def __init__(self, message: str, job_id: Optional[str] = None):
        super().__init__(message)
        self.job_id = job_id


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _rid(deck: Mapping) -> str:
    return deck.get("registry_id") or sim_store._rid(deck)


# ---------------------------------------------------------------------------------------------
# Opponent pool
# ---------------------------------------------------------------------------------------------

def user_opponent_id(deck: Mapping) -> str:
    """Matches the `Name=user_{id}` written into the .dck metadata by sim_client.export_deck_to_dck."""
    return f"user_{deck.get('id', deck.get('source_id', deck.get('name')))}"


def _precon_rows(payload: Any) -> List[dict]:
    items = payload
    if isinstance(payload, Mapping):
        items = next((payload[k] for k in ("opponents", "precons", "decks") if isinstance(payload.get(k), list)), [])
    rows = []
    for it in items or []:
        if isinstance(it, str):
            rows.append({"id": it, "name": it})
        elif isinstance(it, Mapping) and (it.get("id") or it.get("name")):
            rows.append({"id": str(it.get("id") or it.get("name")), "name": str(it.get("name") or it.get("id")),
                         **{k: it[k] for k in ("commander", "set", "year", "colors") if k in it}})
    return rows


def build_opponent_pool(r, client: Optional[sim_client.SimClient] = None, exclude_deck: Optional[Mapping] = None) -> dict:
    """Forge precons + synced Commander Lab decks. Daemon trouble degrades to user decks only."""
    client = client or sim_client.SimClient()
    pool: List[dict] = []
    daemon_error = None
    try:
        for row in _precon_rows(client.list_opponents()):
            pool.append({**row, "source": "precon"})
    except SimClientError as exc:
        daemon_error = str(exc)
    skip = _rid(exclude_deck) if exclude_deck else None
    try:
        import registry

        decks = registry.list_decks(r)
    except Exception:  # noqa: BLE001 - pool stays usable without Redis decks
        log.warning("could not list decks for the opponent pool", exc_info=True)
        decks = []
    for d in decks:
        if not d.get("name") or (skip and _rid(d) == skip):
            continue
        pool.append({"id": user_opponent_id(d), "name": d["name"], "source": "user_deck", "deck_id": d.get("id"),
                     "commander": " + ".join(d.get("commanders") or []) or None})
    return {"opponents": pool, "daemon_error": daemon_error}


# ---------------------------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------------------------

def validate_params(raw: Any) -> dict:
    raw = raw if isinstance(raw, Mapping) else {}
    mode = raw.get("opponent_mode", "standard")
    if mode not in OPPONENT_MODES:
        raise SimParamError(f"opponent_mode must be one of {', '.join(OPPONENT_MODES)}")
    games = raw.get("games", 20)
    if isinstance(games, bool) or not isinstance(games, int):
        raise SimParamError("games must be an integer")
    if games not in GAME_PRESETS and not 1 <= games <= MAX_TEST_GAMES:
        raise SimParamError(f"games must be {GAME_PRESETS[0]} or {GAME_PRESETS[1]} (1..{MAX_TEST_GAMES} allowed for tests)")
    notes = raw.get("notes") or ""
    if not isinstance(notes, str):
        raise SimParamError("notes must be a string")
    notes = notes.strip()
    if len(notes) > MAX_NOTES:
        raise SimParamError(f"notes must be at most {MAX_NOTES} characters")
    opponents = raw.get("opponents") or []
    if not isinstance(opponents, list) or not all(isinstance(o, str) and o.strip() for o in opponents):
        raise SimParamError("opponents must be a list of opponent ids")
    opponents = [o.strip() for o in opponents]
    if mode == "specific":
        if len(opponents) != SPECIFIC_COUNT:
            raise SimParamError(f"specific mode needs exactly {SPECIFIC_COUNT} opponents")
    elif mode == "rapid1v1":
        if len(opponents) != 1:
            raise SimParamError("rapid1v1 mode needs exactly 1 opponent")
    else:
        opponents = []
    return {"opponent_mode": mode, "games": games, "opponents": opponents, "notes": notes}


# ---------------------------------------------------------------------------------------------
# Job state
# ---------------------------------------------------------------------------------------------

def run_key(job_id: str) -> str:
    return RUN_PREFIX + job_id


def _loads(raw: Any) -> Optional[dict]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def load_job(r, job_id: str) -> Optional[dict]:
    return _loads(r.get(run_key(job_id)))


def _save_job(r, job: dict) -> dict:
    job["updated_at"] = _now_iso()
    r.set(run_key(job["job_id"]), json.dumps(job), ex=JOB_TTL_S)
    return job


def public_job(job: Mapping) -> dict:
    return {k: job.get(k) for k in ("job_id", "deck_id", "status", "games_completed", "games_total", "error",
                                    "error_kind", "params", "created_at", "updated_at", "finished_at", "review_source")}


def _reserve(rid: str, job_id: str) -> None:
    with _INFLIGHT_LOCK:
        if rid in _INFLIGHT:
            raise SimRunBusy("a simulation is already running for this deck", _INFLIGHT[rid])
        _INFLIGHT[rid] = job_id


def release(rid: str, job_id: Optional[str] = None) -> None:
    with _INFLIGHT_LOCK:
        if rid in _INFLIGHT and (job_id is None or _INFLIGHT[rid] == job_id):
            del _INFLIGHT[rid]


def active_job(r, deck: Mapping) -> Optional[dict]:
    """The in-flight job of this process for the deck, if any."""
    rid = _rid(deck)
    with _INFLIGHT_LOCK:
        jid = _INFLIGHT.get(rid)
    return load_job(r, jid) if jid else None


# ---------------------------------------------------------------------------------------------
# Submission
# ---------------------------------------------------------------------------------------------

def _export_user_decks(decks: List[Mapping]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for d in decks:
        try:
            _, _, dck = sim_client.export_deck_to_dck(dict(d))
        except ValueError as exc:
            log.info("skipping opponent deck %s: %s", d.get("name"), exc)
            continue
        out[user_opponent_id(d)] = dck
    return out


def build_payload(r, deck: Mapping, params: Mapping, client: sim_client.SimClient) -> dict:
    """POST /jobs body: target deck + opponent selection + .dck texts of any user decks involved."""
    mode, opponents = params["opponent_mode"], list(params["opponents"])
    payload = sim_client.build_job_payload(dict(deck), games=params["games"], opponent_mode=mode,
                                           opponents=opponents or None)
    import registry

    others = [d for d in registry.list_decks(r) if _rid(d) != _rid(deck)]
    by_id = {user_opponent_id(d): d for d in others}
    if mode in ("specific", "rapid1v1"):
        chosen = [by_id[o] for o in opponents if o in by_id]
        texts = _export_user_decks(chosen)
        missing = [o for o in opponents if o in by_id and o not in texts]
        if missing:
            raise SimParamError(f"opponent deck(s) can't be exported (not a complete 100-card deck): {', '.join(missing)}")
    elif mode == "random":
        pool = list(by_id.values())
        random.shuffle(pool)
        texts = _export_user_decks(pool[:MAX_USER_DECK_EXPORTS])
        payload["opponent_pool"] = [o["id"] for o in build_opponent_pool(r, client, deck)["opponents"]
                                    if o["source"] == "precon"] + list(texts)
    else:
        texts = {}
    if texts:
        payload["opponent_decks"] = texts
    payload["notes"] = params["notes"]
    return payload


def begin_run(r, deck: Mapping, raw_params: Any, client: Optional[sim_client.SimClient] = None) -> dict:
    """Validate, reserve the deck, submit to the daemon. Raises SimParamError / SimRunBusy /
    SimBusy / SimUnavailable / SimError; on any raise nothing is left reserved or recorded."""
    client = client or sim_client.SimClient()
    params = validate_params(raw_params)
    deck = dict(deck)
    rid = _rid(deck)
    job_id = uuid.uuid4().hex[:12]
    _reserve(rid, job_id)
    try:
        payload = build_payload(r, deck, params, client)
        daemon_job = client.start_job(payload)
        job = {"job_id": job_id, "deck_id": rid, "deck_name": deck.get("name"), "status": "queued",
               "daemon_job_id": daemon_job.get("job_id") or daemon_job.get("id"), "params": params,
               "games_completed": 0, "games_total": params["games"], "created_at": _now_iso(),
               "fingerprint": review_metrics.fingerprint(deck)}
        _save_job(r, job)
        sim_store.record_job_started(r, deck, {"job_id": job_id, "status": "queued"})
        return job
    except Exception:
        release(rid, job_id)
        raise


def _progress(job: dict, daemon: Mapping) -> None:
    prog = daemon.get("progress") if isinstance(daemon.get("progress"), Mapping) else {}
    done = daemon.get("games_completed", prog.get("games_completed", prog.get("completed")))
    total = daemon.get("games_total", prog.get("games_total", prog.get("total")))
    if isinstance(done, int) and not isinstance(done, bool):
        job["games_completed"] = done
    if isinstance(total, int) and not isinstance(total, bool) and total > 0:
        job["games_total"] = total


def run_job(r, job: dict, deck: Mapping, client: Optional[sim_client.SimClient] = None,
            oracle_factory: Optional[Callable[[], Any]] = None, *, poll_interval: Optional[float] = None,
            max_wait: Optional[float] = None, sleep: Optional[Callable[[float], None]] = None) -> dict:
    client = client or sim_client.SimClient()
    poll_interval = POLL_INTERVAL_S if poll_interval is None else poll_interval
    max_wait = MAX_POLL_S if max_wait is None else max_wait
    sleep = sleep or time.sleep
    deck = dict(deck)
    rid = job["deck_id"]
    try:
        _execute(r, job, deck, client, oracle_factory, poll_interval, max_wait, sleep)
    except Exception as exc:  # noqa: BLE001 - the saved report must survive any failure
        log.exception("sim job %s failed", job["job_id"])
        job.update(status="failed", error=f"{type(exc).__name__}: {str(exc)[:300]}", error_kind="internal")
        if isinstance(exc, SimUnavailable):
            job["error_kind"] = "sim_unavailable"
        try:
            sim_store.save_completed(r, deck, {"job_id": job["job_id"], "status": "failed", "error": job["error"]})
        except Exception:  # noqa: BLE001
            log.warning("could not close sim job pointer", exc_info=True)
    finally:
        release(rid, job["job_id"])
    job["finished_at"] = _now_iso()
    try:
        return _save_job(r, job)
    except Exception:  # noqa: BLE001
        log.exception("sim job %s: could not persist final state", job["job_id"])
        return job


def _execute(r, job, deck, client, oracle_factory, poll_interval, max_wait, sleep) -> None:
    job.update(status="running", started_at=_now_iso())
    _save_job(r, job)
    deadline = time.monotonic() + max_wait
    while True:
        daemon = client.get_job(job["daemon_job_id"])
        _progress(job, daemon)
        state = str(daemon.get("status") or "").lower()
        if state == "done":
            break
        if state in ("failed", "error", "cancelled", "canceled"):
            raise RuntimeError(str(daemon.get("error") or f"simulation {state}")[:300])
        _save_job(r, job)
        if time.monotonic() > deadline:
            raise TimeoutError("simulation did not finish in time")
        sleep(poll_interval)
    report = daemon.get("report")
    if not isinstance(report, Mapping):
        raise RuntimeError("simulation finished without a report")
    job["games_completed"] = job["games_total"] if not job.get("games_completed") else job["games_completed"]
    job.update(status="writing_review")
    _save_job(r, job)

    static = _static_review(r, deck)
    comparison = sim_diff.build_sim_comparison(deck, report, static)
    review, source, note = write_review(r, deck, job["params"], comparison, oracle_factory)
    final = dict(report)
    final["params"] = {**(report.get("params") or {}), **job["params"]}
    final["comparison"] = comparison
    final["oracle_review"] = {"source": source, "note": note, **review}
    saved = sim_store.save_completed(r, deck, {"job_id": job["job_id"], "status": "done", "report": final})
    if saved is None:
        raise RuntimeError("could not save the completed simulation")
    job.update(status="done", review_source=source, error=None)


def _static_review(r, deck: Mapping) -> Optional[dict]:
    """Saved Deep Review doc when present, else freshly computed metrics; None if neither works."""
    try:
        from . import review_doc

        doc = review_doc.load_latest(r, _rid(deck))
        if doc:
            return doc
    except Exception:  # noqa: BLE001
        log.debug("no saved review doc", exc_info=True)
    try:
        return review_metrics.review_deck(r, dict(deck))
    except Exception:  # noqa: BLE001 - sim review still works without static metrics
        log.warning("static review metrics unavailable", exc_info=True)
        return None


# ---------------------------------------------------------------------------------------------
# Review prose (Oracle or deterministic fallback)
# ---------------------------------------------------------------------------------------------

SYSTEM_PROMPT = (
    "Commander Lab request. Load and follow your `deck-sim-review` skill exactly "
    "(skill_view name='deck-sim-review'). Single turn: do not call tools. "
    "Reply with exactly one fenced ```json block matching sim_review.schema.json. Nothing after it."
)


def _pct(x: Any) -> str:
    return f"{x * 100:.0f}%" if isinstance(x, (int, float)) else "n/a"


def _journal(r, deck: Mapping) -> dict:
    try:
        from . import review_doc

        return review_doc._journal(r, dict(deck))
    except Exception:  # noqa: BLE001
        return {}


def _slim_comparison(comparison: Mapping) -> dict:
    """The prompt copy: per-game logs are bulky and not needed for prose."""
    c = json.loads(json.dumps(comparison, default=str))
    (c.get("pod_breakdown") or {}).pop("games", None)
    return c


def request_prompt(rid: str, params: Mapping, comparison: Mapping, journal: Mapping) -> str:
    payload = {"deck_id": rid, "fingerprint": comparison.get("fingerprint"), "params": dict(params),
               "comparison": _slim_comparison(comparison), "journal": journal}
    return (
        f"One-off simulation review request for '{(comparison.get('deck') or {}).get('name')}' ({rid}).\n\n"
        "Commander Lab ran the games and computed every number below. Follow the `deck-sim-review` skill. "
        "Respond to params.notes if present. Do not introduce numbers of your own.\n\n"
        f"```json\n{json.dumps(payload, indent=2, sort_keys=True, default=str)}\n```\n\n"
        "End with exactly ONE fenced ```json block matching sim_review.schema.json."
    )


def _validate_reply(data: Mapping, fingerprint: str) -> dict:
    from .review_doc import ReviewParseError

    if data.get("schema_version") != "1":
        raise ReviewParseError("schema_version must be '1'")
    if data.get("fingerprint") != fingerprint:
        raise ReviewParseError("oracle did not echo the fingerprint it was given")
    extra = set(data) - {"schema_version", "fingerprint", "headline", "notes_response", "tempo_and_mana_read",
                         "card_performance_read", "actionable_tweaks"}
    if extra:
        raise ReviewParseError(f"unexpected keys: {sorted(extra)}")
    out: Dict[str, Any] = {}
    for key, limit, required in (("headline", 300, True), ("notes_response", PROSE_MAX, False),
                                 ("tempo_and_mana_read", PROSE_MAX, False), ("card_performance_read", PROSE_MAX, False)):
        v = data.get(key, "" if not required else None)
        if not isinstance(v, str) or (required and not v.strip()) or len(v) > limit:
            raise ReviewParseError(f"{key} must be a string of at most {limit} characters")
        out[key] = v.strip()
    tweaks = data.get("actionable_tweaks")
    if (not isinstance(tweaks, list) or not all(isinstance(t, str) and t.strip() and len(t) <= TWEAK_MAX for t in tweaks)
            or not TWEAK_RANGE[0] <= len(tweaks) <= TWEAK_RANGE[1]):
        raise ReviewParseError(f"actionable_tweaks must be {TWEAK_RANGE[0]}-{TWEAK_RANGE[1]} strings of at most {TWEAK_MAX} characters")
    out["actionable_tweaks"] = [t.strip() for t in tweaks]
    return out


def _clean(review: dict, allowed: set, names: List[str]) -> tuple:
    from .review_doc import strip_invented_numbers

    dropped = 0
    out: Dict[str, Any] = {}
    for key in ("headline", "notes_response", "tempo_and_mana_read", "card_performance_read"):
        out[key], n = strip_invented_numbers(review[key], allowed, names)
        dropped += n
    tweaks = []
    for t in review["actionable_tweaks"]:
        clean, n = strip_invented_numbers(t, allowed, names)
        if n:
            dropped += n
        elif clean:
            tweaks.append(clean)
    out["actionable_tweaks"] = tweaks
    return out, dropped


def fallback_review(params: Mapping, comparison: Mapping) -> dict:
    """Deterministic prose built only from the comparison numbers."""
    tc = comparison.get("tempo_comparison") or {}
    sim, static, deltas = tc.get("sim") or {}, tc.get("static") or {}, tc.get("deltas") or {}
    cc = comparison.get("card_comparison") or {}
    pod = comparison.get("pod_breakdown") or {}
    games = pod.get("games_played")

    wr = pod.get("win_rate")
    headline = (f"Over {games} simulated games the deck won {_pct(wr)}; see the tempo and card reads for where it diverged from the paper review."
                if games else "The simulation finished; see the tempo and card reads for how it diverged from the paper review.")

    notes_resp = ""
    if params.get("notes"):
        notes_resp = ("Your notes were not interpreted automatically (Oracle was unavailable). "
                      f"The run covered {games or params.get('games')} games; compare the numbers below against what you were testing.")

    t_parts = []
    if sim.get("t5_mana_starvation_rate") is not None:
        t_parts.append(f"T5 mana starvation was {_pct(sim['t5_mana_starvation_rate'])} in simulation vs "
                       f"{_pct(static.get('t5_starvation_probability'))} expected on paper.")
    if sim.get("avg_first_commander_turn") is not None:
        t_parts.append(f"The commander was first cast on turn {sim['avg_first_commander_turn']} on average (mana value {static.get('commander_cmc')}).")
    if sim.get("avg_mana_utilization_t2_t6") is not None:
        t_parts.append(f"Mana utilization T2-T6 was {_pct(sim['avg_mana_utilization_t2_t6'])} vs {_pct(static.get('mana_utilization_t2_t6'))} in the static model.")
    t_parts += tc.get("notes") or []

    c_parts = []
    for label, key in (("Confirmed stragglers", "confirmed_stragglers"), ("Surprise dead draws", "surprise_dead_draws"),
                       ("Top workhorses", "confirmed_workhorses")):
        names = [x["name"] for x in (cc.get(key) or [])[:4]]
        if names:
            c_parts.append(f"{label}: {', '.join(names)}.")

    tweaks: List[str] = []
    if deltas.get("t5_mana_starvation") is not None and deltas["t5_mana_starvation"] > 0.1:
        tweaks.append("Add a land or cheap ramp piece: simulated T5 mana starvation ran above the paper expectation.")
    if deltas.get("commander_turn_vs_cmc") is not None and deltas["commander_turn_vs_cmc"] > 1:
        tweaks.append("Lower the curve or add early ramp so the commander lands closer to its mana value.")
    for x in (cc.get("surprise_dead_draws") or [])[:2]:
        if not x.get("pet"):
            tweaks.append(f"Re-evaluate {x['name']}: it was cast in only {_pct(x['utilization_rate'])} of games where it was drawn.")
    for x in (cc.get("confirmed_stragglers") or [])[:2]:
        if not x.get("pet"):
            tweaks.append(f"Consider replacing {x['name']}: the static review flagged it and the sim confirmed it ({_pct(x['utilization_rate'])} cast rate).")
    for x in (cc.get("confirmed_stragglers") or []) + (cc.get("surprise_dead_draws") or []):
        if x.get("pet") and len(tweaks) < TWEAK_RANGE[1]:
            tweaks.append(f"Pet card {x['name']} underperformed; support it with more enablers or curve help rather than cutting it.")
    generic = ["Re-run with more games to firm up these reads.", "Keep the current list; the simulation did not flag a clear problem."]
    for g in generic:
        if len(tweaks) < TWEAK_RANGE[0]:
            tweaks.append(g)
    return {"headline": headline, "notes_response": notes_resp,
            "tempo_and_mana_read": " ".join(t_parts)[:PROSE_MAX], "card_performance_read": " ".join(c_parts)[:PROSE_MAX],
            "actionable_tweaks": [t[:TWEAK_MAX] for t in tweaks[:TWEAK_RANGE[1]]]}


def write_review(r, deck: Mapping, params: Mapping, comparison: Mapping,
                 oracle_factory: Optional[Callable[[], Any]] = None) -> tuple:
    """Return (review_dict, source, note); source is "oracle" or "fallback"."""
    from .oracle_client import OracleError, OracleUnavailable
    from .review_doc import ReviewParseError, allowed_numbers, parse_reply, _card_names

    try:
        if oracle_factory is None:
            from .oracle_client import OracleClient

            oracle_factory = OracleClient.from_env
        client = oracle_factory()
        reply = client.chat([
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": request_prompt(_rid(deck), params, comparison, _journal(r, deck))},
        ], tools=[], tool_choice="none")
        parsed = _validate_reply(parse_reply(reply), str(comparison.get("fingerprint")))
        allowed = allowed_numbers({"comparison": comparison, "params": dict(params)})
        review, _dropped = _clean(parsed, allowed, _card_names(comparison))
        if not review["headline"] or len(review["actionable_tweaks"]) < TWEAK_RANGE[0]:
            raise ReviewParseError("reply held too many numbers that are not in the run data")
        return review, "oracle", ""
    except OracleUnavailable as exc:
        note = (exc.detail or "oracle offline")[:200]
    except ReviewParseError as exc:
        note = f"oracle reply invalid: {str(exc)[:250]}"
    except OracleError as exc:
        note = f"{type(exc).__name__}: {str(exc)[:250]}"
    return fallback_review(params, comparison), "fallback", note


def start_thread(r, job: dict, deck: Mapping, client: Optional[sim_client.SimClient] = None,
                 oracle_factory: Optional[Callable[[], Any]] = None, **kw) -> threading.Thread:
    t = threading.Thread(target=run_job, args=(r, job, dict(deck), client, oracle_factory), kwargs=kw,
                         name=f"sim-{job['job_id']}", daemon=True)
    t.start()
    return t


# ---------------------------------------------------------------------------------------------
# Read side
# ---------------------------------------------------------------------------------------------

def saved_view(r, deck: Mapping) -> dict:
    """Saved sim doc + meta for GET /sim."""
    doc = sim_store.load_latest(r, deck)
    meta = (doc or {}).get("meta") or {}
    return {
        "exists": doc is not None,
        "sim": doc,
        "meta": {"exists": doc is not None, "stale": bool(meta.get("stale")), "stale_reason": meta.get("stale_reason"),
                 "created_at": meta.get("saved_at")},
    }

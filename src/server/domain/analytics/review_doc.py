"""RV-3: the saved, versioned Deep Review doc (docs/REVIEW_SPEC.md).

Pipeline (copies Theory / Personality Read): POST rebuild -> background job -> RV-2 metrics
(pure math) -> oracle (tool loop: checks curve, bracket and power first) writes PROSE over them -> typed-sections doc saved in Redis.

Core rule: every number, label and pick comes from `review_metrics`. Oracle's reply is validated
against `review.schema.json` (docs/oracle/deck-review/references) and any sentence carrying a
number that is not in the metrics is stripped. Oracle offline or invalid => the doc is still
saved with the math sections and an "explanation offline" oracle_summary; Rebuild retries.

Nothing here runs on page load: `get_current` only reads Redis and refreshes the stale flag.

Redis:
    review:{deck_id}                 latest doc (JSON)
    review:index:{deck_id}           zset of doc ids by created time (history)
    review:doc:{deck_id}:{doc_id}    each saved doc (history)
    review:job:{job_id}              rebuild job (7 days)
"""
from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Literal, Mapping, Optional, Set

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from . import review_metrics

log = logging.getLogger("commander_lab.review_doc")

SCHEMA_VERSION = "1"
SKILL_NAME = "deck-review"
DOC_PREFIX = "review:"
INDEX_PREFIX = "review:index:"
HIST_PREFIX = "review:doc:"
JOB_PREFIX = "review:job:"
JOB_TTL_S = 7 * 24 * 3600
HISTORY_KEEP = 20
MAX_INFLIGHT = 2
MAX_PROSE = 900

MATH_SECTIONS = ("genre", "composition", "curve_tempo", "focus", "strong_cards", "stragglers", "combos_engines")
SECTION_ORDER = MATH_SECTIONS + ("oracle_summary",)
TITLES = {
    "genre": "Deck Tags", "composition": "Composition", "curve_tempo": "Curve & tempo", "focus": "Focus",
    "strong_cards": "Strong cards", "stragglers": "Stragglers", "combos_engines": "Combos & engines",
    "oracle_summary": "Oracle summary",
}
OFFLINE_NOTE = "Explanation offline: oracle could not be reached. The numbers above are complete; press Rebuild to retry."
INVALID_NOTE = "Explanation unavailable: oracle's reply did not pass validation. The numbers above are complete; press Rebuild to retry."

REQUIRED_EVIDENCE = {"deck_stats": frozenset({"curve", "bracket", "power"})}
TOOLS = ("get_deck", "card_lookup", "deck_stats", "sim_run")
EVIDENCE_NOTE = "oracle answered without checking curve/bracket/power"

_INFLIGHT: Dict[str, str] = {}
_INFLIGHT_LOCK = threading.Lock()


class ReviewError(ValueError):
    """Base error."""


class ReviewParseError(ReviewError):
    """Oracle's reply failed parsing or schema validation."""


class ReviewBusy(ReviewError):
    def __init__(self, message: str, per_deck: bool):
        self.per_deck = per_deck
        super().__init__(message)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _epoch(iso: str) -> float:
    try:
        return datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return time.time()


# ---------------------------------------------------------------------------------------------
# Oracle reply schema (mirrors docs/oracle/deck-review/references/review.schema.json)
# ---------------------------------------------------------------------------------------------

class ProseSections(BaseModel):
    model_config = ConfigDict(extra="forbid")

    genre: str = Field(max_length=MAX_PROSE)
    composition: str = Field(max_length=MAX_PROSE)
    curve_tempo: str = Field(max_length=MAX_PROSE)
    focus: str = Field(max_length=MAX_PROSE)
    strong_cards: str = Field(max_length=MAX_PROSE)
    stragglers: str = Field(max_length=MAX_PROSE)
    combos_engines: str = Field(max_length=MAX_PROSE)
    oracle_summary: str = Field(min_length=1, max_length=MAX_PROSE)


class ReviewProse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["1"]
    fingerprint: str = Field(min_length=1)
    sections: ProseSections


def parse_reply(reply: str) -> dict:
    blocks = re.findall(r"```(?:json)?\s*([\s\S]*?)\s*```", reply or "")
    if not blocks:
        raise ReviewParseError("oracle reply contains no fenced ```json block")
    try:
        data = json.loads(blocks[-1].strip())
    except json.JSONDecodeError as exc:
        raise ReviewParseError(f"oracle JSON block is invalid: {exc}") from exc
    if not isinstance(data, dict):
        raise ReviewParseError("oracle JSON block must be an object")
    return data


def validate_prose(raw: Mapping, fingerprint: str) -> ReviewProse:
    try:
        prose = ReviewProse.model_validate(raw)
    except ValidationError as exc:
        raise ReviewParseError(f"review prose failed schema validation: {exc.errors()[:3]}") from exc
    if prose.fingerprint != fingerprint:
        raise ReviewParseError("oracle did not echo the fingerprint it was given")
    return prose


# ---------------------------------------------------------------------------------------------
# Invented-number guard
# ---------------------------------------------------------------------------------------------

_NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")
_TURN = re.compile(r"(?i)\b(?:turn|turns|t)\s*(\d{1,2})\b")
_SENT = re.compile(r"(?<=[.!?])\s+|\n+")


def _norm_num(x: float) -> str:
    return ("%.4f" % x).rstrip("0").rstrip(".") if x == x else ""


def _walk_numbers(obj: Any, out: Set[str]) -> None:
    if isinstance(obj, bool) or obj is None:
        return
    if isinstance(obj, (int, float)):
        v = float(obj)
        for nd in (0, 1, 2, 3, 4):
            out.add(_norm_num(round(v, nd)))
        for nd in (0, 1):  # fractions quoted as percentages
            out.add(_norm_num(round(v * 100, nd)))
        out.add(_norm_num(abs(v)))
    elif isinstance(obj, str):
        for m in _NUM.findall(obj):
            out.add(_norm_num(float(m)))
    elif isinstance(obj, Mapping):
        for k, v in obj.items():
            _walk_numbers(k, out)
            _walk_numbers(v, out)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            _walk_numbers(v, out)


def allowed_numbers(metrics: Mapping) -> Set[str]:
    out: Set[str] = set()
    _walk_numbers(metrics, out)
    return out


def _card_names(metrics: Mapping) -> List[str]:
    names: Set[str] = set()

    def walk(o: Any) -> None:
        if isinstance(o, Mapping):
            for k, v in o.items():
                if k in ("name", "card") and isinstance(v, str):
                    names.add(v)
                walk(v)
        elif isinstance(o, (list, tuple)):
            for v in o:
                walk(v)
        elif isinstance(o, str) and len(o) < 80:
            names.add(o)
    walk(metrics)
    return sorted((n for n in names if re.search(r"\d", n)), key=len, reverse=True)


def strip_invented_numbers(text: str, allowed: Set[str], names: Iterable[str] = ()) -> tuple[str, int]:
    """Drop every sentence holding a number that is not in the metrics.

    Returns (clean_text, sentences_dropped). Turn references (`turn 4`) and digits inside known
    card names are not counted as numbers."""
    names = list(names)
    kept: List[str] = []
    dropped = 0
    for sent in _SENT.split(text or ""):
        if not sent.strip():
            continue
        probe = sent
        for n in names:
            probe = probe.replace(n, " ")
        probe = _TURN.sub(" ", probe)
        bad = [m for m in _NUM.findall(probe) if _norm_num(float(m)) not in allowed]
        if bad:
            dropped += 1
        else:
            kept.append(sent.strip())
    return " ".join(kept), dropped


def clean_prose(prose: ReviewProse, metrics: Mapping, allowed: Optional[Set[str]] = None) -> tuple[Dict[str, str], int]:
    """`allowed` defaults to the metrics-only set; the oracle loop passes metrics plus tool outputs."""
    if allowed is None:
        allowed = allowed_numbers(metrics)
    names = _card_names(metrics)
    out: Dict[str, str] = {}
    total = 0
    for key in SECTION_ORDER:
        text, n = strip_invented_numbers(getattr(prose.sections, key), allowed, names)
        out[key] = text
        total += n
    return out, total


# ---------------------------------------------------------------------------------------------
# Doc building + storage
# ---------------------------------------------------------------------------------------------

def build_doc(rid: str, metrics: Mapping, prose: Optional[Mapping[str, str]], *, oracle_status: str,
              oracle_note: str = "", stripped: int = 0, created_at: Optional[str] = None) -> dict:
    """Math sections come verbatim from `metrics`; prose rides next to them, never inside `data`."""
    sections: List[dict] = []
    for key in MATH_SECTIONS:
        sec = {"type": key, "title": TITLES[key], "data": metrics.get(key)}
        sec["prose"] = (prose or {}).get(key) or None
        sections.append(sec)
    if oracle_status == "ok":
        text = (prose or {}).get("oracle_summary") or ""
    else:
        text = OFFLINE_NOTE if oracle_status == "offline" else INVALID_NOTE
    sections.append({"type": "oracle_summary", "title": TITLES["oracle_summary"], "prose": None,
                     "data": {"status": oracle_status, "text": text, "note": oracle_note or None,
                              "stripped_sentences": stripped}})
    return {
        "schema_version": SCHEMA_VERSION,
        "doc_id": uuid.uuid4().hex[:12],
        "deck_id": rid,
        "deck_name": (metrics.get("deck") or {}).get("name"),
        "fingerprint": metrics.get("fingerprint"),
        "genre_registry_version": metrics.get("genre_registry_version"),
        "metrics_schema": metrics.get("schema"),
        "created_at": created_at or _now_iso(),
        "stale": False,
        "stale_reason": None,
        "sections": sections,
    }


def math_sections(doc: Mapping) -> List[dict]:
    """The deterministic part of a doc (what two rebuilds of an unchanged deck must share)."""
    return [{"type": s["type"], "data": s["data"]} for s in doc.get("sections") or [] if s.get("type") in MATH_SECTIONS]


def latest_key(rid: str) -> str:
    return f"{DOC_PREFIX}{rid}"


def index_key(rid: str) -> str:
    return f"{INDEX_PREFIX}{rid}"


def hist_key(rid: str, doc_id: str) -> str:
    return f"{HIST_PREFIX}{rid}:{doc_id}"


def _loads(raw) -> Optional[dict]:
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


def save(r, doc: dict) -> dict:
    rid, doc_id = doc["deck_id"], doc["doc_id"]
    payload = json.dumps(doc, ensure_ascii=False)
    r.set(hist_key(rid, doc_id), payload)
    r.set(latest_key(rid), payload)
    r.zadd(index_key(rid), {doc_id: _epoch(doc["created_at"])})
    try:  # keep history bounded
        old = list(r.zrevrange(index_key(rid), HISTORY_KEEP, -1))
        for oid in old:
            oid = oid.decode() if isinstance(oid, bytes) else oid
            r.delete(hist_key(rid, oid))
            r.zrem(index_key(rid), oid)
    except Exception:  # noqa: BLE001 - trimming is best-effort
        log.debug("review history trim failed", exc_info=True)
    return doc


def load_latest(r, rid: str) -> Optional[dict]:
    return _loads(r.get(latest_key(rid)))


def load(r, rid: str, doc_id: str) -> Optional[dict]:
    return _loads(r.get(hist_key(rid, doc_id)))


def history(r, rid: str) -> List[dict]:
    out = []
    for did in r.zrevrange(index_key(rid), 0, -1):
        did = did.decode() if isinstance(did, bytes) else did
        d = load(r, rid, did)
        if d:
            out.append({"doc_id": d["doc_id"], "created_at": d.get("created_at"), "fingerprint": d.get("fingerprint"),
                        "genre_registry_version": d.get("genre_registry_version"), "stale": bool(d.get("stale")),
                        "oracle_status": next((s["data"].get("status") for s in d.get("sections") or []
                                               if s.get("type") == "oracle_summary"), None)})
    return out


# ---------------------------------------------------------------------------------------------
# Stale marking (cheap: fingerprint + registry version only; never rebuilds)
# ---------------------------------------------------------------------------------------------

def stale_reason(r, doc: Mapping, deck: Mapping) -> Optional[str]:
    from . import genres as genres_mod

    reasons = []
    if doc.get("fingerprint") != review_metrics.fingerprint(deck):
        reasons.append("decklist changed since this review was built")
    try:
        cur = genres_mod.get_version(r)
    except Exception:  # noqa: BLE001
        cur = None
    if cur is not None and doc.get("genre_registry_version") is not None and cur != doc.get("genre_registry_version"):
        reasons.append(f"genre registry changed (v{doc.get('genre_registry_version')} -> v{cur})")
    return "; ".join(reasons) or None


def refresh_stale(r, deck: Mapping) -> Optional[dict]:
    """Recompute the stale flag of the latest doc and persist it when it changed. Returns the doc."""
    rid = deck.get("registry_id") or _rid(deck)
    doc = load_latest(r, rid)
    if doc is None:
        return None
    reason = stale_reason(r, doc, deck)
    if bool(doc.get("stale")) != bool(reason) or doc.get("stale_reason") != reason:
        doc["stale"], doc["stale_reason"] = bool(reason), reason
        payload = json.dumps(doc, ensure_ascii=False)
        r.set(latest_key(rid), payload)
        if doc.get("doc_id"):
            r.set(hist_key(rid, doc["doc_id"]), payload)
    return doc


def mark_stale_on_sync(r, deck: Mapping) -> None:
    """Called by the sync path after a decklist write. Never raises."""
    try:
        refresh_stale(r, deck)
    except Exception:  # noqa: BLE001
        log.warning("could not refresh review stale flag", exc_info=True)


def _rid(deck: Mapping) -> str:
    import src.server.domain.decks.registry as registry

    return registry.registry_id_of(dict(deck))


# ---------------------------------------------------------------------------------------------
# Oracle request
# ---------------------------------------------------------------------------------------------

def request_facts(rid: str, metrics: Mapping, journal: Mapping) -> str:
    payload = {"deck_id": rid, "fingerprint": metrics.get("fingerprint"), "metrics": metrics, "journal": journal}
    return f"```json\n{json.dumps(payload, indent=2, sort_keys=True, default=str)}\n```"


def request_text(rid: str, metrics: Mapping) -> str:
    return (
        f"Deep Review prose request for '{(metrics.get('deck') or {}).get('name')}' ({rid}).\n"
        "Commander Lab computed every number above. Check the deck's curve, bracket and power level with "
        "your tools first, then write prose only, one string per section. Do not introduce numbers, "
        "labels or card picks of your own.\n"
        "End with exactly ONE fenced ```json block matching review.schema.json."
    )


def _journal(r, deck: Mapping) -> dict:
    try:
        from . import personality_read

        return personality_read.resolve_journal(r, deck)
    except Exception:  # noqa: BLE001 - journal is optional context
        return {}


def _evidence(outputs: Iterable[Any]) -> List[dict]:
    return [{"tool": "deck_stats", "sections": list((o.arguments or {}).get("sections") or [])}
            for o in outputs if o.ok and o.tool == "deck_stats"]


def build_review(r, deck: Mapping, oracle_factory: Callable[[], Any], metrics: Optional[dict] = None,
                 ctx_factory: Optional[Callable[..., Any]] = None) -> dict:
    """Metrics -> oracle tool loop -> saved doc. Oracle trouble degrades to the offline/invalid note.

    No single-prompt fallback (spec D1): the oracle must check curve, bracket and power with tools."""
    from src.server.domain.knowledge import build_messages
    from src.server.domain.llm.port import LLMError, LLMUnavailable
    from src.server.domain.oracle import OracleBudgetExceeded, OracleEvidenceMissing, OracleRunState
    from src.server.domain.oracle.numbers import allowed_numbers_from
    from src.server.domain.oracle.tools import ToolContext, build_registry

    deck = dict(deck)
    rid = deck.get("registry_id") or _rid(deck)
    if metrics is None:
        metrics = review_metrics.review_deck(r, deck)
    fp = str(metrics.get("fingerprint"))
    prose: Optional[Dict[str, str]] = None
    status, note, stripped = "ok", "", 0
    llm_meta: Optional[dict] = None
    evidence: List[dict] = []

    def validate(text: str, state: OracleRunState) -> List[str]:
        try:
            state.parsed = validate_prose(parse_reply(text), fp)
        except ReviewParseError as exc:
            return [str(exc)]
        return []

    try:
        oracle = oracle_factory()
        ctx = (ctx_factory(r, deck, metrics) if ctx_factory
               else ToolContext(deck=deck, metrics=metrics, journal=_journal(r, deck)))
        ctx.metrics = metrics
        names = [t for t in TOOLS if t != "sim_run" or ctx.sim is not None]
        messages, pack_id = build_messages("deck_review", game_changers=_gc_payload(ctx),
                                           facts_json=request_facts(rid, metrics, _journal(r, deck)),
                                           user_text=request_text(rid, metrics))
        oracle.pack_id = pack_id
        res = oracle.run(task="deck_review", messages=messages, registry=build_registry(names, ctx), ctx=ctx,
                         validate=validate, required_evidence=REQUIRED_EVIDENCE)
        llm_meta = res.trace.to_job_meta()
        evidence = _evidence(res.outputs)
        prose, stripped = clean_prose(res.parsed, metrics, allowed_numbers_from(res.outputs, metrics))
        if not prose.get("oracle_summary"):
            status, note = "invalid", "summary held only numbers that are not in the metrics"
    except LLMUnavailable as exc:
        status, note = "offline", (exc.detail or "")[:200]
    except OracleEvidenceMissing as exc:
        status, note, llm_meta = "invalid", EVIDENCE_NOTE, _meta(exc)
    except OracleBudgetExceeded as exc:
        status, note, llm_meta = "invalid", f"oracle hit its {exc.reason} budget"[:300], _meta(exc)
    except LLMError as exc:  # OracleInvalidAnswer, LLMResponseError, auth
        status, note, llm_meta = "invalid", (str(exc)[:300] if hasattr(exc, "trace") else
                                             f"{type(exc).__name__}: {str(exc)[:250]}"), _meta(exc)
    if status != "ok":
        prose = None
    doc = build_doc(rid, metrics, prose, oracle_status=status, oracle_note=note, stripped=stripped)
    if llm_meta is not None:
        data = doc["sections"][-1]["data"]
        data["llm"], data["evidence"] = llm_meta, evidence
    return save(r, doc)


def _meta(exc: BaseException) -> Optional[dict]:
    trace = getattr(exc, "trace", None)
    return trace.to_job_meta() if trace is not None else None


def _gc_payload(ctx: Any) -> Any:
    gc = getattr(ctx, "game_changers", None)
    return {"cards": list(gc), "source": "Commander Lab Game Changers list", "fetched_at": "this run"} if gc else None


# ---------------------------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------------------------

def job_key(job_id: str) -> str:
    return JOB_PREFIX + job_id


def _save_job(r, job: dict) -> dict:
    job["updated_at"] = _now_iso()
    r.set(job_key(job["job_id"]), json.dumps(job), ex=JOB_TTL_S)
    return job


def load_job(r, job_id: str) -> Optional[dict]:
    return _loads(r.get(job_key(job_id)))


def reserve(rid: str, job_id: str) -> None:
    with _INFLIGHT_LOCK:
        if rid in _INFLIGHT:
            raise ReviewBusy(f"a review rebuild is already running for this deck (job {_INFLIGHT[rid]})", per_deck=True)
        if len(_INFLIGHT) >= MAX_INFLIGHT:
            raise ReviewBusy(f"{MAX_INFLIGHT} review rebuilds are already running; try again shortly", per_deck=False)
        _INFLIGHT[rid] = job_id


def release(rid: str, job_id: Optional[str] = None) -> None:
    with _INFLIGHT_LOCK:
        if rid in _INFLIGHT and (job_id is None or _INFLIGHT[rid] == job_id):
            del _INFLIGHT[rid]


def new_job(r, deck: Mapping) -> dict:
    rid = deck.get("registry_id") or _rid(deck)
    job = {"job_id": uuid.uuid4().hex[:12], "deck_id": rid, "deck_name": deck.get("name"),
           "status": "queued", "created_at": _now_iso()}
    reserve(rid, job["job_id"])
    try:
        return _save_job(r, job)
    except Exception:
        release(rid, job["job_id"])
        raise


def run_job(r, job: dict, deck: Mapping, oracle_factory: Callable[[], Any], ctx_factory: Optional[Callable[..., Any]] = None) -> dict:
    rid = job["deck_id"]
    try:
        job.update(status="running", started_at=_now_iso())
        _save_job(r, job)
        doc = build_review(r, deck, oracle_factory, ctx_factory=ctx_factory)
        oracle = next((s["data"] for s in doc["sections"] if s["type"] == "oracle_summary"), {})
        job.update(status="done", doc_id=doc["doc_id"], oracle_status=oracle.get("status"))
        if oracle.get("llm"):
            job["llm"] = oracle["llm"]
    except Exception as exc:  # noqa: BLE001
        log.exception("review job %s failed", job["job_id"])
        job.update(status="failed", error=f"{type(exc).__name__}: {str(exc)[:300]}", error_kind="internal")
    finally:
        release(rid, job["job_id"])
    job["finished_at"] = _now_iso()
    try:
        return _save_job(r, job)
    except Exception:  # noqa: BLE001
        log.exception("review job %s: could not persist final state", job["job_id"])
        return job


def start_job(r, job: dict, deck: Mapping, oracle_factory: Callable[[], Any], ctx_factory: Optional[Callable[..., Any]] = None) -> threading.Thread:
    t = threading.Thread(target=run_job, args=(r, job, dict(deck), oracle_factory, ctx_factory),
                         name=f"review-{job['job_id']}", daemon=True)
    t.start()
    return t

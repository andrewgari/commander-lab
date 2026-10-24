"""SM-2: saved simulation report per deck.

Redis:
    deck:sim:{registry_id}       latest COMPLETED simulation (JSON {"meta": {...}, "report": {...}})
    deck:sim:job:{registry_id}   the in-flight/last job pointer (never touches the saved report)

The saved report is only ever replaced by a run that finished with status == "done". Starting, failing
or cancelling a job leaves it untouched. `meta.stale` uses the review fingerprint
(`review_metrics.fingerprint`): decklist changed after the sim => stale.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Mapping, Optional

from . import review_metrics

log = logging.getLogger("commander_lab.sim_store")

SIM_PREFIX = "deck:sim:"
JOB_PREFIX = "deck:sim:job:"
STALE_REASON = "Decklist changed since this simulation was run"
SCHEMA_VERSION = "1"


def sim_key(rid: str) -> str:
    return f"{SIM_PREFIX}{rid}"


def job_key(rid: str) -> str:
    return f"{JOB_PREFIX}{rid}"


def _rid(deck: Mapping) -> str:
    import registry

    return registry.registry_id_of(dict(deck))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _loads(raw: Any) -> Optional[dict]:
    if raw is None:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _stale_reason(saved_fp: Optional[str], deck: Mapping) -> Optional[str]:
    return STALE_REASON if saved_fp != review_metrics.fingerprint(deck) else None


def record_job_started(r, deck: Mapping, job: Mapping) -> dict:
    """Remember the running job. Does NOT touch deck:sim:{rid}."""
    rid = _rid(deck)
    ptr = {"job_id": job.get("job_id") or job.get("id"), "status": job.get("status") or "queued",
           "started_at": _now_iso(), "fingerprint": review_metrics.fingerprint(deck)}
    r.set(job_key(rid), json.dumps(ptr))
    return ptr


def load_job(r, deck: Mapping) -> Optional[dict]:
    return _loads(r.get(job_key(_rid(deck))))


def save_completed(r, deck: Mapping, job: Mapping) -> Optional[dict]:
    """Persist `job["report"]` as the deck's saved simulation iff the job is done.

    Returns the saved doc, or None when the job is not done / has no report (the previous saved
    report, if any, is left exactly as it was).
    """
    report = job.get("report")
    if job.get("status") != "done" or not isinstance(report, dict):
        _finish_pointer(r, deck, job)
        return None
    rid = _rid(deck)
    games = (report.get("summary") or {}).get("games") or (report.get("sim_config") or {}).get("games_run")
    doc = {
        "meta": {
            "schema_version": SCHEMA_VERSION,
            "deck_id": rid,
            "deck_name": deck.get("name"),
            "job_id": job.get("job_id") or job.get("id"),
            "saved_at": _now_iso(),
            "fingerprint": review_metrics.fingerprint(deck),
            "games": games,
            "opponent_mode": (report.get("params") or {}).get("opponent_mode") or job.get("opponent_mode"),
            "stale": False,
            "stale_reason": None,
        },
        "report": report,
    }
    r.set(sim_key(rid), json.dumps(doc, ensure_ascii=False))
    _finish_pointer(r, deck, job)
    return doc


def _finish_pointer(r, deck: Mapping, job: Mapping) -> None:
    ptr = load_job(r, deck)
    jid = job.get("job_id") or job.get("id")
    if ptr is not None and (jid is None or ptr.get("job_id") == jid):
        ptr.update(status=job.get("status") or ptr.get("status"), error=job.get("error"), finished_at=_now_iso())
        r.set(job_key(_rid(deck)), json.dumps(ptr))


def load_latest(r, deck: Mapping, persist_stale: bool = True) -> Optional[dict]:
    """The saved sim with `meta.stale` / `meta.stale_reason` recomputed against the current decklist."""
    rid = _rid(deck)
    doc = _loads(r.get(sim_key(rid)))
    if doc is None:
        return None
    meta = doc.setdefault("meta", {})
    reason = _stale_reason(meta.get("fingerprint"), deck)
    if bool(meta.get("stale")) != bool(reason) or meta.get("stale_reason") != reason:
        meta["stale"], meta["stale_reason"] = bool(reason), reason
        if persist_stale:
            try:
                r.set(sim_key(rid), json.dumps(doc, ensure_ascii=False))
            except Exception:  # noqa: BLE001 - reading must never fail on a write hiccup
                log.warning("could not persist sim stale flag", exc_info=True)
    return doc

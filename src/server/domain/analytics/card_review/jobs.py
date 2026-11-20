"""Background jobs + polling for card reviews (Redis job record, one in flight per deck+card)."""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Callable, Dict, Optional

log = logging.getLogger("commander_lab.card_review")

JOB_PREFIX = "card_review:job:"
JOB_TTL_S = 24 * 3600
MAX_INFLIGHT = 3
MAX_EVENTS = 50
_INFLIGHT: Dict[str, str] = {}
_LOCK = threading.Lock()


class ReviewBusy(Exception):
    def __init__(self, message: str, per_card: bool):
        self.per_card = per_card
        super().__init__(message)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def job_key(job_id: str) -> str:
    return JOB_PREFIX + job_id


def _save(r, job: dict) -> dict:
    job["updated_at"] = _now()
    r.set(job_key(job["job_id"]), json.dumps(job, default=str), ex=JOB_TTL_S)
    return job


def load_job(r, job_id: str) -> Optional[dict]:
    raw = r.get(job_key(job_id))
    try:
        return json.loads(raw) if raw else None
    except (TypeError, ValueError):
        return None


def release(slot: str, job_id: Optional[str] = None) -> None:
    with _LOCK:
        if slot in _INFLIGHT and (job_id is None or _INFLIGHT[slot] == job_id):
            del _INFLIGHT[slot]


def new_job(r, deck_id: str, card: str, rounds: int) -> dict:
    slot = f"{deck_id}|{card.lower()}"
    job_id = uuid.uuid4().hex[:12]
    with _LOCK:
        if slot in _INFLIGHT:
            raise ReviewBusy(f"a review of this card is already running (job {_INFLIGHT[slot]})", per_card=True)
        if len(_INFLIGHT) >= MAX_INFLIGHT:
            raise ReviewBusy(f"{MAX_INFLIGHT} reviews are already running; try again shortly", per_card=False)
        _INFLIGHT[slot] = job_id
    job = {"job_id": job_id, "deck_id": deck_id, "card": card, "rounds": rounds, "slot": slot,
           "status": "queued", "stage": "queued", "events": [], "transcript": [], "created_at": _now()}
    try:
        return _save(r, job)
    except Exception:
        release(slot, job_id)
        raise


def run_job(r, job: dict, work: Callable[[Callable[[dict], None]], dict]) -> dict:
    """`work(on_event)` does the review and returns the result dict. Progress is persisted per event, so
    a poller sees the stage and every validated debate turn as it lands."""

    def on_event(e: dict) -> None:
        job["stage"] = e.get("stage", job["stage"])
        if e.get("stage") == "turn":
            job["transcript"].append(e["turn"])
        job["events"] = (job["events"] + [{k: v for k, v in e.items() if k != "turn"} | {"at": _now()}])[-MAX_EVENTS:]
        try:
            _save(r, job)
        except Exception:  # noqa: BLE001 - progress is best effort
            log.warning("could not persist progress for %s", job["job_id"])

    try:
        job.update(status="running", started_at=_now())
        _save(r, job)
        job.update(status="done", stage="done", result=work(on_event))
    except Exception as exc:  # noqa: BLE001
        from .review import ReviewNotAllowed
        kind = "not_allowed" if isinstance(exc, ReviewNotAllowed) else type(exc).__name__
        if kind not in ("not_allowed", "OracleUnavailable", "UnknownCardError", "InvalidDeckError", "CardEvalError"):
            log.exception("card review job %s failed", job["job_id"])
            kind = "internal"
        job.update(status="failed", stage="failed", error=str(exc)[:500], error_kind=kind)
    finally:
        release(job["slot"], job["job_id"])
    job["finished_at"] = _now()
    try:
        return _save(r, job)
    except Exception:  # noqa: BLE001
        log.exception("could not persist final state of %s", job["job_id"])
        return job


def start_job(r, job: dict, work) -> threading.Thread:
    t = threading.Thread(target=run_job, args=(r, job, work), name=f"card-review-{job['job_id']}", daemon=True)
    t.start()
    return t

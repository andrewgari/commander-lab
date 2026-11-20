"""Postgres persistence for review run traces (deck reviewer card 08, DECISIONS.md D2).

The domain builds a ``Trace`` (review_pipeline/trace.py); this module stores it as one
``review_runs`` row plus one ``review_run_steps`` row per model attempt, and reads it back
losslessly so ``Trace.provenance_of`` works on a stored run.
"""
from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.server.domain.analytics.review_pipeline.trace import Trace
from src.server.infra.db.orm_models import ReviewRunRow, ReviewRunStepRow


def store_trace(session: Session, trace: Trace, *, owner_id: Optional[str] = None,
                document_id: Optional[int] = None) -> int:
    """Insert the run and its steps; return the run id. Caller commits."""
    run = ReviewRunRow(
        owner_id=owner_id, document_id=document_id, deck_id=trace.deck_id, started_at=trace.started_at,
        finished_at=trace.finished_at, status=trace.status, models=trace.models,
        prompt_versions=trace.prompt_versions, inputs=trace.inputs, drops=trace.drops,
        finding_steps=trace.finding_steps, cost_usd=trace.cost_usd, cost_complete=trace.cost_complete,
        error=trace.error)
    session.add(run)
    session.flush()
    for s in trace.steps:
        session.add(ReviewRunStepRow(
            run_id=run.id, idx=s["index"], pass_=s["pass"], axis=s["axis"], attempt=s["attempt"], model=s["model"],
            prompt_version=s["prompt_version"], input_hash=s["input_hash"], request=s["request"],
            response=s["response"], tokens_in=s["tokens_in"], tokens_out=s["tokens_out"],
            cached_tokens=s["cached_tokens"], latency_ms=s["latency_ms"], cost_usd=s["cost_usd"],
            verdict=s["verdict"], error=s["error"]))
    session.flush()
    return run.id


def load_trace(session: Session, run_id: int) -> Trace:
    run = session.get(ReviewRunRow, run_id)
    if run is None:
        raise KeyError(f"review run {run_id} not found")
    rows = session.scalars(select(ReviewRunStepRow).where(ReviewRunStepRow.run_id == run_id)
                           .order_by(ReviewRunStepRow.idx)).all()
    steps = [{
        "index": r.idx, "pass": r.pass_, "axis": r.axis, "label": (r.request or {}).get("label", ""),
        "attempt": r.attempt, "prompt_version": r.prompt_version, "model": r.model,
        "response_model": None, "input_hash": r.input_hash, "request": r.request, "response": r.response,
        "tokens_in": r.tokens_in, "tokens_out": r.tokens_out, "cached_tokens": r.cached_tokens,
        "cache_write_tokens": None, "latency_ms": r.latency_ms, "cost_usd": r.cost_usd, "verdict": r.verdict,
        "errors": [], "error": r.error, "at": None} for r in rows]
    return Trace(deck_id=run.deck_id, started_at=run.started_at, finished_at=run.finished_at, status=run.status,
                 error=run.error, inputs=dict(run.inputs or {}), steps=steps, drops=list(run.drops or []),
                 finding_steps=dict(run.finding_steps or {}), cost_usd=run.cost_usd,
                 cost_complete=bool(run.cost_complete))


def provenance_of(session: Session, run_id: int, finding_id: str) -> dict:
    """Which prompt version and model produced ``finding_id`` in stored run ``run_id``?"""
    return load_trace(session, run_id).provenance_of(finding_id)

"""One trace per review run (DECISIONS D2: ``review_runs`` + ``review_run_steps``).

The recorder is pure domain code: it collects the run in memory and ``Trace.to_dict()`` is what the
infra layer stores (JSONB). Every LLM attempt becomes a step with the input hash, prompt version,
model, raw output, tokens, latency and cost; every finding dropped by code becomes a ``drops`` entry
with the reason. ``Trace.provenance_of(finding_id)`` answers "which prompt version and model
produced this finding?".
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from src.server.domain.analytics.review_pipeline.structured import Attempt


class BudgetExceeded(RuntimeError):
    """The running cost passed ``REVIEW_MAX_COST_USD``. The run fails; nothing is truncated silently."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _cost(usage, model: str, prices: Mapping[str, Mapping[str, float]]) -> float | None:
    if usage.cost_usd is not None:
        return float(usage.cost_usd)
    p = prices.get(model)
    if not p:
        return None
    fresh = max(usage.tokens_in - usage.cached_tokens - usage.cache_write_tokens, 0)
    return (fresh * p.get("in", 0.0) + usage.cached_tokens * p.get("cache_read", p.get("in", 0.0))
            + usage.cache_write_tokens * p.get("cache_write", p.get("in", 0.0)) + usage.tokens_out * p.get("out", 0.0)) / 1e6


@dataclass
class Trace:
    deck_id: str
    started_at: str
    finished_at: str | None = None
    status: str = "running"            # running | ok | failed
    error: str | None = None
    inputs: dict[str, Any] = field(default_factory=dict)
    steps: list[dict[str, Any]] = field(default_factory=list)
    drops: list[dict[str, Any]] = field(default_factory=list)
    finding_steps: dict[str, dict[str, Any]] = field(default_factory=dict)
    cost_usd: float | None = None          # sum of the steps whose cost is known; None if none is
    cost_complete: bool = False            # False when some step's cost was unknowable (so cost_usd is a floor)

    @property
    def models(self) -> dict[str, str]:
        """pass -> model; a Pass B axis that ran on a different model than the rest is listed as 'B:<axis>'."""
        out: dict[str, str] = {}
        for s in self.steps:
            if s["pass"] in out and out[s["pass"]] != s["model"] and s["axis"]:
                out[f"{s['pass']}:{s['axis']}"] = s["model"]
            else:
                out.setdefault(s["pass"], s["model"])
        return out

    @property
    def prompt_versions(self) -> dict[str, str]:
        out: dict[str, str] = {}
        for s in self.steps:
            out.setdefault(s["pass"], s["prompt_version"])
        return out

    def provenance_of(self, finding_id: str) -> dict[str, Any]:
        link = self.finding_steps[finding_id]            # KeyError for an unknown finding is intended
        s = self.steps[link["step_index"]]
        return {"finding_id": finding_id, "pass": s["pass"], "axis": s["axis"], "prompt_version": s["prompt_version"],
                "model": s["model"], "response_model": s["response_model"], "input_hash": s["input_hash"],
                "attempt": s["attempt"], "step_index": s["index"], "kept": link["kept"]}

    def to_dict(self) -> dict[str, Any]:
        return {"deck_id": self.deck_id, "started_at": self.started_at, "finished_at": self.finished_at,
                "status": self.status, "error": self.error, "inputs": self.inputs, "models": self.models,
                "prompt_versions": self.prompt_versions, "cost_usd": self.cost_usd, "cost_complete": self.cost_complete, "steps": self.steps,
                "drops": self.drops, "finding_steps": self.finding_steps}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Trace":
        return cls(deck_id=d["deck_id"], started_at=d["started_at"], finished_at=d.get("finished_at"),
                   status=d.get("status", "ok"), error=d.get("error"), inputs=dict(d.get("inputs") or {}),
                   steps=list(d.get("steps") or []), drops=list(d.get("drops") or []),
                   finding_steps=dict(d.get("finding_steps") or {}), cost_usd=d.get("cost_usd"),
                   cost_complete=bool(d.get("cost_complete")))


class TraceRecorder:
    def __init__(self, *, deck_id: str, inputs: Mapping[str, Any], prices: Mapping[str, Mapping[str, float]] | None = None,
                 max_cost_usd: float | None = None):
        self._lock = threading.Lock()
        self._t = Trace(deck_id=deck_id, started_at=_now(), inputs=dict(inputs))
        self._prices = prices or {}
        self._max_cost = max_cost_usd
        self._cost_known = 0.0
        self._cost_unknown = False

    def record_attempt(self, a: Attempt, *, pass_name: str, axis: str | None, prompt_version: str) -> dict[str, Any]:
        r, resp = a.request, a.response
        cost = _cost(resp.usage, r.model, self._prices) if resp else None
        step = {
            "pass": pass_name, "axis": axis, "label": r.label, "attempt": a.index, "prompt_version": prompt_version,
            "model": r.model, "response_model": resp.model if resp else None, "input_hash": r.input_hash,
            "request": {"system_blocks": list(r.system_blocks), "messages": [[m.role, m.content] for m in r.messages],
                        "schema_name": r.schema_name, "max_tokens": r.max_tokens, "cache_blocks": r.cache_blocks},
            "response": resp.text if resp else None,
            "tokens_in": resp.usage.tokens_in if resp else None, "tokens_out": resp.usage.tokens_out if resp else None,
            "cached_tokens": resp.usage.cached_tokens if resp else None,
            "cache_write_tokens": resp.usage.cache_write_tokens if resp else None,
            "latency_ms": resp.latency_ms if resp else None, "cost_usd": cost,
            "verdict": a.verdict, "errors": list(a.errors), "error": a.error, "at": _now(),
        }
        with self._lock:
            step["index"] = len(self._t.steps)
            self._t.steps.append(step)
            if cost is None and resp is not None:
                self._cost_unknown = True
            elif cost is not None:
                self._cost_known += cost
            total = self._cost_known
        if self._max_cost is not None and total > self._max_cost:
            raise BudgetExceeded(f"review cost ${total:.4f} exceeded REVIEW_MAX_COST_USD=${self._max_cost:.4f}")
        return {"index": step["index"]}

    def record_drop(self, *, step_index: int, axis: str | None, finding: Mapping[str, Any], reason: str, detail: Any = None) -> None:
        with self._lock:
            self._t.drops.append({"step_index": step_index, "axis": axis, "reason": reason, "detail": detail,
                                  "finding": dict(finding)})

    def link_finding(self, finding_id: str, step_index: int, *, kept: bool) -> None:
        with self._lock:
            self._t.finding_steps[finding_id] = {"step_index": step_index, "kept": kept}

    def step_of(self, finding_id: str) -> int | None:
        with self._lock:
            link = self._t.finding_steps.get(finding_id)
            return link["step_index"] if link else None

    def finish(self, status: str, error: str | None = None) -> Trace:
        with self._lock:
            self._t.status, self._t.error, self._t.finished_at = status, error, _now()
            known = any(s["cost_usd"] is not None for s in self._t.steps)
            self._t.cost_usd = round(self._cost_known, 6) if known else None
            self._t.cost_complete = known and not self._cost_unknown
            return self._t

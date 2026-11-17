"""LLM provider boundary. The debate only ever talks to a DebateLLM; tests pass a scripted fake."""
from __future__ import annotations

import threading
from typing import Any, Callable, Optional, Protocol

Role = str  # "remove" | "keep" | "judge"


class DebateLLM(Protocol):
    def complete(self, role: Role, system: str, user: str, *, temperature: float = 0.0) -> str:
        """Return the raw model reply (the runner parses and validates the JSON)."""


TOOLS = ("get_deck", "card_lookup", "deck_stats")
EVIDENCE_NOTE = "oracle answered without checking curve/bracket/power"
JUDGE_EVIDENCE = {"deck_stats": frozenset({"curve", "bracket", "power"})}


class OracleDebateLLM:
    """Production adapter: one capped oracle tool-loop run per debate call (spec oracle-tool-loop §3).
    The judge must check the deck's curve, bracket and power before ruling; advocates need no evidence.
    Oracle fixes its own sampling, so `temperature` is advisory and not forwarded."""

    def __init__(self, oracle_factory: Callable[[], Any], ctx_factory: Callable[[], Any]):
        self._oracle_factory = oracle_factory
        self._ctx_factory = ctx_factory
        self._lock = threading.Lock()
        self._judge: Optional[dict] = None
        self._last: Optional[dict] = None

    def llm_meta(self) -> Optional[dict]:
        """Judge run meta (model/trace + evidence), else the last advocate run; None before any call."""
        with self._lock:
            return self._judge or self._last

    def _record(self, role: str, trace: Any, outputs: Any) -> None:
        if trace is None:
            return
        evidence = [{"tool": "deck_stats", "sections": list((o.arguments or {}).get("sections") or [])}
                    for o in (outputs or []) if o.ok and o.tool == "deck_stats"]
        meta = {**trace.to_job_meta(), "role": role, "evidence": evidence}
        with self._lock:
            self._last = meta
            if role == "judge":
                self._judge = meta

    def complete(self, role: Role, system: str, user: str, *, temperature: float = 0.0) -> str:
        from src.server.domain.oracle.tools import build_registry
        from .debate import extract_json

        def validate(text: str, state: Any) -> list:
            if extract_json(text) is None:
                return ["reply must contain a JSON object"]
            return []

        oracle = self._oracle_factory()
        ctx = self._ctx_factory()
        try:
            res = oracle.run(task="card_review",
                             messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                             registry=build_registry(list(TOOLS), ctx), ctx=ctx, validate=validate,
                             required_evidence=JUDGE_EVIDENCE if role == "judge" else {})
        except Exception as exc:
            self._record(role, getattr(exc, "trace", None), None)
            if type(exc).__name__ == "OracleEvidenceMissing":
                exc.args = (EVIDENCE_NOTE,)  # job error text (spec §4)
            raise
        self._record(role, res.trace, res.outputs)
        return res.text

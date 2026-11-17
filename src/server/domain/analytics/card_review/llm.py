"""LLM provider boundary. The debate only ever talks to a DebateLLM; tests pass a scripted fake."""
from __future__ import annotations

from typing import Any, Callable, Protocol

Role = str  # "remove" | "keep" | "judge"


class DebateLLM(Protocol):
    def complete(self, role: Role, system: str, user: str, *, temperature: float = 0.0) -> str:
        """Return the raw model reply (the runner parses and validates the JSON)."""


TOOLS = ("get_deck", "card_lookup", "deck_stats")
JUDGE_EVIDENCE = {"deck_stats": frozenset({"curve", "bracket", "power"})}


class OracleDebateLLM:
    """Production adapter: one capped oracle tool-loop run per debate call (spec oracle-tool-loop §3).
    The judge must check the deck's curve, bracket and power before ruling; advocates need no evidence.
    Oracle fixes its own sampling, so `temperature` is advisory and not forwarded."""

    def __init__(self, oracle_factory: Callable[[], Any], ctx_factory: Callable[[], Any]):
        self._oracle_factory = oracle_factory
        self._ctx_factory = ctx_factory

    def complete(self, role: Role, system: str, user: str, *, temperature: float = 0.0) -> str:
        from src.server.domain.oracle.tools import build_registry
        from .debate import extract_json

        def validate(text: str, state: Any) -> list:
            if extract_json(text) is None:
                return ["reply must contain a JSON object"]
            return []

        oracle = self._oracle_factory()
        ctx = self._ctx_factory()
        res = oracle.run(task="card_review",
                         messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                         registry=build_registry(list(TOOLS), ctx), ctx=ctx, validate=validate,
                         required_evidence=JUDGE_EVIDENCE if role == "judge" else {})
        return res.text

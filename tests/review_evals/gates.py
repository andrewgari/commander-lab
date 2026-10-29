"""Deterministic gates for review_document v1 (eval harness, card 03).

The gate logic lives in ``src/server/domain/analytics/review_pipeline/checks.py`` so the Pass D verifier and
this harness run the same code (card 09). This module keeps the harness's names: ``GATES``, ``run_gates``,
``EvalContext``, ``Violation`` and the fact-pack helpers.

Each gate is ``gate(doc, ctx) -> list[Violation]``; a review passes only when every gate returns zero
violations. Gates never call an LLM or the network. See ``checks.CheckContext`` for what ``EvalContext`` holds.
"""
from __future__ import annotations

from typing import Any, Callable

from src.server.domain.analytics.review_pipeline.checks import (  # noqa: F401  (re-exported)
    CHECKS as GATES, CheckContext as EvalContext, Violation, run_checks)


def fact_pack_hash(fact_pack: Any) -> str:
    """The fact pack's own hash (``FactPack.hash``), which is what ``meta.fact_pack_hash`` carries."""
    return fact_pack.hash


def fresh_fact_pack(deck: dict[str, Any], scryfall: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """Rebuild a fact pack offline from recorded Scryfall data."""
    from src.server.domain.analytics.fact_pack import build_fact_pack

    def _scry(name: str):
        return scryfall.get(name)

    return build_fact_pack(
        {"commanders": deck["commanders"], "cards": deck["cards"]},
        {"scryfall": _scry},
    )


def run_gates(doc: dict, ctx: EvalContext) -> list[Violation]:
    return run_checks(doc, ctx)

"""Pipeline entrypoint for Pass A + Pass B (C, D, E arrive in later cards and extend ``run_ab``'s result).

``run_ab`` never returns a partial review: it returns a brief, an analysis and a trace, or it raises
``ReviewRunFailed`` carrying the (failed) trace. ``on_trace`` receives the finished trace in both
cases so the infra layer can store it even when the run fails.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable

from src.server.domain.analytics.review_pipeline import a_brief, b_analysis
from src.server.domain.analytics.review_pipeline import config as cfg
from src.server.domain.analytics.review_pipeline.context import ReviewContext
from src.server.domain.analytics.review_pipeline.llm import LLMClient
from src.server.domain.analytics.review_pipeline.trace import Trace, TraceRecorder


class ReviewRunFailed(RuntimeError):
    def __init__(self, message: str, trace: Trace):
        super().__init__(message)
        self.trace = trace


@dataclass
class RunResult:
    brief: dict[str, Any]
    analysis: b_analysis.AnalysisResult
    trace: Trace


def _h(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def run_ab(ctx: ReviewContext, client: LLMClient, config: cfg.PipelineConfig | None = None, *,
           on_trace: Callable[[Trace], None] | None = None) -> RunResult:
    if client is None:
        raise TypeError("run_ab needs an LLMClient; there is no default live client in domain code")
    config = config or cfg.PipelineConfig.from_env()
    rec = TraceRecorder(
        deck_id=ctx.deck_id, prices=config.prices, max_cost_usd=config.max_cost_usd,
        inputs={"deck_id": ctx.deck_id, "deck_name": ctx.deck_name, "pipeline_version": cfg.PIPELINE_VERSION,
                "fact_pack_hash": ctx.fact_pack_hash, "profile_hash": _h(ctx.profile_block), "intent_hash": _h(ctx.intent_text),
                "deck_hash": _h("\n".join(f"{q} {n}" for n, q in ctx.deck_cards)),
                "configured_models": {**config.models, **{f"B:{k}": v for k, v in config.axis_models.items()}},
                "configured_prompt_versions": dict(config.prompt_versions)})
    try:
        brief = a_brief.run(ctx, client, config, rec)
        analysis = b_analysis.run(ctx, brief, client, config, rec)
    except BaseException as exc:
        trace = rec.finish("failed", error=f"{type(exc).__name__}: {exc}"[:500])
        if on_trace:
            on_trace(trace)
        if isinstance(exc, (KeyboardInterrupt, SystemExit)):
            raise
        raise ReviewRunFailed(f"review run failed: {type(exc).__name__}: {exc}", trace) from exc
    trace = rec.finish("ok")
    if on_trace:
        on_trace(trace)
    return RunResult(brief=brief, analysis=analysis, trace=trace)

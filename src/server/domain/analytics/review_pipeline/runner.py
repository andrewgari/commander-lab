"""Pipeline entrypoint for Pass A + Pass B (C, D, E arrive in later cards and extend ``run_ab``'s result).

``run_ab`` never returns a partial review: it returns a brief, an analysis and a trace, or it raises
``ReviewRunFailed`` carrying the (failed) trace. ``on_trace`` receives the finished trace in both
cases so the infra layer can store it even when the run fails.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from jsonschema import Draft202012Validator, FormatChecker

from src.server.domain.analytics.review_pipeline import a_brief, b_analysis, c_recommend, d_verify
from src.server.domain.analytics.review_pipeline import config as cfg
from src.server.domain.analytics.review_pipeline.context import ReviewContext
from src.server.domain.analytics.review_pipeline.llm import LLMClient
from src.server.domain.analytics.review_pipeline.trace import Trace, TraceRecorder


class ReviewRunFailed(RuntimeError):
    def __init__(self, message: str, trace: Trace):
        super().__init__(message)
        self.trace = trace


_SCHEMA_PATH = Path(__file__).resolve().parents[5] / "schemas" / "review_document.schema.json"
_SCHEMA = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
_VALIDATOR = Draft202012Validator(_SCHEMA, format_checker=FormatChecker())


def _object_value(value: Any, key: str, default: Any = None) -> Any:
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def validate_review_document(document: Mapping[str, Any]) -> dict[str, Any]:
    """Validate and return a review_document.v1 payload; invalid docs never cross this boundary."""
    payload = dict(document)
    errors = sorted(_VALIDATOR.iter_errors(payload), key=lambda error: list(map(str, error.path)))
    if errors:
        details = "; ".join(f"/{'/'.join(map(str, error.path))}: {error.message}" for error in errors)
        raise ValueError(f"assembled review_document.v1 does not match schema: {details}")
    return payload


def assemble_review_document(*, ctx: ReviewContext, fact_pack: Any, verified: Any,
                             writer_output: Mapping[str, Any], config: cfg.PipelineConfig | None = None,
                             trace: Trace | None = None, generated_at: str | None = None) -> dict[str, Any]:
    """Assemble Pass E prose with verified Pass D data and deterministic charts, then validate v1.

    ``writer_output`` owns prose-only fields. All findings, swaps, scorecard, bracket data, provenance,
    and charts are taken from code-owned pipeline outputs. A top_changes selection is reduced to surviving
    swap ids; it is null if no selection survives, never padded with invented swaps.
    """
    config = config or cfg.PipelineConfig.from_env()
    pack = fact_pack.to_dict() if hasattr(fact_pack, "to_dict") else fact_pack
    if not isinstance(pack, Mapping):
        raise TypeError("fact_pack must be a mapping or expose to_dict()")
    chart_data = pack.get("charts")
    if not isinstance(chart_data, Mapping):
        raise ValueError("fact pack has no charts object")
    card_rows = pack.get("cards") or []
    ids_by_name = {card.get("name"): card.get("oracle_id") for card in card_rows if isinstance(card, Mapping)}
    commanders = []
    for commander in ctx.commanders:
        oracle_id = ids_by_name.get(commander)
        if not isinstance(oracle_id, str):
            raise ValueError(f"fact pack has no oracle_id for commander {commander!r}")
        commanders.append({"name": commander, "oracle_id": oracle_id})

    swaps = list(_object_value(verified, "swaps", []))
    surviving_ids = {swap.get("id") for swap in swaps if isinstance(swap, Mapping)}
    top = _object_value(verified, "top_changes")
    if isinstance(top, Mapping):
        ids = [swap_id for swap_id in top.get("swap_ids", []) if swap_id in surviving_ids][:3]
        top_changes = {"swap_ids": ids, "why_these_first": top.get("why_these_first")} if ids else None
    else:
        top_changes = None

    trace_models = dict(getattr(trace, "models", {}) or {})
    trace_prompts = dict(getattr(trace, "prompt_versions", {}) or {})
    models = {"A": config.model("A"), "B": config.model("B"), "C": config.model("C"), "D": config.model("critic"),
              "E": "oracle-profile", **trace_models}
    prompt_versions = {"A": config.prompt_version("a_brief"), "B": config.prompt_version("b_analysis"),
                       "C": config.prompt_version("c_recommend"), "D": config.prompt_version("d_critic"),
                       "E": config.prompt_version("e_writer"), "critic": config.prompt_version("d_critic"),
                       **trace_prompts}
    axes = _object_value(verified, "axes", [])
    scorecard = [{"axis": axis["axis"], "score": axis["score"], "one_line": axis["one_line"],
                  "finding_ids": axis["finding_ids"]} for axis in axes]
    bracket = {"estimated": ctx.estimated_bracket, "target": ctx.target_bracket, "reasons": list(ctx.bracket_reasons)}
    document = {
        "schema_version": "review_document.v1",
        "meta": {"deck_id": ctx.deck_id, "deck_name": ctx.deck_name, "commanders": commanders,
                 "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
                 "pipeline_version": cfg.PIPELINE_VERSION, "prompt_versions": prompt_versions, "models": models,
                 "fact_pack_hash": ctx.fact_pack_hash},
        "verdict": writer_output["verdict"],
        "deck_identity": writer_output["deck_identity"],
        "scorecard": scorecard,
        "strengths": list(_object_value(verified, "strengths", [])),
        "problems": list(_object_value(verified, "problems", [])),
        "top_changes": top_changes,
        "swaps": swaps,
        "cuts_to_consider": list(_object_value(verified, "cuts_to_consider", [])),
        "bracket": bracket,
        "mana": writer_output["mana"],
        "questions_for_owner": writer_output["questions_for_owner"],
        "charts": json.loads(json.dumps(chart_data)),
        "omitted": list(_object_value(verified, "omitted", [])),
    }
    return validate_review_document(document)


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


@dataclass
class ReviewResult:
    """Pass A-D output. ``verified`` is what may ship; ``recommendations`` and ``analysis`` are the unverified drafts, kept for the trace."""
    brief: dict[str, Any]
    analysis: b_analysis.AnalysisResult
    recommendations: c_recommend.Recommendations
    verified: d_verify.VerifyResult
    trace: Trace

    def document_sections(self) -> dict[str, Any]:
        """The review_document v1 sections Passes A-D own. ``omitted`` is the verifier's removals (debug appendix only)."""
        v = self.verified
        return {"strengths": v.strengths, "problems": v.problems, "swaps": v.swaps, "cuts_to_consider": v.cuts_to_consider,
                "top_changes": v.top_changes, "omitted": v.omitted,
                "scorecard": [{"axis": a["axis"], "score": a["score"], "one_line": a["one_line"], "finding_ids": a["finding_ids"]}
                              for a in v.axes]}


def run_abcd(ctx: ReviewContext, client: LLMClient, pool: Any, config: cfg.PipelineConfig | None = None, *,
             on_trace: Callable[[Trace], None] | None = None) -> ReviewResult:
    """Brief -> analysis -> recommendations (from ``pool`` only) -> verification. Like ``run_ab`` it never returns a
    partial review: it returns a ReviewResult or raises ``ReviewRunFailed`` carrying the failed trace.

    ``pool``: a ``candidates.CandidatePool`` (or its ``to_dict()``). The critic must be a different model family
    from every generator (``ConfigError`` otherwise, before any call is made)."""
    if client is None:
        raise TypeError("run_abcd needs an LLMClient; there is no default live client in domain code")
    config = config or cfg.PipelineConfig.from_env()
    config.check_critic_is_independent()
    view = c_recommend.PoolView.from_pool(pool, max_cards=config.pool_prompt_cards)
    rec = TraceRecorder(
        deck_id=ctx.deck_id, prices=config.prices, max_cost_usd=config.max_cost_usd,
        inputs={"deck_id": ctx.deck_id, "deck_name": ctx.deck_name, "pipeline_version": cfg.PIPELINE_VERSION,
                "fact_pack_hash": ctx.fact_pack_hash, "profile_hash": _h(ctx.profile_block), "intent_hash": _h(ctx.intent_text),
                "deck_hash": _h("\n".join(f"{q} {n}" for n, q in ctx.deck_cards)),
                "pool_hash": pool.hash if hasattr(pool, "hash") else pool.get("hash"),
                "rejected_swaps": sorted([list(p) for p in ctx.rejected_swaps]),
                "configured_models": {**config.models, **{f"B:{k}": v for k, v in config.axis_models.items()}},
                "configured_prompt_versions": dict(config.prompt_versions)})
    try:
        brief = a_brief.run(ctx, client, config, rec)
        analysis = b_analysis.run(ctx, brief, client, config, rec)
        recs, _ = c_recommend.run(ctx, brief, analysis, view, client, config, rec)
        verified = d_verify.verify(ctx, brief, analysis, recs, view, client, config, rec)
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
    return ReviewResult(brief=brief, analysis=analysis, recommendations=recs, verified=verified, trace=trace)


def run_review(*, ctx: ReviewContext, fact_pack: Any, pool: Any, client: LLMClient,
               writer: Callable[[Mapping[str, Any]], str], config: cfg.PipelineConfig | None = None,
               on_trace: Callable[[Trace], None] | None = None,
               run_passes: Callable[..., Any] | None = None) -> dict[str, Any]:
    """Run A–E and return only a complete, schema-validated review_document.v1.

    The writer callback receives only the verified Pass D payload and must return its JSON text.
    It is retried a bounded number of times by Pass E's checker; a dropped/invalid writer result
    fails the run rather than returning a partial document.
    """
    from dataclasses import asdict, is_dataclass
    from src.server.domain.analytics.review_pipeline import e_writer

    config = config or cfg.PipelineConfig.from_env()
    run_passes = run_passes or run_abcd
    result = run_passes(ctx, client, pool, config, on_trace=on_trace)
    verified_obj = result.verified
    if is_dataclass(verified_obj):
        verified = asdict(verified_obj)  # type: ignore[arg-type]
    elif isinstance(verified_obj, Mapping):
        verified = dict(verified_obj)
    else:
        verified = {key: getattr(verified_obj, key) for key in
                    ("verdict", "deck_identity", "axes", "strengths", "problems", "swaps",
                     "cuts_to_consider", "top_changes", "omitted") if hasattr(verified_obj, key)}
    raw = e_writer.generate_checked(writer, verified, max_attempts=2)
    if raw is None:
        raise ReviewRunFailed("Pass E writer output failed verification after bounded retries", result.trace)
    try:
        writer_output = json.loads(raw)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ReviewRunFailed("Pass E writer returned invalid JSON", result.trace) from exc
    if not isinstance(writer_output, Mapping):
        raise ReviewRunFailed("Pass E writer output must be a JSON object", result.trace)
    return assemble_review_document(ctx=ctx, fact_pack=fact_pack, verified=verified_obj,
                                    writer_output=writer_output, config=config, trace=result.trace)


if __name__ == "__main__":
    raise SystemExit("use tests.review_evals.run_eval to execute review evals")

"""Pass B: one analysis call per axis, run in parallel, then code drops what it cannot verify.

The model returns findings; code (a) assigns ids, (b) resolves every cited card against the deck and
every cited fact key against the fact index, (c) drops any finding that fails and logs the drop to
the trace, (d) caps confidence on findings that hinge on an open question. Nothing the model says is
shipped unverified (CONTRACT §2.1-2.2).
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from src.server.domain.analytics.review_pipeline import a_brief
from src.server.domain.analytics.review_pipeline import config as cfg
from src.server.domain.analytics.review_pipeline import prompts, schemas
from src.server.domain.analytics.review_pipeline.calls import traced_call
from src.server.domain.analytics.review_pipeline.context import ReviewContext
from src.server.domain.analytics.review_pipeline.llm import LLMClient, LLMRequest, Message
from src.server.domain.analytics.review_pipeline.names import strip_typography
from src.server.domain.analytics.review_pipeline.structured import portable_schema, validate_schema
from src.server.domain.analytics.review_pipeline.trace import TraceRecorder

PASS = "B"
OPEN_QUESTION_CONFIDENCE_CAP = 0.5
_SEV_RANK = {"critical": 0, "high": 1, "med": 2, "low": 3}
DOC_FIELDS = ("id", "axis", "claim", "evidence", "severity", "confidence")


@dataclass
class AnalysisResult:
    axes: list[dict[str, Any]] = field(default_factory=list)         # scorecard rows (document `scorecard` + extras)
    strengths: list[dict[str, Any]] = field(default_factory=list)    # review_document findings
    problems: list[dict[str, Any]] = field(default_factory=list)
    sidecar: dict[str, dict[str, Any]] = field(default_factory=dict)  # finding id -> fields not in the document finding


def semantic_errors(obj: dict) -> list[str]:
    if obj.get("no_significant_problems") and obj.get("problems"):
        return ["no_significant_problems is true but problems is not empty; either list no problems or set it false"]
    return []


def _mentions(text: str, name: str) -> bool:
    t, n = strip_typography(text), strip_typography(name)
    if not n:
        return False
    flags = 0 if (" " not in n and "," not in n) else re.IGNORECASE     # one-word names are often ordinary words
    return re.search(r"(?<![\w'])" + re.escape(n) + r"(?![\w'])", t, flags) is not None


def check_finding(f: dict, ctx: ReviewContext, question_ids: set[str]) -> tuple[str, Any] | None:
    """None if the finding is verifiable, else (reason, detail). Order = cheapest, most damning first."""
    ev = f.get("evidence") or {}
    cards, keys = ev.get("cards") or [], ev.get("fact_keys") or []
    if not cards and not keys:
        return "no_evidence", None
    bad_cards = [c for c in cards if ctx.resolver.resolve(c) is None]
    if bad_cards:
        return "evidence_card_not_in_deck", bad_cards
    no_id = [ctx.resolver.resolve(c) for c in cards if not ctx.resolver.oracle_id(ctx.resolver.resolve(c))]
    if no_id:
        return "evidence_card_without_oracle_id", no_id
    missing = [k for k in keys if not ctx.index.exists(k)]
    if missing:
        unavailable = [k for k in missing if ctx.index.unavailable_reason(k)]
        return ("fact_key_unavailable", unavailable) if unavailable else ("fact_key_not_found", missing)
    unknown_q = [q for q in f.get("depends_on_questions") or [] if q not in question_ids]
    if unknown_q:
        return "unknown_question_id", unknown_q
    prose = f"{f.get('claim', '')} {f.get('fix_direction', '')}"
    foreign = sorted(n for n in ctx.foreign_names if _mentions(prose, n))
    if foreign:
        return "claim_names_card_not_in_deck", foreign
    return None


def build_request(ctx: ReviewContext, brief: dict, axis: cfg.Axis, config: cfg.PipelineConfig) -> LLMRequest:
    t = prompts.load("b_analysis", config.prompt_version("b_analysis"))
    std = prompts.quality_standard(config.prompt_version("quality_standard"))
    return LLMRequest(
        model=config.model(PASS, axis.id),
        system_blocks=(
            t.render("system.role", quality_standard=std.text),
            t.render("system.context", profile=ctx.profile_block, brief=a_brief.render_brief(brief), card_table=ctx.card_table),
            t.render(f"system.axis.{axis.id}", axis_facts=ctx.index.render(axis.fact_prefixes)),
        ),
        messages=(Message("user", t.render("user", axis_label=axis.label, deck_name=ctx.deck_name,
                                           commanders=", ".join(ctx.commanders))),),
        schema_name="axis_analysis", schema=portable_schema(schemas.ANALYSIS_SCHEMA), max_tokens=config.max_tokens_b,
        cache_blocks=2, label=f"{PASS}:{axis.id}")


def _run_axis(ctx: ReviewContext, brief: dict, axis: cfg.Axis, client: LLMClient, config: cfg.PipelineConfig,
              rec: TraceRecorder) -> tuple[cfg.Axis, dict, int]:
    req = build_request(ctx, brief, axis, config)
    validate = lambda obj: validate_schema(obj, schemas.ANALYSIS_SCHEMA) or semantic_errors(obj)
    obj, step = traced_call(client, req, validate=validate, max_repairs=config.max_repairs, rec=rec, pass_name=PASS,
                            axis=axis.id, prompt_version=config.prompt_version("b_analysis"))
    return axis, obj, step


def _materialise(ctx: ReviewContext, axis: cfg.Axis, kind: str, n: int, f: dict, question_ids: set[str]) -> tuple[dict, dict]:
    refs, seen = [], set()
    for c in f["evidence"]["cards"]:
        canon = ctx.resolver.resolve(c)
        if canon not in seen:
            seen.add(canon)
            refs.append({"name": canon, "oracle_id": ctx.resolver.oracle_id(canon)})
    conf = float(f["confidence"])
    deps = list(f.get("depends_on_questions") or [])
    if deps:
        conf = min(conf, OPEN_QUESTION_CONFIDENCE_CAP)
    doc = {"id": f"{kind}-{axis.id}-{n}", "axis": axis.id, "claim": f["claim"].strip(),
           "evidence": {"cards": refs, "fact_keys": list(dict.fromkeys(f["evidence"]["fact_keys"]))},
           "severity": f["severity"], "confidence": round(conf, 3)}
    return doc, {"fix_direction": (f.get("fix_direction") or "").strip(), "depends_on_questions": deps}


def _order(findings: list[dict]) -> list[dict]:
    return sorted(findings, key=lambda f: (_SEV_RANK[f["severity"]], -f["confidence"], f["id"]))


def run(ctx: ReviewContext, brief: dict, client: LLMClient, config: cfg.PipelineConfig, rec: TraceRecorder) -> AnalysisResult:
    axes = list(cfg.AXES)
    done: list[tuple[cfg.Axis, dict, int]] = []
    errors: list[BaseException] = []

    def go(ax: cfg.Axis):
        try:
            return _run_axis(ctx, brief, ax, client, config, rec)
        except BaseException as exc:  # noqa: BLE001  (re-raised below, after every axis has been traced)
            errors.append(exc)
            return None

    first, rest = ([axes[0]], axes[1:]) if config.warm_cache else ([], axes)
    for ax in first:                       # one call alone so the shared prefix is cached before the fan-out
        r = go(ax)
        if r:
            done.append(r)
    if not errors:
        with ThreadPoolExecutor(max_workers=len(rest) or 1) as pool:
            done.extend(r for r in pool.map(go, rest) if r)
    if errors:
        raise errors[0]

    question_ids = {q["id"] for q in brief["questions_for_owner"]}
    result = AnalysisResult()
    order = {a.id: i for i, a in enumerate(axes)}
    for axis, obj, step in sorted(done, key=lambda d: order[d[0].id]):
        kept_ids: list[str] = []
        dropped = 0
        for kind, key in (("s", "strengths"), ("p", "problems")):
            bucket = result.strengths if kind == "s" else result.problems
            for n, f in enumerate(obj[key], 1):
                fid = f"{kind}-{axis.id}-{n}"
                bad = check_finding(f, ctx, question_ids)
                if bad:
                    dropped += 1
                    rec.record_drop(step_index=step, axis=axis.id, finding={"id": fid, "kind": key, **f}, reason=bad[0], detail=bad[1])
                    rec.link_finding(fid, step, kept=False)
                    continue
                doc, side = _materialise(ctx, axis, kind, n, f, question_ids)
                bucket.append(doc)
                result.sidecar[doc["id"]] = side
                kept_ids.append(doc["id"])
                rec.link_finding(doc["id"], step, kept=True)
        result.axes.append({"axis": axis.id, "label": axis.label, "score": obj["axis_score"]["score"],
                            "one_line": obj["axis_score"]["one_line"].strip(), "finding_ids": kept_ids,
                            "no_significant_problems": bool(obj["no_significant_problems"]), "dropped": dropped})
    result.strengths = _order(result.strengths)
    result.problems = _order(result.problems)
    return result

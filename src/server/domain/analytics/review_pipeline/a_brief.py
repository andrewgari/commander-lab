"""Pass A: the deck brief. Flags ambiguity as questions; never guesses intent."""
from __future__ import annotations

import copy
from typing import Any

from src.server.domain.analytics.review_pipeline import config as cfg
from src.server.domain.analytics.review_pipeline import prompts, schemas
from src.server.domain.analytics.review_pipeline.calls import traced_call
from src.server.domain.analytics.review_pipeline.context import ReviewContext
from src.server.domain.analytics.review_pipeline.llm import LLMClient, LLMRequest, Message
from src.server.domain.analytics.review_pipeline.names import CardResolver
from src.server.domain.analytics.review_pipeline.structured import portable_schema, validate_schema
from src.server.domain.analytics.review_pipeline.trace import TraceRecorder

PASS = "A"


def _card_fields(brief: dict) -> list[tuple[str, list[str]]]:
    out = [("central_concept.key_cards", brief.get("central_concept", {}).get("key_cards", []))]
    out += [(f"win_paths[{i}].key_cards", w.get("key_cards", [])) for i, w in enumerate(brief.get("win_paths", []))]
    out += [(f"intentional_quirks[{i}].cards", q.get("cards", [])) for i, q in enumerate(brief.get("intentional_quirks", []))]
    return out


def semantic_errors(brief: dict, ctx: ReviewContext) -> list[str]:
    errs: list[str] = []
    for where, names in _card_fields(brief):
        for n in names:
            if ctx.resolver.resolve(n) is None:
                errs.append(f"{where}: {n!r} is not a card in this deck. Use only names exactly as spelled in the card table.")
    if not ctx.has_stated_intent:
        for i, w in enumerate(brief.get("win_paths", [])):
            if w.get("basis") == "stated":
                errs.append(f"win_paths[{i}].basis is 'stated' but the owner recorded no deck intent; use 'inferred_from_list'.")
    pt = brief.get("power_target", {})
    if pt.get("stated") and not ctx.has_stated_power:
        errs.append("power_target.stated is true but neither the deck intent nor the profile states a target; "
                    "set stated=false, bracket=null, and ask the owner instead.")
    if not pt.get("stated") and pt.get("bracket") is not None:
        errs.append("power_target.bracket must be null when stated is false.")
    return errs


def finalize(brief: dict, resolver: CardResolver | None = None) -> dict:
    """Canonicalise card names and assign question ids (``q-1``...). Ids are code, not model output."""
    b = copy.deepcopy(brief)
    if resolver is not None:
        canon = lambda names: [resolver.resolve(n) or n for n in names]
        b["central_concept"]["key_cards"] = canon(b["central_concept"]["key_cards"])
        for w in b["win_paths"]:
            w["key_cards"] = canon(w["key_cards"])
        for q in b["intentional_quirks"]:
            q["cards"] = canon(q["cards"])
    for i, q in enumerate(b["questions_for_owner"], 1):
        q["id"] = f"q-{i}"
    return b


def render_brief(brief: dict) -> str:
    """The brief as the plain text later passes read."""
    cc = brief["central_concept"]
    lines = [f"What the deck does: {brief['what_it_does']}",
             f"Central concept: {cc['summary']} (key cards: {', '.join(cc['key_cards']) or 'none named'})", "Win paths:"]
    for w in brief["win_paths"]:
        lines.append(f"- {w['path']} [{w['basis']}; cards: {', '.join(w['key_cards']) or 'none named'}]")
    lines.append("Success criteria:")
    lines += [f"- {c}" for c in brief["success_criteria"]] or ["- (none stated)"]
    lines.append("Intentional quirks (deliberate; do not report these as problems):")
    lines += [f"- {q['quirk']} (cards: {', '.join(q['cards']) or 'n/a'}; why deliberate: {q['why_deliberate']})"
              for q in brief["intentional_quirks"]] or ["- (none)"]
    pt = brief["power_target"]
    lines.append("Power target: " + (f"bracket {pt['bracket']}" if pt["stated"] and pt["bracket"] else
                                     ("stated: " + pt["note"] if pt["stated"] else "NOT STATED by the owner")) +
                 (f" ({pt['note']})" if pt["note"] and not pt["stated"] else ""))
    qs = brief["questions_for_owner"]
    lines.append("OPEN QUESTIONS (unanswered; treat as unknown, never assume an answer):")
    lines += [f"- {q['id']}: {q['question']} (why it matters: {q['why_asked']})" for q in qs] or ["- (none)"]
    return "\n".join(lines)


def build_request(ctx: ReviewContext, config: cfg.PipelineConfig) -> LLMRequest:
    t = prompts.load("a_brief", config.prompt_version("a_brief"))
    std = prompts.quality_standard(config.prompt_version("quality_standard"))
    summary = ctx.index.render(cfg.SUMMARY_PREFIXES)
    return LLMRequest(
        model=config.model(PASS),
        system_blocks=(t.render("system.role", quality_standard=std.text),
                       t.render("system.deck", profile=ctx.profile_block, intent=ctx.intent_text,
                                card_table=ctx.card_table, summary_facts=summary)),
        messages=(Message("user", t.render("user", deck_name=ctx.deck_name, commanders=", ".join(ctx.commanders))),),
        schema_name="deck_brief", schema=portable_schema(schemas.BRIEF_SCHEMA), max_tokens=config.max_tokens_a,
        cache_blocks=1, label=PASS)


def run(ctx: ReviewContext, client: LLMClient, config: cfg.PipelineConfig, rec: TraceRecorder) -> dict[str, Any]:
    req = build_request(ctx, config)
    validate = lambda obj: validate_schema(obj, schemas.BRIEF_SCHEMA) or semantic_errors(obj, ctx)
    obj, _ = traced_call(client, req, validate=validate, max_repairs=config.max_repairs, rec=rec,
                         pass_name=PASS, axis=None, prompt_version=config.prompt_version("a_brief"))
    return finalize(obj, ctx.resolver)

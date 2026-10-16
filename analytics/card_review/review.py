"""Single Card Review orchestration: gates -> debate -> judge -> (remove) replacements. See docs/CARD_REVIEW.md.

`run_review` is pure orchestration over an EvaluationResult; all I/O (LLM, card facts, ownership) is injected.
"""
from __future__ import annotations

from datetime import date
from typing import Callable, Dict, Optional, Sequence

from analytics.card_eval import EvaluationResult
from analytics.edhrec_inclusion import name_key

from . import config as cfg
from .debate import DebateConfig, Event, final_confidence, run_debate, run_judge
from .llm import DebateLLM
from .packet import ReviewContext, band, build_packet, is_pet, p_keep
from .replacements import Annotator, FactLookup, suggest_replacements


class ReviewNotAllowed(ValueError):
    """The card can't be reviewed (it's the commander)."""


def _rubric(ev: EvaluationResult) -> dict:
    return {"strength": ev.strength, "band": band(ev.strength), "p_keep_prior": round(p_keep(ev.strength), 2),
            "breakdown": ev.strength_breakdown.model_dump()}


def _roles(ev: EvaluationResult) -> dict:
    return {"primary": ev.roles[0].role if ev.roles else None,
            "secondary": [s.role for s in ev.roles[1:]], "sources": {s.role: s.source for s in ev.roles}}


def run_review(ev: EvaluationResult, ctx: ReviewContext, deck_names: Sequence[str], *, llm: DebateLLM,
               fact_lookup: FactLookup, rounds: int = cfg.DEFAULT_ROUNDS, annotate: Optional[Annotator] = None,
               on_event: Optional[Event] = None) -> dict:
    emit = on_event or (lambda e: None)
    name = ev.card.name
    if name_key(name) in {name_key(c) for c in ev.commanders}:
        raise ReviewNotAllowed("the commander can't be reviewed")

    base = {"card": ev.card.model_dump(), "rubric": _rubric(ev), "roles": _roles(ev), "transcript": [],
            "discarded": [], "replacement_brief": None, "replacements": [], "flags": [], "provisional": False,
            "fallback": False, "gate": None, "rounds": 0, "warnings": list(ev.warnings),
            "data_as_of": {"generated": date.today().isoformat()}}
    emit({"stage": "evaluated", "strength": ev.strength})

    # --- gates (§0.2): no LLM calls -------------------------------------------------------------
    if is_pet(name, ctx):
        emit({"stage": "gate", "gate": "pet_card"})
        return {**base, "verdict": "keep", "confidence": 1.0, "gate": "pet_card",
                "key_reasons": ["Pet card (Deck Journal): protected"], "deciding_tier": "gate"}
    ci_bad = ctx.commander_identity is not None and bool(set(ev.card.color_identity or "") - {"C"} - ctx.commander_identity)
    if not ev.card.legal or ci_bad:
        emit({"stage": "gate", "gate": "illegal"})
        why = "banned or not legal in Commander" if not ev.card.legal else "outside the commander's colour identity"
        brief = {"mode": "like_for_like", "target_roles": [r.role for r in ev.roles[:1]], "avoid": []}
        res = suggest_replacements(ev, brief, ctx, deck_names, fact_lookup, annotate)
        return {**base, "verdict": "remove", "confidence": cfg.GATE_ILLEGAL_CONFIDENCE, "gate": "illegal",
                "key_reasons": [f"Illegal: {why}"], "deciding_tier": "gate", "replacement_brief": brief,
                "replacements": res["suggestions"], "replacement_meta": _meta(res), "flags": res["flags"]}

    # --- debate ---------------------------------------------------------------------------------
    dcfg = DebateConfig(rounds=rounds)
    packet = build_packet(ev, ctx)
    transcript = run_debate(llm, packet, ctx, name, dcfg, emit)
    emit({"stage": "judging"})
    v = run_judge(llm, ev, packet, transcript, ctx, dcfg)
    edhrec_missing = ev.edhrec.status != "ok"
    unconfirmed = not ctx.journal_confirmed
    conf = final_confidence(v["verdict"], ev.strength, v["judge_confidence"], edhrec_missing=edhrec_missing,
                            journal_unconfirmed=unconfirmed, fallback=v["fallback"])
    flags = []
    if edhrec_missing:
        flags.append("edhrec_data_missing")
    if unconfirmed:
        flags.append("journal_unconfirmed")
    out = {**base, "verdict": v["verdict"], "confidence": conf, "key_reasons": v["key_reasons"],
           "provisional": v["verdict"] == "remove" and unconfirmed, "deciding_tier": v["deciding_tier"],
           "fallback": v["fallback"], "transcript": transcript, "discarded": v["discarded"],
           "winning_claims": v["winning_claims"], "flags": flags, "rounds": dcfg.rounds,
           "packet": packet}

    # --- replacements ---------------------------------------------------------------------------
    if v["verdict"] == "remove":
        emit({"stage": "replacements"})
        res = suggest_replacements(ev, v["replacement_brief"], ctx, deck_names, fact_lookup, annotate)
        out.update(replacement_brief=v["replacement_brief"], replacements=res["suggestions"],
                   replacement_meta=_meta(res))
        out["flags"] = flags + res["flags"]
    return out


def _meta(res: dict) -> dict:
    return {"mode": res["mode"], "target_roles": res["target_roles"], "pool_exhausted": res["pool_exhausted"],
            "dropped": res["dropped"]}

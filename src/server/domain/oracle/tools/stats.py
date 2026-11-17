"""deck_stats: curve / bracket / power / composition from the existing analytics."""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from ...draft import brew_power
from ...analytics import bracket_eval, review_metrics as rm
from ..registry import Tool, ToolError
from .context import ToolContext
from .deck import deck_entries

log = logging.getLogger(__name__)

SECTIONS = ("curve", "bracket", "power", "composition")
_BRACKET_KEYS = {"estimated_bracket", "estimated_bracket_name", "estimated_bracket_reason", "minimum_bracket",
                 "target_bracket", "aligned", "conflicts", "explanation"}
_POWER_KEYS = ("estimated_power_level", "power_source", "target_power_level", "power_delta", "power_aligned")


def _unavailable(reason: str) -> Dict[str, Any]:
    return {"status": "unavailable", "reason": reason}


def _target_deck(args: dict, ctx: ToolContext) -> Dict[str, Any]:
    override = args.get("cards")
    if override is None:
        return dict(ctx.deck)
    if not ctx.allow_hypothetical:
        raise ToolError("not_allowed", "the cards override is only available when building a deck")
    known = {c.get("name"): c for c in ctx.deck.get("cards") or [] if c.get("name")}
    # keep enrichment (type, cmc, tags, text) for cards already in the deck; only quantity changes
    return {**ctx.deck, "cards": [{**known.get(c["name"], {}), "name": c["name"], "quantity": c.get("qty", 1)}
                                  for c in override]}


def _metrics(deck: Dict[str, Any], fp: str, is_override: bool, ctx: ToolContext):
    if not is_override and ctx.metrics is not None:
        return ctx.metrics
    key = ("metrics", fp)
    if key not in ctx.memo:
        if ctx.compute_metrics is None:
            return None
        ctx.memo[key] = ctx.compute_metrics(deck)
    return ctx.memo[key]


def _evaluation(deck: Dict[str, Any], fp: str, is_override: bool, ctx: ToolContext):
    """Returns (evaluation, inputs) where inputs is "live" or "snapshot"."""
    key = ("bracket_eval", fp, is_override)
    if key not in ctx.memo:
        ev, inputs = None, "snapshot"
        if not is_override and ctx.bracket_eval is not None:
            try:
                ev = ctx.bracket_eval(deck)
            except Exception:
                log.warning("live bracket evaluation failed; using snapshot inputs", exc_info=True)
                ev = None
            if ev is not None:
                inputs = "live"
        if ev is None:
            gcs = None if ctx.game_changers is None else list(ctx.game_changers)
            ev = bracket_eval.evaluate_bracket(deck, ctx.journal, {"game_changers": gcs})
        ctx.memo[key] = (ev, inputs)
    return ctx.memo[key]


def _section(name: str, deck: Dict[str, Any], fp: str, is_override: bool, ctx: ToolContext) -> Dict[str, Any]:
    if name in ("curve", "composition"):
        m = _metrics(deck, fp, is_override, ctx)
        if m is None:
            return _unavailable("metrics are not configured")
        if name == "curve":
            return dict(m.get("curve_tempo") or _unavailable("no curve data"))
        comp = m.get("composition") or {}
        return {"rows": comp.get("rows"), "total_cards": comp.get("total_cards"),
                "unknown_cards": comp.get("unknown_cards")}
    ev, inputs = _evaluation(deck, fp, is_override, ctx)
    if name == "bracket":
        out = ev.model_dump(include=_BRACKET_KEYS)
        out["floors"] = (ev.findings or {}).get("floors")
        out["inputs"] = inputs
        out["combos_checked"] = bool(((ev.findings or {}).get("two_card_combos") or {}).get("checked"))
        if ctx.game_changers is None:
            out["game_changers"] = "unavailable"
        return out
    out = {k: getattr(ev, k) for k in _POWER_KEYS}
    if ev.power_source == "bracket_band":
        out["note"] = "no Commander Salt or engine estimate; this is the bracket band midpoint"
    if ctx.allow_hypothetical:
        cards = list(deck.get("cards") or [])
        have = {c.get("name") for c in cards}
        cards += [{"name": n, "quantity": 1} for n in deck.get("commanders") or [] if n and n not in have]
        est = brew_power.estimate_power_level(cards,
                                              game_changers=None if ctx.game_changers is None else list(ctx.game_changers))
        out["brew_estimate"] = {"power": est.get("power"), "breakdown": est.get("breakdown")}
    return out


def _deck_stats(args: dict, ctx: ToolContext) -> dict:
    deck = _target_deck(args, ctx)
    is_override = args.get("cards") is not None
    fp = rm.fingerprint(deck)
    out: Dict[str, Any] = {}
    for s in args["sections"]:
        key = ("stats", fp, s, ctx.allow_hypothetical, is_override)
        if key not in ctx.memo:
            ctx.memo[key] = _section(s, deck, fp, is_override, ctx)
        out[s] = ctx.memo[key]
    return out


def deck_stats_tool() -> Tool:
    return Tool(
        name="deck_stats",
        description=("Returns computed deck statistics, one key per requested section: curve (tempo, commander on "
                     "curve, mana), bracket (estimated vs target Commander bracket, floors), power (estimated power "
                     "level) and composition (counts vs template). Call before quoting any number."),
        parameters={"type": "object",
                    "properties": {
                        "sections": {"type": "array", "items": {"type": "string", "enum": list(SECTIONS)},
                                     "minItems": 1, "maxItems": len(SECTIONS), "uniqueItems": True},
                        "cards": {"type": "array", "minItems": 1, "maxItems": 120,
                                  "items": {"type": "object",
                                            "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 200},
                                                           "qty": {"type": "integer", "minimum": 1, "maximum": 99}},
                                            "required": ["name"], "additionalProperties": False}}},
                    "required": ["sections"], "additionalProperties": False},
        handler=_deck_stats,
    )

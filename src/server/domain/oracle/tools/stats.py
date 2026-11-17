"""deck_stats: curve / bracket / power / composition from the existing analytics."""
from __future__ import annotations

from typing import Any, Dict, List

from ...draft import brew_power
from ...analytics import bracket_eval, review_metrics as rm
from ..registry import Tool, ToolError
from .context import ToolContext
from .deck import deck_entries

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


def _evaluation(deck: Dict[str, Any], fp: str, ctx: ToolContext):
    key = ("bracket_eval", fp)
    if key not in ctx.memo:
        gcs = None if ctx.game_changers is None else list(ctx.game_changers)
        ctx.memo[key] = bracket_eval.evaluate_bracket(deck, ctx.journal, {"game_changers": gcs})
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
    ev = _evaluation(deck, fp, ctx)
    if name == "bracket":
        out = ev.model_dump(include=_BRACKET_KEYS)
        out["floors"] = (ev.findings or {}).get("floors")
        if ctx.game_changers is None:
            out["game_changers"] = "unavailable"
        return out
    out = {k: getattr(ev, k) for k in _POWER_KEYS}
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
        key = ("stats", fp, s, ctx.allow_hypothetical)
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

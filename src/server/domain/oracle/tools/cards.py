"""card_lookup: Scryfall card facts for named cards (never from model memory)."""
from __future__ import annotations

from typing import Any, Dict

from ...analytics import brackets
from ..registry import Tool, ToolError
from .context import ToolContext

MAX_NAMES_PER_CALL = 20
MAX_NAMES_PER_RUN = 60
MAX_CALLS = 4
_COUNT = "card_lookup:names"
_CACHE = "card_lookup:cards"


def _oracle_text(card: Dict[str, Any]) -> Any:
    if card.get("oracle_text") is not None:
        return card.get("oracle_text")
    faces = [f.get("oracle_text") for f in card.get("card_faces") or [] if f.get("oracle_text")]
    return "\n//\n".join(faces) if faces else None


def _mana_cost(card: Dict[str, Any]) -> Any:
    if card.get("mana_cost"):
        return card.get("mana_cost")
    faces = [f.get("mana_cost") for f in card.get("card_faces") or [] if f.get("mana_cost")]
    return " // ".join(faces) if faces else card.get("mana_cost")


def _row(query: str, card: Dict[str, Any], ctx: ToolContext) -> Dict[str, Any]:
    gc = None if ctx.game_changers is None else brackets.is_game_changer(card.get("name") or query, ctx.game_changers)
    return {
        "query": query, "found": True, "name": card.get("name"), "mana_cost": _mana_cost(card),
        "cmc": card.get("cmc"), "type_line": card.get("type_line"), "oracle_text": _oracle_text(card),
        "color_identity": card.get("color_identity"),
        "commander_legal": (card.get("legalities") or {}).get("commander") == "legal",
        "game_changer": gc,
    }


def _card_lookup(args: dict, ctx: ToolContext) -> dict:
    if ctx.scryfall_batch is None:
        raise ToolError("unavailable", "card lookup is not configured")
    names = list(args["names"])
    seen = ctx.memo.setdefault(_COUNT, 0)
    if seen + len(names) > MAX_NAMES_PER_RUN:
        raise ToolError("call_limit", f"card_lookup may look up at most {MAX_NAMES_PER_RUN} names per run "
                                      f"({max(MAX_NAMES_PER_RUN - seen, 0)} left)")
    ctx.memo[_COUNT] = seen + len(names)
    cache: Dict[str, Any] = ctx.memo.setdefault(_CACHE, {})
    need = [n for n in names if n.lower() not in cache]
    if need:
        got = ctx.scryfall_batch(need) or {}
        for n in need:
            cache[n.lower()] = got.get(n.lower())
    return {"cards": [_row(n, cache[n.lower()], ctx) if cache.get(n.lower())
                      else {"query": n, "found": False, "reason": "not on Scryfall"} for n in names]}


def card_lookup_tool() -> Tool:
    return Tool(
        name="card_lookup",
        description=("Returns Scryfall facts (mana cost, mana value, type line, oracle text, color identity, "
                     "commander legality, Game Changer flag) for 1-20 exact card names. Call for any card whose "
                     "text you want to quote; never rely on memory."),
        parameters={"type": "object",
                    "properties": {"names": {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 200},
                                             "minItems": 1, "maxItems": MAX_NAMES_PER_CALL, "uniqueItems": True}},
                    "required": ["names"], "additionalProperties": False},
        handler=_card_lookup,
        max_calls=MAX_CALLS,
    )

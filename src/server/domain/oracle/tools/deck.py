"""get_deck: the deck under review, straight from ToolContext."""
from __future__ import annotations

from typing import Any, Dict

from ...analytics import review_metrics as rm
from ..registry import Tool
from .context import ToolContext


def deck_entries(deck: Any) -> Dict[str, int]:
    """name -> qty over cards, commanders defaulting to 1 (same rule as the deck fingerprint)."""
    qty: Dict[str, int] = {}
    for c in deck.get("cards") or []:
        n = c.get("name")
        if n:
            try:
                q = int(c.get("quantity") or c.get("qty") or 1)
            except (TypeError, ValueError):
                q = 1
            qty[n] = qty.get(n, 0) + max(q, 1)
    for n in deck.get("commanders") or []:
        if n:
            qty.setdefault(n, 1)
    return qty


def _get_deck(args: dict, ctx: ToolContext) -> dict:
    deck = ctx.deck
    qty = deck_entries(deck)
    return {
        "name": deck.get("name"),
        "registry_id": deck.get("registry_id"),
        "commanders": [c for c in deck.get("commanders") or [] if c],
        "cards": [{"name": n, "qty": q} for n, q in sorted(qty.items())],
        "total": sum(qty.values()),
        "fingerprint": rm.fingerprint(deck),
        "journal": ctx.journal,
    }


def get_deck_tool() -> Tool:
    return Tool(
        name="get_deck",
        description=("Returns the deck under review: name, commanders, every card with quantity, total card count, "
                     "a content fingerprint and the player's journal (goals/targets) if any. Call first."),
        parameters={"type": "object", "properties": {}, "additionalProperties": False},
        handler=_get_deck,
    )

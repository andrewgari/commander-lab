"""Pure fallback predicate for "can this card be a commander?" (card-search §3.1.2).

The source of truth is Scryfall ``is:commander`` (see ``commander_set``). This
predicate is only the LAST fallback, for when the eligible set is missing and
cannot be built. It reads a plain mapping so it has no ORM or Redis coupling.

Known gap: the ``cards`` mirror stores no power/toughness and no layout, so a
P/T-less Spacecraft (The Eternity Elevator), a Grist-style walker with no "can
be your commander" text, and meld results (Brisela) are misclassified. Callers
mark results produced through this predicate ``degraded``.
"""
from __future__ import annotations

from typing import Any, Mapping


def front_face(type_line: str) -> str:
    """The face that sits in the command zone: everything before `` // ``."""
    return (type_line or "").split(" // ")[0]


def is_commander_eligible(card: Mapping[str, Any]) -> bool:
    legalities = card.get("legalities") or {}
    if legalities.get("commander") != "legal":
        return False
    front = front_face(card.get("type_line") or "").casefold()
    legendary = "legendary" in front
    if legendary and "creature" in front:
        return True
    if "can be your commander" in (card.get("oracle_text") or "").casefold():
        return True
    if legendary and any(t in front for t in ("background", "vehicle", "spacecraft")):
        return True
    return False

"""Canonical deck slot model for Commander Lab.

A deck is organized into named slots. This module is the single source of
truth for slot identity, consumed by the decklist parser (decklist_parser),
the intake pipeline (deck_intake), provider normalization (providers/*),
the registry (registry.py), and the slot-legality validator
(deck_validator.py, which gates card type classes per slot).

Slots:
    commander        the commander(s) of the deck
    mainboard        the 99 (the main deck)
    maybeboard       cards under consideration (default remap target)
    considering      cards being considered (existing category)
    planar_deck      Planechase variant deck (planes + phenomenons)
    scheme_deck      Archenemy variant deck (schemes)
    attraction_deck  Unfinity Attraction deck

"Sideboard" is a REMOVED concept. Inputs that still carry a Sideboard
section/category/board are remapped to maybeboard by default, and every
such remap is flagged (SIDEBOARD_MAPPED_FLAG) plus logged so the default
is visible and confirmable by the user.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional

logger = logging.getLogger("commander_lab.deck_slots")


class DeckSlot:
    """Canonical deck slot names."""

    COMMANDER = "commander"
    MAINBOARD = "mainboard"
    MAYBEBOARD = "maybeboard"
    CONSIDERING = "considering"
    PLANAR_DECK = "planar_deck"
    SCHEME_DECK = "scheme_deck"
    ATTRACTION_DECK = "attraction_deck"

    VALID = frozenset({
        COMMANDER,
        MAINBOARD,
        MAYBEBOARD,
        CONSIDERING,
        PLANAR_DECK,
        SCHEME_DECK,
        ATTRACTION_DECK,
    })


#: Slots that hold a separate variant deck rather than main-deck cards.
VARIANT_SLOTS = frozenset({
    DeckSlot.PLANAR_DECK,
    DeckSlot.SCHEME_DECK,
    DeckSlot.ATTRACTION_DECK,
})

#: Slots that are never part of the 100-card main deck.
NON_MAIN_SLOTS = frozenset(DeckSlot.VALID) - {DeckSlot.COMMANDER, DeckSlot.MAINBOARD}

#: Parse/serialization flag emitted when a Sideboard section was remapped
#: to Maybeboard by default. Visible in parse results so the default is
#: confirmable.
SIDEBOARD_MAPPED_FLAG = "sideboard_mapped_to_maybeboard"

#: Display labels for slots (title-cased provider/Archidekt-style spelling).
SLOT_LABELS = {
    DeckSlot.COMMANDER: "Commander",
    DeckSlot.MAINBOARD: "Mainboard",
    DeckSlot.MAYBEBOARD: "Maybeboard",
    DeckSlot.CONSIDERING: "Considering",
    DeckSlot.PLANAR_DECK: "Planar Deck",
    DeckSlot.SCHEME_DECK: "Scheme Deck",
    DeckSlot.ATTRACTION_DECK: "Attraction Deck",
}

#: Legacy section/board names that map onto canonical slots. Anything not
#: listed here and not already canonical is unknown (returns None).
_SLOT_ALIASES = {
    "commander": DeckSlot.COMMANDER,
    "commanders": DeckSlot.COMMANDER,
    "cmdr": DeckSlot.COMMANDER,
    "mainboard": DeckSlot.MAINBOARD,
    "maindeck": DeckSlot.MAINBOARD,
    "main": DeckSlot.MAINBOARD,
    "deck": DeckSlot.MAINBOARD,
    "library": DeckSlot.MAINBOARD,
    "maybeboard": DeckSlot.MAYBEBOARD,
    "maybe": DeckSlot.MAYBEBOARD,
    "considering": DeckSlot.CONSIDERING,
    "planar_deck": DeckSlot.PLANAR_DECK,
    "planar deck": DeckSlot.PLANAR_DECK,
    "planar": DeckSlot.PLANAR_DECK,
    "planes": DeckSlot.PLANAR_DECK,
    "planechase": DeckSlot.PLANAR_DECK,
    "scheme_deck": DeckSlot.SCHEME_DECK,
    "scheme deck": DeckSlot.SCHEME_DECK,
    "schemes": DeckSlot.SCHEME_DECK,
    "scheme": DeckSlot.SCHEME_DECK,
    "archenemy": DeckSlot.SCHEME_DECK,
    "attraction_deck": DeckSlot.ATTRACTION_DECK,
    "attraction deck": DeckSlot.ATTRACTION_DECK,
    "attractions": DeckSlot.ATTRACTION_DECK,
    "attraction": DeckSlot.ATTRACTION_DECK,
    # Removed concept: sideboard remaps to maybeboard by default.
    "sideboard": DeckSlot.MAYBEBOARD,
    "sideboards": DeckSlot.MAYBEBOARD,
    "side": DeckSlot.MAYBEBOARD,
    "sb": DeckSlot.MAYBEBOARD,
}

#: Alias spellings that denote the removed Sideboard concept. Used to
#: detect when the sideboard->maybeboard remap was actually applied (and
#: therefore must be flagged).
SIDEBOARD_ALIASES = frozenset({"sideboard", "sideboards", "side", "sb"})

#: Card type class -> the variant slot that type belongs to. Drives
#: type-based slot assignment in provider normalization and (in the
#: validator task) per-slot legality gating.
TYPE_CLASS_TO_SLOT = {
    "plane": DeckSlot.PLANAR_DECK,
    "phenomenon": DeckSlot.PLANAR_DECK,
    "scheme": DeckSlot.SCHEME_DECK,
    "attraction": DeckSlot.ATTRACTION_DECK,
}


def normalize_slot(name: Any) -> Optional[str]:
    """Map a section/category/board spelling to a canonical slot name.

    Returns None for unknown values. Legacy 'Sideboard' spellings return
    maybeboard (the default remap); use is_sideboard_spelling() to detect
    that a remap happened so it can be flagged.
    """
    if not isinstance(name, str):
        return None
    key = " ".join(name.strip().lower().split())
    if key in DeckSlot.VALID:
        return key
    return _SLOT_ALIASES.get(key)


def is_sideboard_spelling(name: Any) -> bool:
    """True when `name` is a spelling of the removed Sideboard concept."""
    if not isinstance(name, str):
        return False
    key = " ".join(name.strip().lower().replace("-", "_").split())
    return key in SIDEBOARD_ALIASES


def slot_for_types(types: Iterable[str]) -> Optional[str]:
    """Variant slot for a card's type line data, e.g. ['Plane'] -> planar_deck.

    Returns None for cards with no variant type class (ordinary main-deck
    cards).
    """
    lowered = {str(t).strip().lower() for t in (types or [])}
    for type_class, slot in TYPE_CLASS_TO_SLOT.items():
        if type_class in lowered:
            return slot
    return None


def empty_slots() -> Dict[str, List[Dict[str, Any]]]:
    """A fresh {slot: []} mapping covering every canonical slot."""
    return {slot: [] for slot in sorted(DeckSlot.VALID)}


def classify_card_slot(
    categories: Optional[Iterable[str]],
    types: Optional[Iterable[str]],
) -> "tuple[str, bool]":
    """Classify one provider card (Archidekt-style categories + oracle types)
    into a canonical slot.

    Returns (slot, remapped_from_sideboard). ``slot`` is DeckSlot.MAINBOARD
    for an ordinary main-deck card; commanders are detected by the caller
    before calling this. Precedence:
      1. explicit structural categories (Sideboard -> maybeboard, remapped;
         Maybeboard; Considering)
      2. card type class (Plane/Phenomenon -> planar_deck, Scheme ->
         scheme_deck, Attraction -> attraction_deck)
      3. variant category labels ("Planar Deck", "Schemes", ...)
    """
    cats = [str(c) for c in (categories or [])]
    lowered = {c.strip().lower() for c in cats}
    if any(is_sideboard_spelling(c) for c in cats):
        return DeckSlot.MAYBEBOARD, True
    if "maybeboard" in lowered:
        return DeckSlot.MAYBEBOARD, False
    if "considering" in lowered:
        return DeckSlot.CONSIDERING, False
    type_slot = slot_for_types(types or [])
    if type_slot is not None:
        return type_slot, False
    for cat in cats:
        slot = normalize_slot(cat)
        if slot in VARIANT_SLOTS:
            return slot, False
    return DeckSlot.MAINBOARD, False


def normalize_slots(raw: Optional[Mapping[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    """Normalize a serialized slots mapping onto canonical slot names.

    Unknown slot keys are remapped through normalize_slot() when possible;
    entries under the removed 'sideboard' key land in maybeboard (no data
    loss). Returns a mapping covering every canonical slot.
    """
    slots = empty_slots()
    for key, entries in (raw or {}).items():
        slot = key if key in DeckSlot.VALID else normalize_slot(key)
        if slot is None:
            # Unrecognized slot key: keep the cards rather than dropping
            # them (no data loss) by parking them in maybeboard.
            slot = DeckSlot.MAYBEBOARD
        for entry in entries or []:
            if isinstance(entry, Mapping) and entry.get("name"):
                row: Dict[str, Any] = {
                    "name": entry["name"],
                    "quantity": int(entry.get("quantity") or 1),
                }
                # Preserve provenance markers (e.g. remapped_from=sideboard)
                # so the confirmable sideboard->maybeboard default survives
                # serialization round-trips.
                if entry.get("remapped_from"):
                    row["remapped_from"] = entry["remapped_from"]
                slots[slot].append(row)
    return slots


def flag_sideboard_remap(flags: List[str], context: str, count: int = 1) -> None:
    """Record the visible sideboard->maybeboard remap flag (once per list)
    and emit a log line so the default mapping is confirmable."""
    logger.info(
        "Sideboard mapped to Maybeboard by default (%s, %d card(s)); "
        "rename the section to 'Maybeboard' to keep it explicit.",
        context,
        count,
    )
    if SIDEBOARD_MAPPED_FLAG not in flags:
        flags.append(SIDEBOARD_MAPPED_FLAG)


def migrate_serialized_deck(deck: Mapping[str, Any]) -> Dict[str, Any]:
    """Upgrade a serialized deck/intake dict to the slot model (in-memory).

    - Ensures a normalized 'slots' mapping exists.
    - A legacy top-level 'sideboard' list is folded into the maybeboard
      slot (no data loss) and removed.
    - All other keys are preserved untouched.

    Idempotent: running it on an already-migrated dict is a no-op apart
    from slot normalization.
    """
    out = dict(deck or {})
    raw_slots: Dict[str, Any] = dict(out.get("slots") or {})
    legacy_sideboard = out.pop("sideboard", None)
    if legacy_sideboard:
        raw_slots.setdefault(DeckSlot.MAYBEBOARD, [])
        raw_slots[DeckSlot.MAYBEBOARD] = list(raw_slots[DeckSlot.MAYBEBOARD]) + [
            e for e in legacy_sideboard if isinstance(e, Mapping) and e.get("name")
        ]
    out["slots"] = normalize_slots(raw_slots)
    return out

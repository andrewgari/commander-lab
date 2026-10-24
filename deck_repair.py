"""
Deterministic repair helpers for trivial deck_validator failures.

Kept separate from validation on purpose: validation never mutates anything.
Each helper returns a NEW DraftDeck plus a list of human-readable actions taken.
`auto_repair` applies them in a fixed order and is idempotent. It only fixes
what can be fixed mechanically; anything else (illegal commanders, partner
problems, unknown cards, shared-pool conflicts) is left for the LLM repair loop
or the caller.

Order in auto_repair:
    1. drop_invalid_cards  -- off-identity / banned / not-legal / unknown / bad-qty
    2. dedupe_singleton    -- clamp non-exempt cards to their allowed copies
    3. fit_to_size         -- trim (basics first, then from the end) or pad with
                              basics split evenly over the commander identity
"""
from __future__ import annotations

from typing import List, Tuple

from deck_slots import DeckSlot
from deck_validator import (
    COLORS, COMMANDER_BANNED, DECK_SIZE, BASIC_LAND_NAMES, CardCatalog, DraftCard,
    DraftDeck, _allowed_copies, _is_int, normalize_name, slot_gate,
)

_COLOR_TO_BASIC = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}

Repair = Tuple[DraftDeck, List[str]]


def _clone(deck: DraftDeck, cards: List[DraftCard]) -> DraftDeck:
    # Repair only ever rewrites the mainboard; named slots pass through.
    return DraftDeck(commanders=list(deck.commanders), cards=cards, name=deck.name,
                     slots={slot: list(entries) for slot, entries in deck.slots.items()})


def _identity(deck: DraftDeck, catalog: CardCatalog) -> frozenset:
    ci = frozenset()
    for c in deck.commanders:
        info = catalog.get(c)
        if info is not None:
            ci |= info.color_identity
    return ci


def drop_invalid_cards(deck: DraftDeck, catalog: CardCatalog) -> Repair:
    ci = _identity(deck, catalog)
    banned = {normalize_name(n) for n in COMMANDER_BANNED}
    kept: List[DraftCard] = []
    actions: List[str] = []
    for card in deck.cards:
        info = catalog.get(card.name)
        reason = None
        if not _is_int(card.quantity) or card.quantity < 1:
            reason = "invalid quantity"
        elif info is None:
            reason = "unknown card"
        elif info.color_identity - ci:
            reason = "outside color identity"
        elif info.commander_legality in ("banned", "not_legal"):
            reason = f"{info.commander_legality} in Commander"
        elif info.commander_legality is None and normalize_name(card.name) in banned:
            reason = "banned in Commander"
        elif info.commander_legality is None:
            reason = "legality unknown (card record needs re-sync)"
        elif not slot_gate(info, DeckSlot.MAINBOARD).legal:
            reason = slot_gate(info, DeckSlot.MAINBOARD).reason
        if reason:
            actions.append(f"removed {card.name} x{card.quantity}: {reason}")
        else:
            kept.append(card)
    return _clone(deck, kept), actions


def dedupe_singleton(deck: DraftDeck, catalog: CardCatalog) -> Repair:
    """Merge repeated entries and clamp each card to its allowed copies. Copies
    of a card that match a commander also count against the limit."""
    seen: dict = {c_key: 1 for c_key in (normalize_name(c) for c in deck.commanders)}
    kept: List[DraftCard] = []
    actions: List[str] = []
    for card in deck.cards:
        if not _is_int(card.quantity) or card.quantity < 1:
            kept.append(card)
            continue
        key = normalize_name(card.name)
        _, limit = _allowed_copies(card.name, catalog.get(card.name))
        have = seen.get(key, 0)
        room = card.quantity if limit is None else max(0, limit - have)
        take = min(card.quantity, room)
        if take < card.quantity:
            actions.append(f"reduced {card.name} by {card.quantity - take} (singleton)")
        if take:
            seen[key] = have + take
            kept.append(DraftCard(card.name, take))
    return _clone(deck, kept), actions


def fit_to_size(deck: DraftDeck, catalog: CardCatalog) -> Repair:
    """Trim or pad to exactly DECK_SIZE (commanders included)."""
    actions: List[str] = []
    cards = [DraftCard(c.name, c.quantity) for c in deck.cards
             if _is_int(c.quantity) and c.quantity >= 1]
    total = len(deck.commanders) + sum(c.quantity for c in cards)

    if total > DECK_SIZE:
        excess = total - DECK_SIZE

        def basic(c: DraftCard) -> bool:
            info = catalog.get(c.name)
            return bool(info and info.is_basic)

        # Basics first (from the end), then everything else from the end.
        for pass_basics in (True, False):
            for card in reversed(cards):
                if excess == 0:
                    break
                if basic(card) != pass_basics:
                    continue
                cut = min(card.quantity, excess)
                card.quantity -= cut
                excess -= cut
                actions.append(f"trimmed {card.name} x{cut}")
        cards = [c for c in cards if c.quantity > 0]
    elif total < DECK_SIZE:
        need = DECK_SIZE - total
        colors = [c for c in COLORS if c in _identity(deck, catalog)]
        basics = [_COLOR_TO_BASIC[c] for c in colors] or ["Wastes"]
        per = {b: need // len(basics) for b in basics}
        for b in basics[: need % len(basics)]:
            per[b] += 1
        for b in basics:
            if not per[b]:
                continue
            for card in cards:
                if normalize_name(card.name) == normalize_name(b):
                    card.quantity += per[b]
                    break
            else:
                cards.append(DraftCard(b, per[b]))
            actions.append(f"added {b} x{per[b]}")
    return _clone(deck, cards), actions


def auto_repair(deck: DraftDeck, catalog: CardCatalog) -> Repair:
    actions: List[str] = []
    for step in (drop_invalid_cards, dedupe_singleton, fit_to_size):
        deck, acts = step(deck, catalog)
        actions.extend(acts)
    return deck, actions

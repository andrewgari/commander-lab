"""
Brew output gate: the single place that guarantees a Brew result is a complete,
legal, fully-named Commander deck.

Every Brew build goes through `gate_draft` (see brew.execute_build) before the
draft is stored or returned. It

  1. sanitises names: blank names are dropped; UUID-like names (oracle ids /
     instance ids that leaked through as "names") are resolved through the card
     catalog (`card:{oracle_id}`) or dropped. A GUID is never emitted.
  2. repairs: drops cards that are unknown / off-identity / banned / not legal,
     clamps singleton, trims an oversized deck.
  3. fills to exactly 100 (commander(s) + 99): caller candidates (synergy /
     EDHREC / analytics, any of which may be empty), then legal on-colour
     staples, then basic lands matching the colour identity.
  4. verifies with the shared validator (deck_validator) plus GUID/name checks.
     Anything still invalid raises BrewInvalidDeckError. Never returns a bad deck.

Pure except for the optional `r.get("card:{id}")` name lookup.
"""
from __future__ import annotations

import copy
import json
import re
from typing import Any, Dict, Iterable, List, Optional

from src.server.domain.decks.deck_slots import DeckSlot
from src.server.domain.draft import deck_repair
from src.server.domain.draft.deck_validator import (
    BASIC_LAND_NAMES, COLORS, DECK_SIZE, CardCatalog, DraftCard, DraftDeck,
    catalog_from_redis, normalize_name, slot_gate, validate_deck,
)

_UUID_RE = re.compile(
    r"^\{?[0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12}\}?$", re.IGNORECASE)
_COLOR_TO_BASIC = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
MAX_REPAIR_PASSES = 3
MAX_CANDIDATES = 400

# Legal-in-practice, widely played staples, in fill priority order. Legality and
# colour identity are NOT assumed: each one is checked against the catalog at
# fill time, so a banned/unknown/off-colour entry is simply skipped.
STAPLES = (
    "Sol Ring", "Arcane Signet", "Command Tower", "Fellwar Stone", "Mind Stone",
    "Thought Vessel", "Wayfarer's Bauble", "Commander's Sphere", "Chromatic Lantern",
    "Swiftfoot Boots", "Lightning Greaves", "Skullclamp", "Solemn Simulacrum",
    "Burnished Hart", "Exotic Orchard", "Path of Ancestry", "Reliquary Tower",
    "Myriad Landscape", "Evolving Wilds", "Terramorphic Expanse",
    "Swords to Plowshares", "Path to Exile", "Counterspell", "Negate", "Swan Song",
    "Cyclonic Rift", "Rhystic Study", "Brainstorm", "Ponder", "Preordain",
    "Lightning Bolt", "Chaos Warp", "Beast Within", "Heroic Intervention",
    "Generous Gift", "Anguished Unmaking", "Demonic Tutor", "Vampiric Tutor",
    "Doom Blade", "Go for the Throat", "Damnation", "Wrath of God", "Blasphemous Act",
    "Cultivate", "Kodama's Reach", "Rampant Growth", "Farseek", "Nature's Lore",
    "Three Visits", "Harmonize", "Night's Whisper", "Read the Bones",
    "Eternal Witness", "Tireless Tracker", "Cultivator's Caravan", "Talisman of Dominance",
)


def is_uuid_like(value: Any) -> bool:
    """True for a UUID in dashed, braced or 32-hex form. Not for blank/other text."""
    return isinstance(value, str) and bool(_UUID_RE.match(value.strip()))


class BrewInvalidDeckError(ValueError):
    """The deck could not be made valid. `codes` are RuleCode values (plus
    UUID_NAME / BLANK_NAME / CARD_COUNT from the gate itself)."""

    def __init__(self, codes: List[str], messages: List[str]):
        self.codes = list(dict.fromkeys(codes))
        self.messages = messages
        super().__init__("Brew produced an invalid deck: " + "; ".join(messages or self.codes))


def resolve_card_name(r: Any, value: str) -> Optional[str]:
    """Real name for an oracle-id-like value via `card:{id}`, else None."""
    if r is None or not value:
        return None
    try:
        raw = r.get(f"card:{value}")
        if isinstance(raw, bytes):
            raw = raw.decode()
        rec = json.loads(raw) if raw else None
    except Exception:
        return None
    name = rec.get("name") if isinstance(rec, dict) else None
    if isinstance(name, str) and name.strip() and not is_uuid_like(name):
        return name.strip()
    return None


def _identity(deck: DraftDeck, catalog: CardCatalog) -> frozenset:
    ci: frozenset = frozenset()
    for c in deck.commanders:
        info = catalog.get(c)
        if info is not None:
            ci |= info.color_identity
    return ci


def _sanitise(draft: dict, r: Any) -> dict:
    """Resolve or drop unusable names; coerce quantities. Returns a deep copy."""
    out = copy.deepcopy(draft)
    out["commanders"] = [
        resolve_card_name(r, c) or c if isinstance(c, str) and is_uuid_like(c) else c
        for c in (out.get("commanders") or [])
    ]
    cleaned: List[dict] = []
    for card in out.get("cards") or []:
        name = card.get("name")
        if not isinstance(name, str) or not name.strip():
            continue
        name = name.strip()
        if is_uuid_like(name):
            real = resolve_card_name(r, name)
            if not real:
                continue  # unresolvable id: dropped (the fill step replaces it)
            card["oracle_id"] = card.get("oracle_id") or name
            name = real
        qty = card.get("quantity", 1)
        if isinstance(qty, bool) or not isinstance(qty, int) or qty < 1:
            continue
        card["name"], card["quantity"] = name, qty
        cleaned.append(card)
    out["cards"] = cleaned
    return out


def _fill(deck: DraftDeck, catalog: CardCatalog, candidates: Iterable[str],
          staples: Iterable[str]) -> DraftDeck:
    """Top up to DECK_SIZE: candidates -> staples -> basics. Legal on-colour only."""
    cards = [DraftCard(c.name, c.quantity) for c in deck.cards]
    need = DECK_SIZE - len(deck.commanders) - sum(c.quantity for c in cards)
    if need <= 0:
        return deck_repair._clone(deck, cards)
    ci = _identity(deck, catalog)
    present = {normalize_name(c) for c in deck.commanders} | {normalize_name(c.name) for c in cards}

    def eligible(name: Any) -> bool:
        if not isinstance(name, str) or not name.strip() or is_uuid_like(name):
            return False
        info = catalog.get(name)
        return bool(
            info is not None and not info.is_basic and normalize_name(info.name) not in present
            and info.commander_legality == "legal"
            and not (info.color_identity - ci)
            and slot_gate(info, DeckSlot.MAINBOARD).legal
        )

    for name in [*list(candidates)[:MAX_CANDIDATES], *staples]:
        if need <= 0:
            break
        if eligible(name):
            real = catalog.get(name).name  # type: ignore[union-attr]
            cards.append(DraftCard(real, 1))
            present.add(normalize_name(real))
            need -= 1

    if need > 0:
        basics = [_COLOR_TO_BASIC[c] for c in COLORS if c in ci] or ["Wastes"]
        per = {b: need // len(basics) for b in basics}
        for b in basics[: need % len(basics)]:
            per[b] += 1
        for b, n in per.items():
            if not n:
                continue
            for card in cards:
                if normalize_name(card.name) == normalize_name(b):
                    card.quantity += n
                    break
            else:
                cards.append(DraftCard(b, n))
    return deck_repair._clone(deck, cards)


def _check_names(draft: dict) -> tuple:
    codes, msgs = [], []
    names = list(draft.get("commanders") or []) + [c.get("name") for c in draft.get("cards") or []]
    if any(not isinstance(n, str) or not n.strip() for n in names):
        codes.append("BLANK_NAME")
        msgs.append("a card has no name")
    bad = [n for n in names if is_uuid_like(n)]
    if bad:
        codes.append("UUID_NAME")
        msgs.append("GUID used as a card name: " + ", ".join(bad))
    return codes, msgs


def verify_draft(draft: dict, catalog: CardCatalog) -> None:
    """Raise BrewInvalidDeckError unless `draft` is a complete legal deck."""
    codes, msgs = _check_names(draft)
    lines = sum(int(c.get("quantity", 1) or 0) for c in draft.get("cards") or [])
    if lines != DECK_SIZE:
        codes.append("DECK_SIZE")
        msgs.append(f"draft lists {lines} cards, must be exactly {DECK_SIZE}")
    deck = DraftDeck.from_builder_draft(draft)
    res = validate_deck(deck, catalog)
    codes += res.codes()
    msgs += [e.message for e in res.errors]
    if codes:
        raise BrewInvalidDeckError(codes, msgs)


def finalize_draft(draft: dict, catalog: CardCatalog, *, r: Any = None,
                   candidates: Iterable[str] = (), staples: Iterable[str] = STAPLES) -> dict:
    """Sanitise, repair and fill `draft` to a legal 100-card deck, or raise
    BrewInvalidDeckError. Returns a new draft dict (input untouched)."""
    candidates, staples = list(candidates), list(staples)
    work = _sanitise(draft, r)
    originals: Dict[str, dict] = {}
    for card in work["cards"]:
        originals.setdefault(normalize_name(card["name"]), card)

    deck = DraftDeck.from_builder_draft(work)
    for _ in range(MAX_REPAIR_PASSES):
        deck, _a = deck_repair.drop_invalid_cards(deck, catalog)
        deck, _a = deck_repair.dedupe_singleton(deck, catalog)
        if len(deck.commanders) + sum(c.quantity for c in deck.cards) > DECK_SIZE:
            deck, _a = deck_repair.fit_to_size(deck, catalog)
        deck = _fill(deck, catalog, candidates, staples)
        if validate_deck(deck, catalog).valid:
            break

    # Rebuild draft cards: commanders first, then merged mainboard lines.
    cards: List[dict] = []
    for cmd in deck.commanders:
        cards.append({**originals.get(normalize_name(cmd), {}), "name": cmd, "quantity": 1})
    merged: Dict[str, dict] = {}
    for c in deck.cards:
        key = normalize_name(c.name)
        if key in merged:
            merged[key]["quantity"] += c.quantity
            continue
        base = originals.get(key)
        if base is not None:
            line = {**base, "name": c.name, "quantity": c.quantity}
        else:
            line = {
                "name": c.name, "quantity": c.quantity, "tier": 3, "tier_label": "new",
                "cost": 0.0, "rationale": "Auto-filled to complete the 100-card deck",
            }
        merged[key] = line
        cards.append(line)
    out = {k: v for k, v in work.items() if k != "cards"}
    out["cards"] = cards
    verify_draft(out, catalog)
    return out


def gate_draft(r: Any, draft: dict, candidates: Iterable[str] = ()) -> dict:
    """finalize_draft with the catalog loaded from Redis. The Brew entry point."""
    candidates = [c for c in candidates if isinstance(c, str)][:MAX_CANDIDATES]
    names = list(draft.get("commanders") or [])
    for c in draft.get("cards") or []:
        n = c.get("name")
        if isinstance(n, str) and n.strip():
            names.append(resolve_card_name(r, n.strip()) or n.strip() if is_uuid_like(n) else n.strip())
    names += candidates + list(STAPLES)
    catalog = catalog_from_redis(r, [n for n in names if not is_uuid_like(n)])
    return finalize_draft(draft, catalog, r=r, candidates=candidates)

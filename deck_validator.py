"""
Deterministic Commander deck validator for Reconstruct drafts.

Pure module: no LLM, no network, no Redis access in the validation path. The
Reconstruct builder (see reconstruct.BaseReconstructBuilder) produces up to two
draft decks; every draft must pass `validate_decks` before it is stored. Each
failure is a structured `ValidationError` (rule code, offending cards, message,
details) that can be serialised with `.to_dict()` and fed back to an LLM for
repair.

Rules (error codes in `RuleCode`):
    DECK_SIZE            exactly 100 cards including commander(s)
    COLOR_IDENTITY       every card within the combined commander identity
    SINGLETON            max 1 copy, except basics and "any number / up to N"
    BANNED / NOT_LEGAL   Commander banlist, from legality data; NOT_LEGAL is
                         also the code for slot type-gate violations (a card
                         whose type class does not belong in its slot)
    UNKNOWN_LEGALITY     card record has no synced legality; fails closed
    COMMANDER_COUNT      1 or 2 commanders
    COMMANDER_ILLEGAL    commander is not a legal commander
    PARTNER_INVALID      two commanders that are not a legal pair
    SHARED_POOL_CONFLICT combined use across both decks exceeds owned quantity
    UNKNOWN_CARD         card missing from the card catalog (likely hallucinated)
    INVALID_QUANTITY     non-positive / non-integer quantity
    DECK_COUNT           not 1 or 2 decks

Slot-legality gates (deterministic, driven by catalog type data): each named
slot (see deck_slots.DeckSlot) may only contain its own type class. Planes and
phenomenons belong in planar_deck, schemes in scheme_deck, attractions in
attraction_deck; none of those type classes are legal in the commander or
mainboard slots. Maybeboard/considering have no type gate. A slot violation is
flagged NOT_LEGAL with a human-readable REASON string (e.g. "plane not legal
in mainboard slot"); a card with no type data in a variant slot fails closed
("card type unknown; cannot verify ..."). `slot_gate()` returns the per-card
pass/fail + reason (`SlotGateResult`) for the review pipeline, and
`gate_deck_slots()` runs the gate over every occupied slot of a draft.

Inputs are simple local types (`DraftDeck`, `CardInfo`, `CardCatalog`) so this
module has no dependency on the not-yet-merged pool module. Adapters:
    DraftDeck.from_builder_draft(draft)   -- reconstruct builder draft dict
    CardInfo.from_record(record)          -- card:{oracle_id} ORM record (cards.py)
    catalog_from_redis(r, names)          -- build a catalog for a set of names

Data sources (see sync.py): `card:{oracle_id}` records carry `color`
("WUB" / "C"), `type`, `types`, `super_types`, `sub_types`, `keywords`,
`oracle_text`, and `commander_legality` ("legal"/"banned"/"not_legal").
Records written before legality syncing existed have no legality; for those the
maintained `COMMANDER_BANNED` snapshot is used as a fallback. Synced legality
always wins over the snapshot. Unknown legality (None) FAILS CLOSED: such cards
are flagged UNKNOWN_LEGALITY (unless the snapshot already flags them BANNED) and
never silently pass as legal.

Shared-pool semantics: `tier1_available` and `tier2_available` are card ->
owned count maps; a card's available quantity is the SUM of both tiers (they are
distinct pools of physical copies). Basic lands are exempt from the pool check
by default (`exempt_basics_from_pool=True`) since they are effectively
unlimited; pass False to enforce them as well. Cards in neither map are
"Tier 3" (to be purchased) and unconstrained.
"""
from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from deck_slots import TYPE_CLASS_TO_SLOT, DeckSlot, normalize_slot

DECK_SIZE = 100
MAX_DECKS = 2

COLORS = "WUBRG"
_COLOR_NAMES = {
    "white": "W", "blue": "U", "black": "B", "red": "R", "green": "G",
}


class RuleCode(str, Enum):
    DECK_SIZE = "DECK_SIZE"
    COLOR_IDENTITY = "COLOR_IDENTITY"
    SINGLETON = "SINGLETON"
    BANNED = "BANNED"
    NOT_LEGAL = "NOT_LEGAL"
    UNKNOWN_LEGALITY = "UNKNOWN_LEGALITY"
    COMMANDER_COUNT = "COMMANDER_COUNT"
    COMMANDER_ILLEGAL = "COMMANDER_ILLEGAL"
    PARTNER_INVALID = "PARTNER_INVALID"
    SHARED_POOL_CONFLICT = "SHARED_POOL_CONFLICT"
    UNKNOWN_CARD = "UNKNOWN_CARD"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    DECK_COUNT = "DECK_COUNT"


# --------------------------------------------------------------------------
# Maintained data
# --------------------------------------------------------------------------

BASIC_LAND_NAMES = (
    "Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes",
    "Snow-Covered Plains", "Snow-Covered Island", "Snow-Covered Swamp",
    "Snow-Covered Mountain", "Snow-Covered Forest", "Snow-Covered Wastes",
)
_BASIC_IDENTITY = {
    "Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G",
    "Wastes": "",
}

# Cards whose rules text lifts the singleton restriction. The oracle text is
# parsed first ("A deck can have any number of / up to N cards named ...");
# this list is the fallback for cards whose text is missing from the catalog.
# value: None = any number, int = maximum copies.
SINGLETON_EXCEPTIONS: Dict[str, Optional[int]] = {
    "Relentless Rats": None,
    "Shadowborn Apostle": None,
    "Persistent Petitioners": None,
    "Rat Colony": None,
    "Dragon's Approach": None,
    "Hare Apparent": None,
    "Templar Knight": None,
    "Slime Against Humanity": None,
    "Seven Dwarves": 7,
    "Nazgûl": 9,
}

# Best-effort snapshot of the Commander banlist, used ONLY when a card record
# carries no legality data. Synced legality (commander_legality) overrides it.
COMMANDER_BANNED = frozenset({
    "Ancestral Recall", "Balance", "Biorhythm", "Black Lotus", "Braids, Cabal Minion",
    "Chaos Orb", "Coalition Victory", "Channel", "Dockside Extortionist",
    "Emrakul, the Aeons Torn", "Erayo, Soratami Ascendant", "Falling Star",
    "Fastbond", "Flash", "Gifts Ungiven", "Golos, Tireless Pilgrim",
    "Griselbrand", "Hullbreacher", "Iona, Shield of Emeria", "Jeweled Lotus",
    "Karakas", "Leovold, Emissary of Trest", "Library of Alexandria",
    "Limited Resources", "Lutri, the Spellchaser", "Mana Crypt", "Mox Emerald",
    "Mox Jet", "Mox Pearl", "Mox Ruby", "Mox Sapphire",
    "Nadu, Winged Wisdom", "Panoptic Mirror", "Paradox Engine",
    "Primeval Titan", "Prophet of Kruphix", "Recurring Nightmare",
    "Rofellos, Llanowar Emissary", "Shahrazad", "Sundering Titan",
    "Sway of the Stars", "Sylvan Primordial", "Time Vault", "Time Walk",
    "Tinker", "Tolarian Academy", "Trade Secrets", "Upheaval", "Worldfire",
    "Yawgmoth's Bargain",
})


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    """Case/diacritic/punctuation-insensitive key ('Nazgûl' == 'nazgul')."""
    s = unicodedata.normalize("NFKD", str(name or ""))
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = s.replace("\u2019", "'").replace("\u2018", "'")
    return re.sub(r"\s+", " ", s).strip().casefold()


_NAME_KEY = normalize_name

_NUMBER_WORDS = {
    w: i for i, w in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve "
        "thirteen fourteen fifteen sixteen seventeen eighteen nineteen twenty".split()
    )
}

_SINGLETON_TEXT_RE = re.compile(
    r"a deck can have (?:(any number of)|up to (\w+)) cards? named", re.IGNORECASE
)
_SUPERTYPES = {"basic", "legendary", "snow", "world", "ongoing", "elite", "host"}


def parse_color_identity(value: Any) -> frozenset:
    """Accept 'WUB', 'C', '', ['W','U'], ['White','Blue'], None."""
    if value is None:
        return frozenset()
    if isinstance(value, str):
        items: Iterable = [value] if value.casefold() in _COLOR_NAMES else list(value)
    else:
        items = value
    out = set()
    for item in items:
        token = str(item).strip()
        if token.casefold() in _COLOR_NAMES:
            out.add(_COLOR_NAMES[token.casefold()])
        elif token.upper() in tuple(COLORS):
            out.add(token.upper())
    return frozenset(out)


def _strip_reminder(text: str) -> str:
    return re.sub(r"\([^)]*\)", "", text or "")


def singleton_limit_from_text(oracle_text: str) -> Tuple[bool, Optional[int]]:
    """(found, limit). limit None = any number. Parses the deck-building clause."""
    m = _SINGLETON_TEXT_RE.search(oracle_text or "")
    if not m:
        return False, None
    if m.group(1):
        return True, None
    word = m.group(2).casefold()
    if word.isdigit():
        return True, int(word)
    if word in _NUMBER_WORDS:
        return True, _NUMBER_WORDS[word]
    return False, None


# --------------------------------------------------------------------------
# Local types
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class CardInfo:
    name: str
    color_identity: frozenset = frozenset()
    types: Tuple[str, ...] = ()
    supertypes: Tuple[str, ...] = ()
    subtypes: Tuple[str, ...] = ()
    keywords: Tuple[str, ...] = ()
    oracle_text: str = ""
    commander_legality: Optional[str] = None  # "legal" | "banned" | "not_legal" | None

    @classmethod
    def from_record(cls, rec: Mapping[str, Any]) -> "CardInfo":
        """Adapt a `card:{oracle_id}` ORM record (cards.py / sync.py)."""
        types = list(rec.get("types") or [])
        supertypes = list(rec.get("super_types") or rec.get("supertypes") or [])
        subtypes = list(rec.get("sub_types") or rec.get("subtypes") or [])
        if rec.get("type_line"):
            left, _, right = str(rec["type_line"]).partition("\u2014")
            for word in left.split():
                (supertypes if word.casefold() in _SUPERTYPES else types).append(word)
            subtypes += right.split()
        elif not types and rec.get("type"):
            # Legacy records hold a single display type, e.g. "Creature".
            for word in str(rec["type"]).split():
                (supertypes if word.casefold() in _SUPERTYPES else types).append(word)
        legality = rec.get("commander_legality")
        if legality is None and isinstance(rec.get("legalities"), Mapping):
            legality = rec["legalities"].get("commander")
        color = rec.get("color_identity", rec.get("color"))
        return cls(
            name=rec["name"],
            color_identity=parse_color_identity(color),
            types=tuple(types),
            supertypes=tuple(dict.fromkeys(supertypes)),
            subtypes=tuple(subtypes),
            keywords=tuple(rec.get("keywords") or ()),
            oracle_text=rec.get("oracle_text") or rec.get("text") or "",
            commander_legality=(str(legality).casefold() if legality else None),
        )

    def _has(self, seq: Sequence[str], word: str) -> bool:
        return word.casefold() in {s.casefold() for s in seq}

    @property
    def is_basic(self) -> bool:
        return self._has(self.supertypes, "Basic") or self.name in BASIC_LAND_NAMES

    @property
    def is_legendary(self) -> bool:
        return self._has(self.supertypes, "Legendary")

    @property
    def is_creature(self) -> bool:
        return self._has(self.types, "Creature")

    @property
    def can_be_commander(self) -> bool:
        if "can be your commander" in (self.oracle_text or "").casefold():
            return True
        return self.is_legendary and self.is_creature


def _builtin_basic(name: str) -> Optional[CardInfo]:
    base = name[len("Snow-Covered "):] if name.startswith("Snow-Covered ") else name
    if base not in _BASIC_IDENTITY or name not in BASIC_LAND_NAMES:
        return None
    snow = name.startswith("Snow-Covered ")
    return CardInfo(
        name=name,
        color_identity=parse_color_identity(_BASIC_IDENTITY[base]),
        types=("Land",),
        supertypes=("Basic", "Snow") if snow else ("Basic",),
        subtypes=(base,) if base != "Wastes" else (),
        commander_legality="legal",
    )


class CardCatalog:
    """Name -> CardInfo lookup (diacritic/case-insensitive; DFC front faces
    resolve too). Basic lands are always resolvable."""

    def __init__(self, cards: Iterable[CardInfo] = ()):
        self._by_key: Dict[str, CardInfo] = {}
        for c in cards:
            self.add(c)

    @classmethod
    def from_records(cls, records: Iterable[Mapping[str, Any]]) -> "CardCatalog":
        return cls(CardInfo.from_record(r) for r in records)

    def add(self, card: CardInfo) -> None:
        self._by_key[normalize_name(card.name)] = card
        if "//" in card.name:
            self._by_key.setdefault(normalize_name(card.name.split("//")[0]), card)

    def get(self, name: str) -> Optional[CardInfo]:
        key = normalize_name(name)
        hit = self._by_key.get(key)
        if hit is not None:
            return hit
        for basic in BASIC_LAND_NAMES:
            if normalize_name(basic) == key:
                return _builtin_basic(basic)
        return None

    def __contains__(self, name: str) -> bool:
        return self.get(name) is not None


@dataclass
class DraftCard:
    name: str
    quantity: int = 1


@dataclass
class DraftDeck:
    """One draft: commander(s) plus mainboard (commanders NOT included in
    cards). `slots` holds the named non-main slots (deck_slots.DeckSlot):
    planar_deck, scheme_deck, attraction_deck, maybeboard, considering.
    Variant-slot cards are NOT part of the 100 and are exempt from deck size,
    color identity, singleton and pool checks; they are governed by the
    per-slot type gates (see slot_gate)."""
    commanders: List[str]
    cards: List[DraftCard]
    name: str = ""
    slots: Dict[str, List[DraftCard]] = field(default_factory=dict)

    @classmethod
    def from_builder_draft(cls, draft: Mapping[str, Any]) -> "DraftDeck":
        """Adapt a BaseReconstructBuilder draft dict. The builder format lists
        commanders inside `cards` too; one copy of each is stripped so the
        validator can count commanders separately."""
        commanders = list(draft.get("commanders") or [])
        remaining = {normalize_name(c): 1 for c in commanders}
        cards: List[DraftCard] = []
        for c in draft.get("cards") or []:
            name = c.get("name")
            qty = c.get("quantity", 1)
            key = normalize_name(name)
            if remaining.get(key) and isinstance(qty, int) and qty >= 1:
                remaining[key] = 0
                qty -= 1
                if qty == 0:
                    continue
            cards.append(DraftCard(name=name, quantity=qty))
        return cls(commanders=commanders, cards=cards,
                   name=draft.get("name") or draft.get("id") or "")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "DraftDeck":
        """{'commanders': [...], 'cards': [{'name','quantity'}] | {name: qty},
        'slots': {slot: [{'name','quantity'}, ...]}}

        Slot keys are normalized through deck_slots (legacy/unknown keys fold
        into maybeboard, matching intake). 'commander'/'mainboard' slot
        entries are used only when the explicit 'commanders'/'cards' fields
        are absent; the explicit fields always win.
        """
        raw = data.get("cards") or []
        if isinstance(raw, Mapping):
            cards = [DraftCard(n, q) for n, q in raw.items()]
        else:
            cards = [DraftCard(c["name"], c.get("quantity", 1)) for c in raw]
        commanders = list(data.get("commanders") or [])

        slots: Dict[str, List[DraftCard]] = {}
        main_from_slot: List[DraftCard] = []
        cmd_from_slot: List[str] = []
        for key, entries in (data.get("slots") or {}).items():
            slot = key if key in DeckSlot.VALID else normalize_slot(key)
            if slot is None:
                slot = DeckSlot.MAYBEBOARD
            for e in entries or []:
                if isinstance(e, Mapping):
                    if not e.get("name"):
                        continue
                    entry = DraftCard(e["name"], e.get("quantity", 1))
                else:
                    entry = DraftCard(str(e), 1)
                if slot == DeckSlot.COMMANDER:
                    cmd_from_slot.append(entry.name)
                elif slot == DeckSlot.MAINBOARD:
                    main_from_slot.append(entry)
                else:
                    slots.setdefault(slot, []).append(entry)
        if not commanders:
            commanders = cmd_from_slot
        if not cards:
            cards = main_from_slot
        return cls(commanders=commanders, cards=cards,
                   name=data.get("name", ""), slots=slots)


@dataclass
class ValidationError:
    code: str
    cards: List[str]
    message: str
    deck_index: Optional[int] = None  # None for cross-deck errors
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": str(getattr(self.code, "value", self.code)),
            "cards": list(self.cards),
            "message": self.message,
            "deck_index": self.deck_index,
            "details": self.details,
        }


@dataclass
class ValidationResult:
    errors: List[ValidationError] = field(default_factory=list)

    @property
    def valid(self) -> bool:
        return not self.errors

    def errors_for(self, deck_index: Optional[int]) -> List[ValidationError]:
        return [e for e in self.errors if e.deck_index == deck_index]

    def codes(self) -> List[str]:
        return [str(getattr(e.code, "value", e.code)) for e in self.errors]

    def to_dict(self) -> Dict[str, Any]:
        return {"valid": self.valid, "errors": [e.to_dict() for e in self.errors]}

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


# --------------------------------------------------------------------------
# Partner / commander logic
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class PartnerProfile:
    partner: bool = False
    partner_with: Optional[str] = None
    partner_group: Optional[str] = None  # "Partner—Survivors" etc.
    friends_forever: bool = False
    choose_background: bool = False
    is_background: bool = False
    doctors_companion: bool = False
    is_doctor: bool = False


def partner_profile(card: CardInfo) -> PartnerProfile:
    partner = False
    partner_with = partner_group = None
    friends = choose_bg = companion = False
    for raw in _strip_reminder(card.oracle_text).splitlines():
        line = raw.strip().rstrip(".").strip()
        low = line.casefold()
        m = re.match(r"^partner with (.+)$", line, re.IGNORECASE)
        if m:
            partner_with = m.group(1).strip()
            continue
        m = re.match(r"^partner\s*[\u2014\u2013-]\s*(.+)$", line, re.IGNORECASE)
        if m:
            partner_group = normalize_name(m.group(1))
            continue
        if low == "partner":
            partner = True
        elif low == "friends forever":
            friends = True
        elif low == "choose a background":
            choose_bg = True
        elif low == "doctor's companion":
            companion = True
    kws = {k.casefold() for k in card.keywords}
    if not (partner or partner_with or partner_group) and "partner" in kws:
        partner = True
    friends = friends or "friends forever" in kws
    choose_bg = choose_bg or "choose a background" in kws
    companion = companion or "doctor's companion" in kws
    return PartnerProfile(
        partner=partner, partner_with=partner_with, partner_group=partner_group,
        friends_forever=friends, choose_background=choose_bg,
        is_background=card._has(card.subtypes, "Background"),
        doctors_companion=companion, is_doctor=card._has(card.subtypes, "Doctor"),
    )


def check_partner_pair(a: CardInfo, b: CardInfo) -> Optional[str]:
    """None if (a, b) is a legal commander pair, else a human-readable reason."""
    pa, pb = partner_profile(a), partner_profile(b)
    if pa.partner and pb.partner:
        return None
    if pa.partner_with or pb.partner_with:
        if (pa.partner_with and pb.partner_with
                and normalize_name(pa.partner_with) == normalize_name(b.name)
                and normalize_name(pb.partner_with) == normalize_name(a.name)):
            return None
        return ("'Partner with <name>' only pairs with the exact named card, "
                "and that card must name it back")
    if pa.partner_group and pa.partner_group == pb.partner_group:
        return None
    if pa.friends_forever and pb.friends_forever:
        return None
    if (pa.choose_background and pb.is_background) or (pb.choose_background and pa.is_background):
        return None
    if (pa.doctors_companion and pb.is_doctor) or (pb.doctors_companion and pa.is_doctor):
        return None
    return ("commanders share no pairing ability (need both Partner, a matching "
            "'Partner with', both Friends forever, Choose a Background + Background, "
            "or Doctor's companion + Doctor)")


def is_legal_commander(card: CardInfo) -> bool:
    return card.can_be_commander


# --------------------------------------------------------------------------
# Slot-legality gates
# --------------------------------------------------------------------------

#: Type classes that belong to a variant slot (from deck_slots).
VARIANT_TYPE_CLASSES = frozenset(TYPE_CLASS_TO_SLOT)

#: Variant slot -> the type classes allowed in it.
SLOT_ALLOWED_TYPE_CLASSES: Dict[str, frozenset] = {
    DeckSlot.PLANAR_DECK: frozenset({"plane", "phenomenon"}),
    DeckSlot.SCHEME_DECK: frozenset({"scheme"}),
    DeckSlot.ATTRACTION_DECK: frozenset({"attraction"}),
}

#: Human-readable allowed-content labels for gate reasons.
SLOT_ALLOWED_LABELS = {
    DeckSlot.PLANAR_DECK: "planes and phenomenons",
    DeckSlot.SCHEME_DECK: "schemes",
    DeckSlot.ATTRACTION_DECK: "attractions",
}

#: Slots with no type gate (anything goes).
UNGATED_SLOTS = frozenset({DeckSlot.MAYBEBOARD, DeckSlot.CONSIDERING})


@dataclass(frozen=True)
class SlotGateResult:
    """Per-card, per-slot gate verdict. `reason` is ALWAYS populated (pass or
    fail) so the review pipeline can attach it to suggestions."""
    card: str
    slot: str
    legal: bool
    reason: str
    type_class: Optional[str] = None  # variant class found, else None

    def to_dict(self) -> Dict[str, Any]:
        return {"card": self.card, "slot": self.slot, "legal": self.legal,
                "reason": self.reason, "type_class": self.type_class}


def _variant_type_class(info: CardInfo) -> Optional[str]:
    # Scan the WHOLE type line: Plane/Phenomenon/Scheme are card types, but
    # Attraction lives in the subtype position ("Artifact — Attraction"), so a
    # types-only check would let a real Attraction slip through the gate.
    lowered = {t.casefold() for t in info.types + info.subtypes}
    for type_class in sorted(VARIANT_TYPE_CLASSES):
        if type_class in lowered:
            return type_class
    return None


def slot_gate(info: Optional[CardInfo], slot: str,
              card_name: str = "") -> SlotGateResult:
    """Deterministic type-class gate for one card in one slot.

    - commander / mainboard: variant type classes (Plane, Phenomenon, Scheme,
      Attraction) are not legal; everything else passes (commander legality
      itself is covered by the commander rules, not this gate).
    - planar_deck / scheme_deck / attraction_deck: only the slot's own type
      class passes. A catalog card with NO type data fails closed (its slot
      membership cannot be verified).
    - maybeboard / considering: no gate, always passes.
    - unknown card (info None): fails outside the ungated slots (UNKNOWN_CARD
      is also reported by the validator proper).
    """
    name = card_name or (info.name if info is not None else "")
    slot = normalize_slot(slot) or str(slot or "").strip().lower()
    if slot in UNGATED_SLOTS:
        return SlotGateResult(name, slot, True,
                              f"no type gate for the {slot} slot; anything goes")
    if info is None:
        return SlotGateResult(
            name, slot, False,
            f"card not in the catalog; cannot verify {slot or 'unknown'} slot "
            "legality")
    variant = _variant_type_class(info)
    if slot in SLOT_ALLOWED_TYPE_CLASSES:
        if variant is not None and variant in SLOT_ALLOWED_TYPE_CLASSES[slot]:
            return SlotGateResult(name, slot, True,
                                  f"{variant} is legal in the {slot} slot", variant)
        if not info.types:
            return SlotGateResult(
                name, slot, False,
                f"card type unknown; cannot verify {slot} slot legality "
                "(re-sync the card record)")
        found = variant or info.types[0].casefold()
        return SlotGateResult(
            name, slot, False,
            f"{found} not legal in {slot} slot "
            f"({SLOT_ALLOWED_LABELS[slot]} only)", variant)
    if slot in (DeckSlot.COMMANDER, DeckSlot.MAINBOARD):
        if variant is not None:
            return SlotGateResult(
                name, slot, False,
                f"{variant} not legal in {slot} slot "
                f"(belongs in {TYPE_CLASS_TO_SLOT[variant]})", variant)
        return SlotGateResult(name, slot, True,
                              f"no variant type class; legal in the {slot} slot")
    return SlotGateResult(name, slot, True,
                          f"no type gate defined for slot '{slot}'")


def gate_deck_slots(deck: DraftDeck, catalog: CardCatalog) -> List[SlotGateResult]:
    """Run the type gate over every occupied slot of a draft (commander,
    mainboard, then the named slots in sorted order). Returns one
    SlotGateResult per card entry, passes included."""
    results: List[SlotGateResult] = []
    for name in deck.commanders:
        results.append(slot_gate(catalog.get(name), DeckSlot.COMMANDER, name))
    for c in deck.cards:
        results.append(slot_gate(catalog.get(c.name), DeckSlot.MAINBOARD, c.name))
    for slot in sorted(deck.slots):
        for c in deck.slots[slot]:
            results.append(slot_gate(catalog.get(c.name), slot, c.name))
    return results


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------

def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _tally(deck: DraftDeck, catalog: CardCatalog):
    """-> (counts: key -> [display, qty], invalid_quantity_names)."""
    counts: Dict[str, List[Any]] = {}
    bad: List[str] = []

    def bump(name: str, qty: int):
        key = normalize_name(name)
        info = catalog.get(name)
        entry = counts.setdefault(key, [info.name if info else name, 0])
        entry[1] += qty

    for c in deck.commanders:
        bump(c, 1)
    for card in deck.cards:
        if not _is_int(card.quantity) or card.quantity < 1:
            bad.append(card.name)
            continue
        bump(card.name, card.quantity)
    return counts, bad


def _allowed_copies(name: str, info: Optional[CardInfo]) -> Tuple[bool, Optional[int]]:
    """(exempt_from_singleton, limit). limit None = unlimited, else max copies."""
    if info is not None:
        if info.is_basic:
            return True, None
        found, limit = singleton_limit_from_text(info.oracle_text)
        if found:
            return True, limit
    else:
        for basic in BASIC_LAND_NAMES:
            if normalize_name(basic) == normalize_name(name):
                return True, None
    for exc_name, limit in SINGLETON_EXCEPTIONS.items():
        if normalize_name(exc_name) == normalize_name(name):
            return True, limit
    return False, 1


def _check_deck(deck: DraftDeck, idx: int, catalog: CardCatalog,
                banned: frozenset) -> List[ValidationError]:
    errs: List[ValidationError] = []
    counts, bad_qty = _tally(deck, catalog)
    banned_keys = {normalize_name(n) for n in banned}

    if bad_qty:
        errs.append(ValidationError(
            RuleCode.INVALID_QUANTITY, sorted(set(bad_qty)),
            "Quantities must be positive integers; these entries were ignored: "
            + ", ".join(sorted(set(bad_qty))) + ".", idx))

    # Unknown cards
    unknown = [v[0] for k, v in counts.items() if catalog.get(v[0]) is None]
    if unknown:
        errs.append(ValidationError(
            RuleCode.UNKNOWN_CARD, unknown,
            "These cards are not in the card database (misspelled or invented?): "
            + ", ".join(unknown) + ". Replace them with real card names.", idx))

    # 1. Deck size
    total = sum(v[1] for v in counts.values())
    if total != DECK_SIZE:
        delta = total - DECK_SIZE
        errs.append(ValidationError(
            RuleCode.DECK_SIZE, [],
            f"Deck has {total} cards including commander(s); it must have exactly "
            f"{DECK_SIZE}. " + (f"Remove {delta}." if delta > 0 else f"Add {-delta}."),
            idx, {"expected": DECK_SIZE, "actual": total, "delta": delta}))

    # 5. Commander count / legality / partner
    cmds = [(c, catalog.get(c)) for c in deck.commanders]
    if len(cmds) not in (1, 2):
        errs.append(ValidationError(
            RuleCode.COMMANDER_COUNT, list(deck.commanders),
            f"A deck needs 1 or 2 commanders; found {len(cmds)}.", idx,
            {"expected": "1 or 2", "actual": len(cmds)}))
    else:
        known = [(n, i) for n, i in cmds if i is not None]
        if len(cmds) == 2:
            if normalize_name(cmds[0][0]) == normalize_name(cmds[1][0]):
                errs.append(ValidationError(
                    RuleCode.PARTNER_INVALID, [cmds[0][0], cmds[1][0]],
                    "The same card cannot be both commanders.", idx))
            elif len(known) == 2:
                reason = check_partner_pair(known[0][1], known[1][1])
                if reason:
                    errs.append(ValidationError(
                        RuleCode.PARTNER_INVALID, [known[0][1].name, known[1][1].name],
                        f"{known[0][1].name} and {known[1][1].name} are not a legal "
                        f"commander pair: {reason}.", idx))
        for name, info in known:
            if len(cmds) == 2 and partner_profile(info).is_background:
                continue  # legality of the pairing is covered by PARTNER_INVALID
            if not info.can_be_commander:
                errs.append(ValidationError(
                    RuleCode.COMMANDER_ILLEGAL, [info.name],
                    f"{info.name} cannot be a commander: it must be a legendary "
                    "creature or say 'can be your commander'.", idx))

    # 2. Color identity
    ci: frozenset = frozenset()
    for _, info in cmds:
        if info is not None:
            ci |= info.color_identity
    allowed = "".join(c for c in COLORS if c in ci) or "colorless"
    off = []
    off_detail = {}
    for key, (display, _q) in counts.items():
        info = catalog.get(display)
        if info is None:
            continue
        extra = info.color_identity - ci
        if extra:
            off.append(display)
            off_detail[display] = "".join(c for c in COLORS if c in extra)
    if off:
        errs.append(ValidationError(
            RuleCode.COLOR_IDENTITY, off,
            f"Cards outside the commander color identity ({allowed}): "
            + ", ".join(f"{n} (has {off_detail[n]})" for n in off)
            + ". Replace them with cards inside the identity.", idx,
            {"allowed_identity": allowed, "off_colors": off_detail}))

    # 3. Singleton
    dupes = []
    dupe_detail = {}
    for key, (display, qty) in counts.items():
        exempt, limit = _allowed_copies(display, catalog.get(display))
        if limit is not None and qty > limit:
            dupes.append(display)
            dupe_detail[display] = {"count": qty, "max": limit}
    if dupes:
        errs.append(ValidationError(
            RuleCode.SINGLETON, dupes,
            "Too many copies (singleton format): "
            + ", ".join(f"{n} x{dupe_detail[n]['count']} (max {dupe_detail[n]['max']})"
                        for n in dupes) + ".", idx, {"copies": dupe_detail}))

    # 4. Banlist / legality. Unknown legality fails closed: a card whose record
    # carries no synced legality is flagged UNKNOWN_LEGALITY, never silently
    # legal (the COMMANDER_BANNED snapshot still applies as the fallback).
    banned_found, not_legal, unknown_legality = [], [], []
    for key, (display, _q) in counts.items():
        info = catalog.get(display)
        legality = info.commander_legality if info else None
        if legality == "banned":
            banned_found.append(display)
        elif legality == "not_legal":
            not_legal.append(display)
        elif legality is None:
            if key in banned_keys:
                banned_found.append(display)
            elif info is not None:
                unknown_legality.append(display)
    if banned_found:
        errs.append(ValidationError(
            RuleCode.BANNED, banned_found,
            "Banned in Commander: " + ", ".join(banned_found) + ". Remove them.", idx))
    if not_legal:
        errs.append(ValidationError(
            RuleCode.NOT_LEGAL, not_legal,
            "Not legal in Commander: " + ", ".join(not_legal) + ". Remove them.", idx))
    if unknown_legality:
        errs.append(ValidationError(
            RuleCode.UNKNOWN_LEGALITY, unknown_legality,
            "Commander legality unknown (card record has no synced legality "
            "data; re-sync to confirm): " + ", ".join(unknown_legality)
            + ". Unknown legality is not legal; replace or re-sync these cards.", idx))

    # 6. Slot type gates. Commander/mainboard may not hold variant type
    # classes (Plane/Phenomenon/Scheme/Attraction); each variant slot may only
    # hold its own type class (a card with no type data fails closed there).
    # Maybeboard/considering are ungated. Every violation is NOT_LEGAL and
    # carries a human-readable reason for the review pipeline.
    slot_violations: List[SlotGateResult] = []
    for name in deck.commanders:
        info = catalog.get(name)
        if info is None:
            continue  # UNKNOWN_CARD already reported
        gate = slot_gate(info, DeckSlot.COMMANDER)
        if not gate.legal:
            slot_violations.append(gate)
    for c in deck.cards:
        if not _is_int(c.quantity) or c.quantity < 1:
            continue
        info = catalog.get(c.name)
        if info is None:
            continue  # UNKNOWN_CARD already reported
        gate = slot_gate(info, DeckSlot.MAINBOARD)
        if not gate.legal:
            slot_violations.append(gate)

    slot_bad_qty: List[str] = []
    slot_unknown: List[str] = []
    for slot in sorted(deck.slots):
        for c in deck.slots[slot]:
            if not _is_int(c.quantity) or c.quantity < 1:
                slot_bad_qty.append(c.name)
                continue
            if slot in UNGATED_SLOTS or slot not in SLOT_ALLOWED_TYPE_CLASSES:
                continue  # anything goes / no gate defined
            info = catalog.get(c.name)
            if info is None:
                slot_unknown.append(c.name)
                continue
            gate = slot_gate(info, slot)
            if not gate.legal:
                slot_violations.append(gate)

    if slot_bad_qty:
        errs.append(ValidationError(
            RuleCode.INVALID_QUANTITY, sorted(set(slot_bad_qty)),
            "Quantities must be positive integers; these named-slot entries "
            "were ignored: " + ", ".join(sorted(set(slot_bad_qty))) + ".", idx))
    if slot_unknown:
        errs.append(ValidationError(
            RuleCode.UNKNOWN_CARD, slot_unknown,
            "These slot cards are not in the card database (misspelled or "
            "invented?): " + ", ".join(slot_unknown)
            + ". Replace them with real card names.", idx))
    if slot_violations:
        errs.append(ValidationError(
            RuleCode.NOT_LEGAL, [g.card for g in slot_violations],
            "Cards in the wrong slot: "
            + "; ".join(f"{g.card}: {g.reason}" for g in slot_violations) + ".",
            idx, {"slot_reasons": {g.card: {"slot": g.slot, "reason": g.reason}
                                   for g in slot_violations}}))
    return errs


def _check_pool(decks: Sequence[DraftDeck], catalog: CardCatalog,
                tier1: Mapping[str, int], tier2: Mapping[str, int],
                exempt_basics: bool) -> List[ValidationError]:
    avail: Dict[str, int] = {}
    labels: Dict[str, str] = {}
    for tier in (tier1, tier2):
        for name, qty in (tier or {}).items():
            key = normalize_name(name)
            avail[key] = avail.get(key, 0) + int(qty)
            labels.setdefault(key, name)
    used: Dict[str, Dict[int, int]] = {}
    for idx, deck in enumerate(decks):
        counts, _ = _tally(deck, catalog)
        for key, (display, qty) in counts.items():
            if key in avail:
                info = catalog.get(display)
                if exempt_basics and info is not None and info.is_basic:
                    continue
                used.setdefault(key, {})[idx] = qty
                labels[key] = display
    errs = []
    for key in sorted(used):
        total = sum(used[key].values())
        if total > avail[key]:
            name = labels[key]
            per = {f"deck_{i}": q for i, q in sorted(used[key].items())}
            errs.append(ValidationError(
                RuleCode.SHARED_POOL_CONFLICT, [name],
                f"{name}: decks use {total} copies combined ("
                + ", ".join(f"deck {i} x{q}" for i, q in sorted(used[key].items()))
                + f") but only {avail[key]} available. Remove {total - avail[key]} "
                "or replace with another card.", None,
                {"requested": total, "available": avail[key], "per_deck": per}))
    return errs


def validate_decks(
    decks: Sequence[DraftDeck],
    catalog: CardCatalog,
    tier1_available: Optional[Mapping[str, int]] = None,
    tier2_available: Optional[Mapping[str, int]] = None,
    *,
    exempt_basics_from_pool: bool = True,
    banned_fallback: Iterable[str] = COMMANDER_BANNED,
) -> ValidationResult:
    """Validate one or two drafts. Pure and deterministic; errors are ordered by
    deck index, then rule order, then shared-pool conflicts."""
    decks = list(decks)
    if not 1 <= len(decks) <= MAX_DECKS:
        return ValidationResult([ValidationError(
            RuleCode.DECK_COUNT, [],
            f"Expected 1 or {MAX_DECKS} decks; got {len(decks)}.", None,
            {"actual": len(decks)})])
    banned = frozenset(banned_fallback)
    errors: List[ValidationError] = []
    for idx, deck in enumerate(decks):
        errors.extend(_check_deck(deck, idx, catalog, banned))
    errors.extend(_check_pool(decks, catalog, tier1_available or {},
                              tier2_available or {}, exempt_basics_from_pool))
    return ValidationResult(errors)


def validate_deck(deck: DraftDeck, catalog: CardCatalog,
                  tier1_available: Optional[Mapping[str, int]] = None,
                  tier2_available: Optional[Mapping[str, int]] = None,
                  **kwargs) -> ValidationResult:
    """Convenience wrapper for a single draft."""
    return validate_decks([deck], catalog, tier1_available, tier2_available, **kwargs)


def format_errors_for_prompt(result: ValidationResult) -> str:
    """Compact text block for an LLM repair prompt."""
    if result.valid:
        return "VALID"
    lines = []
    for e in result.errors:
        where = "all decks" if e.deck_index is None else f"deck {e.deck_index}"
        lines.append(f"- [{getattr(e.code, 'value', e.code)}] ({where}) {e.message}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# Catalog loading (the only I/O helper; not used by validation itself)
# --------------------------------------------------------------------------

def catalog_from_redis(r: Any, names: Iterable[str]) -> CardCatalog:
    """Build a catalog for `names` from `idx:card_name_to_oracle:{name}` ->
    `card:{oracle_id}` (cards.py), falling back to legacy `card_meta:{name}`.
    Names that resolve nowhere are simply absent (reported as UNKNOWN_CARD)."""
    def _load(key: str):
        raw = r.get(key)
        if raw is None:
            return None
        if isinstance(raw, bytes):
            raw = raw.decode()
        return raw

    cat = CardCatalog()
    for name in dict.fromkeys(names):
        rec = None
        oid = _load(f"idx:card_name_to_oracle:{name}")
        if oid:
            raw = _load(f"card:{oid}")
            rec = json.loads(raw) if raw else None
        if rec is None:
            raw = _load(f"card_meta:{name}")
            if raw:
                rec = dict(json.loads(raw))
                rec.setdefault("name", name)
        if rec is not None:
            cat.add(CardInfo.from_record(rec))

    # Fallback: diacritic/case/DFC-front-face variants (single pass over indexes).
    missing = [n for n in dict.fromkeys(names) if n not in cat]
    scan = getattr(r, "scan_iter", None)
    if missing and scan is not None:
        want = {normalize_name(n) for n in missing}
        found: Dict[str, Tuple[str, str]] = {}  # normalized -> (kind, key)

        def _note(kind: str, key: Any) -> None:
            if isinstance(key, bytes):
                key = key.decode()
            prefix = "idx:card_name_to_oracle:" if kind == "idx" else "card_meta:"
            nm = key[len(prefix):]
            for cand in (normalize_name(nm), normalize_name(nm.split("//")[0])):
                if cand in want and cand not in found:
                    found[cand] = (kind, key)

        for k in scan(match="idx:card_name_to_oracle:*"):
            _note("idx", k)
        for k in scan(match="card_meta:*"):
            _note("meta", k)
        for kind, key in found.values():
            rec = None
            if kind == "idx":
                oid = _load(key)
                raw = _load(f"card:{oid}") if oid else None
                rec = json.loads(raw) if raw else None
            else:
                raw = _load(key)
                if raw:
                    rec = dict(json.loads(raw))
                    rec.setdefault("name", key[len("card_meta:"):])
            if rec is not None:
                cat.add(CardInfo.from_record(rec))
    return cat


def draft_card_names(decks: Iterable[DraftDeck]) -> List[str]:
    """All distinct names referenced by the drafts (for catalog_from_redis)."""
    names: List[str] = []
    for d in decks:
        names.extend(d.commanders)
        names.extend(c.name for c in d.cards)
    return list(dict.fromkeys(names))

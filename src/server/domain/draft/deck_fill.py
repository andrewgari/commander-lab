"""
Guaranteed fill step: top a Commander deck up to exactly commander + 99.

`fill_deck(commander, cards, sources, catalog)` is pure and deterministic
(apart from calling the optional source callables). Fallback order:

  1. remaining source candidates (synergy / EDHREC / analytics, in the order given)
  2. curated on-colour STAPLES
  3. basic lands matching the commander's colour identity, split evenly across
     its colours (remainder to the earliest of WUBRG); Wastes if colourless.

Every candidate must pass the legality checklist: known to the catalog, synced
Commander legality == "legal" (unknown fails closed; the banned snapshot also
applies), inside the commander colour identity, not a variant-slot type, and
singleton (basics and "any number of cards named" cards excepted).

Sources may be iterables of names or zero-arg callables returning one. A
callable that raises or exceeds `source_timeout` seconds is skipped, so EDHREC
or analytics being down never prevents a full deck. Reusable by the validator
for repair: pass the current mainboard names as `cards`.
"""
from __future__ import annotations

import logging
import threading
from typing import Any, Callable, Iterable, List, Optional, Sequence, Tuple, Union

from src.server.domain.decks.deck_slots import DeckSlot
from src.server.domain.draft.deck_validator import (
    COLORS, DECK_SIZE, CardCatalog, _allowed_copies, normalize_name,
    slot_gate,
)

log = logging.getLogger(__name__)

DEFAULT_SOURCE_TIMEOUT = 5.0
_COLOR_TO_BASIC = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}

# Curated, maintained staples in priority order. Legality / colour identity are
# never assumed: each is checked against the catalog at fill time, so banned,
# unknown or off-colour entries are simply skipped.
STAPLES: Tuple[str, ...] = (
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

Source = Tuple[str, Union[Iterable[str], Callable[[], Iterable[str]]]]


_MAX_SOURCE_THREADS = 4
_source_slots = threading.BoundedSemaphore(_MAX_SOURCE_THREADS)


def _load_source(label: str, src: Any, timeout: float) -> List[str]:
    """Names from one source; [] (and a log line) on any error or timeout.

    Callables run on a daemon thread, with the iterable fully materialised
    inside it so the deadline covers fetching too. Worker threads are capped:
    a hung source holds a slot, and when none is free the source is skipped
    rather than spawning more threads.
    """
    def clean(items: Any) -> List[str]:
        return [n for n in list(items or []) if isinstance(n, str)]

    try:
        if not callable(src):
            return clean(src)
        if not _source_slots.acquire(blocking=False):
            log.warning("deck_fill: source %r skipped (worker threads exhausted)", label)
            return []
        box: dict = {}

        def run() -> None:
            try:
                box["v"] = clean(src())
            except Exception as exc:  # noqa: BLE001
                box["e"] = exc
            finally:
                _source_slots.release()

        t = threading.Thread(target=run, daemon=True, name=f"deck-fill-{label}")
        t.start()
        t.join(timeout)
        if "e" in box:
            raise box["e"]
        if "v" not in box:
            log.warning("deck_fill: source %r timed out after %ss", label, timeout)
            return []
        return box["v"]
    except Exception as exc:  # noqa: BLE001 - a dead source must never break the fill
        log.warning("deck_fill: source %r unavailable (%s: %s)", label, type(exc).__name__, exc)
        return []


def _basic_split(identity: frozenset, n: int) -> List[str]:
    colors = [c for c in COLORS if c in identity]
    if not colors:
        return ["Wastes"] * n
    base, extra = divmod(n, len(colors))
    out: List[str] = []
    for i, c in enumerate(colors):
        out += [_COLOR_TO_BASIC[c]] * (base + (1 if i < extra else 0))
    return out


def fill_deck(commander: str, cards: Optional[Iterable[str]],
              sources: Optional[Sequence[Source]], catalog: CardCatalog, *,
              staples: Iterable[str] = STAPLES,
              source_timeout: float = DEFAULT_SOURCE_TIMEOUT) -> List[str]:
    """Return exactly DECK_SIZE card names: [commander, *99 legal cards].

    `cards` are the existing mainboard names (commander optional, illegal ones
    dropped, order kept, oversize trimmed from the end). Raises ValueError only
    if the commander is not in the catalog (colour identity unknowable).
    """
    cmd_info = catalog.get(commander)
    if cmd_info is None:
        raise ValueError(f"unknown commander {commander!r}; cannot determine colour identity")
    if cmd_info.commander_legality != "legal":
        raise ValueError(f"commander {commander!r} is not Commander-legal "
                         f"(legality: {cmd_info.commander_legality})")
    if not cmd_info.can_be_commander:
        raise ValueError(f"{commander!r} is not eligible to be a commander")
    if not slot_gate(cmd_info, DeckSlot.COMMANDER).legal:
        raise ValueError(f"{commander!r} is not legal in the commander slot")
    identity = cmd_info.color_identity
    cmd_key = normalize_name(cmd_info.name)

    deck: List[str] = []
    counts: dict = {}

    def legal(name: str) -> bool:
        info = catalog.get(name)
        if info is None or normalize_name(info.name) == cmd_key:
            return False
        if info.commander_legality != "legal":  # banned / not_legal / unknown (fail closed)
            return False
        if info.color_identity - identity:
            return False
        if not slot_gate(info, DeckSlot.MAINBOARD).legal:
            return False
        _, limit = _allowed_copies(info.name, info)
        return limit is None or counts.get(normalize_name(info.name), 0) < limit

    def try_add(name: str) -> None:
        if len(deck) >= DECK_SIZE - 1 or not legal(name):
            return
        info = catalog.get(name)
        deck.append(info.name)
        key = normalize_name(info.name)
        counts[key] = counts.get(key, 0) + 1

    for n in cards or []:
        try_add(n)
    for label, src in sources or []:
        if len(deck) >= DECK_SIZE - 1:
            break
        for n in _load_source(label, src, source_timeout):
            try_add(n)
    for n in staples:
        try_add(n)
    # Basics are intrinsically legal and unlimited: use the built-in names
    # directly so a sparse/odd catalog record can never block the last step.
    deck += _basic_split(identity, DECK_SIZE - 1 - len(deck))
    out = [cmd_info.name] + deck
    if len(out) != DECK_SIZE:  # unreachable by construction; fail loudly, never short
        raise RuntimeError(f"fill_deck produced {len(out)} cards, expected {DECK_SIZE}")
    return out

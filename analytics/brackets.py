"""
Commander Brackets (BR-1): the official WotC bracket table + the Game Changers list.

Single source of truth for what each bracket 1-5 means. Later BR- cards read
this module; nothing else should hard-code bracket rules. The human-readable
version, with citations, is docs/BRACKETS_SPEC.md.

Two kinds of constraint (see CONSTRAINTS):
  HARD     - checked deterministically from the decklist (Game Changer count,
             mass land denial, extra-turn chains, two-card combos).
  ADVISORY - shown to the user, never used to place a deck (tutors, turn
             expectations, intent / "feel").

Game Changers come from Scryfall (`is:gamechanger`), cached in Redis for
CACHE_TTL_S. If Scryfall can't be reached the checked-in snapshot
analytics/data/game_changers.json is served instead (and not cached, so the
next call retries Scryfall). Refresh the snapshot with
`python scripts/refresh_game_changers.py`.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

from src.integrations.scryfall import ScryfallClient, ScryfallError
from src.shared.http.exceptions import HttpClientError

log = logging.getLogger("commander_lab.brackets")

# Date the table below was last checked against the official WotC articles.
RULES_AS_OF = "2026-10-15"

SOURCES: Dict[str, str] = {
    "intro_2025_02": "https://magic.wizards.com/en/news/announcements/introducing-commander-brackets-beta",
    "update_2025_04": "https://magic.wizards.com/en/news/announcements/commander-brackets-beta-update-april-22-2025",
    "update_2025_10": "https://magic.wizards.com/en/news/announcements/commander-brackets-beta-update-october-21-2025",
    "update_2026_02": "https://magic.wizards.com/en/news/announcements/commander-brackets-beta-update-february-9-2026",
    "format_page": "https://magic.wizards.com/en/formats/commander",
    "scryfall_query": "https://scryfall.com/search?q=is%3Agamechanger",
    "spellbook_brackets": "https://commanderspellbook.com/syntax-guide/",
    "commandersalt": "https://commandersalt.com/",
}

# --- constraint vocabulary --------------------------------------------------

NOT_ALLOWED = "not_allowed"
NO_CHAINS = "no_chains"            # extra turns: a few, never chained or looped
LATE_GAME_ONLY = "late_game_only"  # two-card combos: none that tend to come up early
ALLOWED = "allowed"
CONSTRAINT_VALUES = (NOT_ALLOWED, NO_CHAINS, LATE_GAME_ONLY, ALLOWED)

HARD = "hard"
ADVISORY = "advisory"


@dataclass(frozen=True)
class Constraint:
    key: str
    kind: str      # HARD | ADVISORY
    field: str     # Bracket attribute holding the per-bracket value
    label: str
    check: str     # how it's evaluated (for the spec / UI)


CONSTRAINTS: Tuple[Constraint, ...] = (
    Constraint("game_changers", HARD, "max_game_changers", "Game Changers",
               "count deck cards (incl. commanders) on the Scryfall is:gamechanger list"),
    Constraint("mass_land_denial", HARD, "mass_land_denial", "Mass land denial",
               "deck contains a card from the mass-land-denial list (BR-2)"),
    Constraint("extra_turns", HARD, "extra_turns", "Extra turns",
               "extra-turn card count, and whether any extra-turn loop/chain exists (BR-2)"),
    Constraint("two_card_combos", HARD, "two_card_combos", "Two-card combos",
               "Commander Spellbook two-card combos in the deck, by bracket tag (BR-3)"),
    Constraint("tutors", ADVISORY, "tutors", "Tutors",
               "shown only; WotC dropped tutor limits on 2025-10-21 (the best tutors are Game Changers)"),
    Constraint("turn_expectation", ADVISORY, "min_turns", "Turns before a win",
               "compared with the deck's estimated speed, never used to place it"),
    Constraint("intent", ADVISORY, "intent", "Intent / feel",
               "the user's own bracket choice wins over anything we infer"),
)


@dataclass(frozen=True)
class Bracket:
    number: int
    name: str
    intent: str
    expectations: Tuple[str, ...]
    max_game_changers: Optional[int]   # None = unlimited
    mass_land_denial: str
    extra_turns: str
    two_card_combos: str
    tutors: str
    min_turns: Optional[int]           # "expect at least N turns before you win or lose"; None = any turn
    sources: Tuple[str, ...] = field(default=())

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["expectations"] = list(self.expectations)
        d["sources"] = list(self.sources)
        return d


_INTRO, _OCT = SOURCES["intro_2025_02"], SOURCES["update_2025_10"]
_FEB, _FMT = SOURCES["update_2026_02"], SOURCES["format_page"]
_NO_TUTOR_LIMIT = ("No tutor limit since 2025-10-21; efficient tutors are Game Changers. "
                   "Advisory only.")

BRACKETS: Tuple[Bracket, ...] = (
    Bracket(
        number=1, name="Exhibition",
        intent="Theme over power: show off something unusual. Card legality can flex by pod agreement.",
        expectations=(
            "Decks prioritize a goal, theme, or idea over power",
            "Win conditions are highly thematic or substandard",
            "Gameplay is a chance to show off your creation",
        ),
        max_game_changers=0, mass_land_denial=NOT_ALLOWED, extra_turns=NOT_ALLOWED,
        two_card_combos=NOT_ALLOWED,
        tutors="Originally 'sparse'. " + _NO_TUTOR_LIMIT,
        min_turns=9, sources=(_INTRO, _OCT, _FEB, _FMT),
    ),
    Bracket(
        number=2, name="Core",
        intent="Unoptimized, straightforward, social. No longer defined as 'the average precon'.",
        expectations=(
            "Decks are unoptimized and straightforward, some cards picked for fun",
            "Win conditions are incremental, telegraphed on the board, and disruptable",
            "Low-pressure gameplay; each deck gets to show its plan",
        ),
        max_game_changers=0, mass_land_denial=NOT_ALLOWED, extra_turns=NO_CHAINS,
        two_card_combos=NOT_ALLOWED,
        tutors="Originally 'sparse'. " + _NO_TUTOR_LIMIT,
        min_turns=8, sources=(_INTRO, _OCT, _FMT),
    ),
    Bracket(
        number=3, name="Upgraded",
        intent="Powered up: strong synergy and card quality, up to three Game Changers.",
        expectations=(
            "Strong synergy and high card quality; can disrupt opponents",
            "Game Changers are likely value engines and game-ending spells",
            "Can win in one big turn from hand, from steadily accrued resources",
        ),
        max_game_changers=3, mass_land_denial=NOT_ALLOWED, extra_turns=NO_CHAINS,
        two_card_combos=LATE_GAME_ONLY,
        tutors=_NO_TUTOR_LIMIT,
        min_turns=6, sources=(_INTRO, _OCT, _FMT),
    ),
    Bracket(
        number=4, name="Optimized",
        intent="High power, no restrictions beyond the banned list, but not built for the cEDH metagame.",
        expectations=(
            "Lethal, consistent, and fast",
            "Game Changers are likely fast mana, snowballing engines, free disruption, tutors",
            "Efficient, instantaneous win conditions",
        ),
        max_game_changers=None, mass_land_denial=ALLOWED, extra_turns=ALLOWED,
        two_card_combos=ALLOWED,
        tutors="Unrestricted.",
        min_turns=4, sources=(_INTRO, _OCT, _FMT),
    ),
    Bracket(
        number=5, name="cEDH",
        intent="Competitive: built for the cEDH metagame; winning over self-expression.",
        expectations=(
            "Meticulously designed for the cEDH metagame",
            "Win conditions optimized for efficiency and consistency",
            "Intricate play with razor-thin margins",
        ),
        max_game_changers=None, mass_land_denial=ALLOWED, extra_turns=ALLOWED,
        two_card_combos=ALLOWED,
        tutors="Unrestricted.",
        min_turns=None, sources=(_INTRO, _OCT, _FMT),
    ),
)

_BY_NAME = {b.name.lower(): b for b in BRACKETS}


def get_bracket(key: Union[int, str, None]) -> Optional[Bracket]:
    """Bracket by number (3, "3", "Bracket 3") or name ("upgraded", "cEDH")."""
    if key is None or isinstance(key, bool):
        return None
    if isinstance(key, int):
        return BRACKETS[key - 1] if 1 <= key <= len(BRACKETS) else None
    s = str(key).strip().lower()
    if s.startswith("bracket"):
        s = s[len("bracket"):].strip()
    if s.isdigit():
        return get_bracket(int(s))
    return _BY_NAME.get(s)


def _require(number: int) -> Bracket:
    b = get_bracket(number)
    if b is None:
        raise ValueError(f"unknown bracket {number!r}")
    return b


def game_changers_allowed(bracket: Union[int, str], count: int) -> bool:
    """Is `count` Game Changers within this bracket's hard limit?"""
    if count < 0:
        raise ValueError("count must be >= 0")
    b = _require(bracket)  # type: ignore[arg-type]
    return b.max_game_changers is None or count <= b.max_game_changers


def min_bracket_for_game_changers(count: int) -> int:
    """Lowest bracket whose Game Changer limit admits `count`."""
    if count < 0:
        raise ValueError("count must be >= 0")
    for b in BRACKETS:
        if b.max_game_changers is None or count <= b.max_game_changers:
            return b.number
    return BRACKETS[-1].number  # unreachable: the last brackets are unlimited


def brackets_payload() -> Dict[str, Any]:
    return {
        "as_of": RULES_AS_OF,
        "brackets": [b.to_dict() for b in BRACKETS],
        "constraints": [asdict(c) for c in CONSTRAINTS],
        "sources": dict(SOURCES),
    }


# --- Game Changers ----------------------------------------------------------

SCRYFALL_QUERY = "is:gamechanger"
CACHE_KEY = "brackets:game_changers"
CACHE_TTL_S = 24 * 3600
FALLBACK_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "game_changers.json")

SOURCE_SCRYFALL = "scryfall"
SOURCE_CACHE = "cache"
SOURCE_FALLBACK = "fallback"


class GameChangersUnavailable(Exception):
    """Scryfall couldn't give us the list (down, error, or an empty result)."""


@dataclass
class GameChangerList:
    cards: List[str]
    source: str
    fetched_at: str

    @property
    def count(self) -> int:
        return len(self.cards)

    def to_dict(self) -> Dict[str, Any]:
        return {"count": self.count, "source": self.source, "fetched_at": self.fetched_at,
                "cards": list(self.cards)}


def name_key(name: str) -> str:
    """Lower-cased front face, same rule as analytics/card2vec.name_key."""
    n = (name or "").replace(" // ", " / ").split(" / ")[0]
    return n.strip().lower()


def find_game_changers(deck_names: Iterable[str], game_changers: Iterable[str]) -> List[str]:
    """Game Changers present in the deck (canonical names, deck order, no dupes)."""
    canon = {name_key(n): n for n in game_changers}
    out: List[str] = []
    for n in deck_names:
        hit = canon.get(name_key(n))
        if hit and hit not in out:
            out.append(hit)
    return out


def is_game_changer(name: str, game_changers: Iterable[str]) -> bool:
    k = name_key(name)
    return bool(k) and any(name_key(g) == k for g in game_changers)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_fallback(path: str = FALLBACK_PATH) -> GameChangerList:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return GameChangerList(cards=list(data["cards"]), source=SOURCE_FALLBACK, fetched_at=data["as_of"])


async def fetch_game_changers(client: ScryfallClient) -> List[str]:
    """All `is:gamechanger` cards from Scryfall, sorted by name."""
    try:
        names = {c.name async for c in client.search_all_cards(SCRYFALL_QUERY, unique="cards", order="name")}
    except (ScryfallError, HttpClientError) as exc:
        raise GameChangersUnavailable(str(exc)) from exc
    if not names:
        raise GameChangersUnavailable("Scryfall returned no Game Changers")
    return sorted(names)


def _read_cache(r) -> Optional[GameChangerList]:
    if r is None:
        return None
    try:
        raw = r.get(CACHE_KEY)
        if not raw:
            return None
        data = json.loads(raw)
        return GameChangerList(cards=list(data["cards"]), source=SOURCE_CACHE, fetched_at=data["fetched_at"])
    except Exception as exc:  # redis down or junk in the key: just refetch
        log.warning("game changers cache unreadable: %s", exc)
        return None


def _write_cache(r, gc: GameChangerList) -> None:
    if r is None:
        return
    try:
        r.setex(CACHE_KEY, CACHE_TTL_S, json.dumps({"cards": gc.cards, "fetched_at": gc.fetched_at}))
    except Exception as exc:
        log.warning("game changers cache write failed: %s", exc)


async def get_game_changers(r, client: Optional[ScryfallClient] = None, refresh: bool = False) -> GameChangerList:
    """Redis cache -> Scryfall -> checked-in snapshot. Never raises for network trouble."""
    if not refresh:
        cached = _read_cache(r)
        if cached is not None:
            return cached
    owned = client is None
    if owned:
        client = ScryfallClient()
    try:
        names = await fetch_game_changers(client)
    except GameChangersUnavailable as exc:
        log.warning("Scryfall Game Changers unavailable, using snapshot: %s", exc)
        return load_fallback()
    finally:
        if owned:
            await client.aclose()
    gc = GameChangerList(cards=names, source=SOURCE_SCRYFALL, fetched_at=_now())
    _write_cache(r, gc)
    log.info("game changers fetched from Scryfall: %d cards", gc.count)
    return gc

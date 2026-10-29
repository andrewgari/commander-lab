"""
Deterministic bracket checks on a decklist (BR-3).

    check_deck_brackets(deck, game_changers=..., combos=...) -> BracketFindings

Per-rule facts about one deck, no target bracket needed. Each hard rule gives
a *floor* (the lowest bracket that rule allows) and lists the exact cards that
caused it; `minimum_bracket` is the highest floor, i.e. the lowest bracket
where every HARD rule passes. Tutors are advisory and never move it. Bracket
5 is never computed (4 vs 5 is intent; docs/BRACKETS_SPEC.md).

Hard rules and floors (from analytics/brackets.BRACKETS):
  game_changers     count -> brackets.min_bracket_for_game_changers (0 -> 1, 1-3 -> 3, 4+ -> 4)
  mass_land_denial  any card -> 4
  extra_turns       any card -> 2; an in-deck Spellbook combo producing "Infinite turns"
                    (no unverified template requirement) -> 4
  two_card_combos   any Spellbook combo of <= 2 cards and no extra requirements -> 3;
                    tagged Ruthless (R) or Banned (B) = "early" -> 4

Data, all deterministic:
  - Game Changers: passed in (brackets.get_game_changers), default the BR-1 snapshot.
  - Mass land denial / extra turns / tutors: analytics/data/bracket_cards.json,
    built from Scryfall Tagger otags with the same queries Commander Spellbook
    uses for its card flags (scripts/refresh_bracket_cards.py), plus the curated
    overrides below. Optional oracle text (the interaction tagger's
    `card_interactions:*` cache) catches extra-turn / "destroy all lands" cards
    newer than the snapshot.
  - Combos: Commander Spellbook find-my-combos output (analytics/interactions/combos.py),
    passed in. If it's missing the combo rule is reported as not checked.
No LLM, no network inside check_deck_brackets.
"""
from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Union

from pydantic import BaseModel, Field

from src.server.domain.analytics import brackets as br

DATA_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "bracket_cards.json")

SCRYFALL_QUERIES: Dict[str, str] = {
    "mass_land_denial": "otag:mass-land-denial",
    "extra_turns": "otag:extra-turn",
    "tutors": "otag:tutor -otag:tutor-land -otag:tutor-seek mv<=3",
}

# Curated overrides on top of the Tagger snapshot (names, any case).
# WotC's own mass-land-denial examples are pinned so a Tagger edit can't drop them.
MASS_LAND_DENIAL_INCLUDE = ("Armageddon", "Ruination", "Sunder", "Winter Orb", "Blood Moon")
MASS_LAND_DENIAL_EXCLUDE: tuple = ()
EXTRA_TURNS_INCLUDE: tuple = ()
EXTRA_TURNS_EXCLUDE: tuple = ()
TUTORS_INCLUDE: tuple = ()
TUTORS_EXCLUDE: tuple = ()

# Commander Spellbook bracket tags (backend Variant.BracketTag).
SPELLBOOK_TAGS = {"R": "Ruthless", "S": "Spicy", "P": "Powerful", "O": "Oddball",
                  "C": "Core", "E": "Exhibition", "B": "Banned"}
EARLY_COMBO_TAGS = frozenset({"R", "B"})   # spec: "early" = Ruthless; Banned is worse still

EXTRA_TURN_RE = re.compile(r"\btakes? an extra turn\b|\bextra turns? after this one\b", re.I)
LAND_WIPE_RE = re.compile(r"\bdestroy all lands\b|\bsacrifices? all lands\b|\bexile all lands\b", re.I)
INFINITE_TURNS_RE = re.compile(r"\binfinite (extra )?turns\b", re.I)

NameList = Union[Mapping[str, Any], Sequence[Any]]


def name_key(name: str) -> str:
    return br.name_key(name)


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class CardHit(BaseModel):
    name: str
    source: str = Field("list", description="list (snapshot/override) or oracle_text (regex fallback)")


class GameChangerFinding(BaseModel):
    rule: str = "game_changers"
    kind: str = br.HARD
    count: int
    cards: List[str]
    floor: int


class CardRuleFinding(BaseModel):
    rule: str
    kind: str = br.HARD
    count: int
    cards: List[CardHit]
    floor: int


class ComboHit(BaseModel):
    id: str
    url: Optional[str] = None
    cards: List[str]
    bracket_tag: Optional[str] = None
    bracket_tag_name: Optional[str] = None
    early: bool
    produces: List[str] = Field(default_factory=list)


class ExtraTurnFinding(CardRuleFinding):
    rule: str = "extra_turns"
    loops: List[ComboHit] = Field(default_factory=list, description="in-deck Spellbook combos that repeat extra turns")


class ComboFinding(BaseModel):
    rule: str = "two_card_combos"
    kind: str = br.HARD
    checked: bool
    count: int
    combos: List[ComboHit]
    floor: int


class TutorFinding(BaseModel):
    rule: str = "tutors"
    kind: str = br.ADVISORY
    count: int
    cards: List[CardHit]


class FloorReason(BaseModel):
    rule: str
    floor: int
    cards: List[str]


class BracketFindings(BaseModel):
    minimum_bracket: int
    minimum_bracket_name: str
    floors: List[FloorReason] = Field(description="every hard rule above bracket 1, with the cards that raised it")
    game_changers: GameChangerFinding
    mass_land_denial: CardRuleFinding
    extra_turns: ExtraTurnFinding
    two_card_combos: ComboFinding
    tutors: TutorFinding
    notes: List[str] = Field(default_factory=list)
    data: Dict[str, Any] = Field(default_factory=dict, description="where each list came from")


# ---------------------------------------------------------------------------
# Data
# ---------------------------------------------------------------------------


@lru_cache(maxsize=4)
def _load_snapshot(path: str = DATA_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def card_lists(snapshot: Optional[dict] = None) -> Dict[str, Dict[str, str]]:
    """{rule: {name_key: canonical name}} = snapshot + includes - excludes."""
    snap = snapshot if snapshot is not None else _load_snapshot()
    spec = {
        "mass_land_denial": (MASS_LAND_DENIAL_INCLUDE, MASS_LAND_DENIAL_EXCLUDE),
        "extra_turns": (EXTRA_TURNS_INCLUDE, EXTRA_TURNS_EXCLUDE),
        "tutors": (TUTORS_INCLUDE, TUTORS_EXCLUDE),
    }
    out: Dict[str, Dict[str, str]] = {}
    for rule, (inc, exc) in spec.items():
        m = {name_key(n): n for n in list(snap.get(rule, [])) + list(inc)}
        for n in exc:
            m.pop(name_key(n), None)
        out[rule] = m
    return out


def _default_game_changers() -> List[str]:
    return br.load_fallback().cards


# ---------------------------------------------------------------------------
# Deck input
# ---------------------------------------------------------------------------


def deck_card_names(deck: Union[Mapping[str, Any], Iterable[Any]]) -> List[str]:
    """Unique card names (commanders first, then deck order). Accepts a deck
    dict ({commanders: [...], cards: [{name, quantity}]}), or an iterable of
    names / {name} dicts."""
    if isinstance(deck, Mapping):
        items: List[Any] = list(deck.get("commanders") or []) + list(deck.get("cards") or [])
    else:
        items = list(deck)
    seen, out = set(), []
    for it in items:
        n = it.get("name") if isinstance(it, Mapping) else it
        if not n or not isinstance(n, str):
            continue
        k = name_key(n)
        if k and k not in seen:
            seen.add(k)
            out.append(n)
    return out


def _combo_variants(combos: Any) -> Optional[List[dict]]:
    """Normalized in-deck variants from deck_combos() / find_my_combos() / a list."""
    if combos is None:
        return None
    if isinstance(combos, Mapping):
        if "in_deck" in combos:
            return list(combos.get("in_deck") or [])
        if "included" in combos:
            return list(combos.get("included") or [])
        return []
    return list(combos)


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


def _hits(names: List[str], lst: Dict[str, str], oracle: Mapping[str, Mapping[str, Any]],
          pattern: Optional[re.Pattern]) -> List[CardHit]:
    out = []
    okeys = {name_key(k): v for k, v in (oracle or {}).items()}
    for n in names:
        if name_key(n) in lst:
            out.append(CardHit(name=n, source="list"))
        elif pattern is not None:
            text = (okeys.get(name_key(n)) or {}).get("oracle_text") or ""
            if text and pattern.search(text):
                out.append(CardHit(name=n, source="oracle_text"))
    return out


def _combo_hit(v: Mapping[str, Any]) -> ComboHit:
    tag = v.get("bracket_tag")
    return ComboHit(id=str(v.get("id")), url=v.get("url"), cards=list(v.get("card_names") or []),
                    bracket_tag=tag, bracket_tag_name=SPELLBOOK_TAGS.get(tag or ""),
                    early=tag in EARLY_COMBO_TAGS,
                    produces=list(v.get("produces") or []))


def is_two_card(v: Mapping[str, Any]) -> bool:
    """At most two cards (by quantity) and no extra template requirements."""
    cards = v.get("cards")
    if cards:
        n = sum(int(c.get("quantity") or 1) for c in cards)
    else:
        n = len(v.get("card_names") or [])
    return 1 <= n <= 2 and not (v.get("requires") or [])


def is_extra_turn_loop(v: Mapping[str, Any]) -> bool:
    """A Spellbook combo whose result is explicitly infinite turns, with no
    template requirement (find-my-combos doesn't verify templates, and an
    extra-turn card plus some other infinite isn't a turn loop: Gonti's Aether
    Heart exiles itself)."""
    if v.get("requires"):
        return False
    return produces_infinite_turns(v)


def produces_infinite_turns(v: Mapping[str, Any]) -> bool:
    return any(INFINITE_TURNS_RE.search(p or "") for p in v.get("produces") or [])


def check_deck_brackets(
    deck: Union[Mapping[str, Any], Iterable[Any]],
    *,
    game_changers: Optional[Iterable[str]] = None,
    combos: Any = None,
    oracle: Optional[Mapping[str, Mapping[str, Any]]] = None,
    snapshot: Optional[dict] = None,
) -> BracketFindings:
    """Pure: decklist + fixed data -> BracketFindings.

    deck           deck dict (commanders + cards) or an iterable of names.
    game_changers  Game Changer names; default the BR-1 snapshot.
    combos         Commander Spellbook in-deck combos: deck_combos() / find_my_combos()
                   output or a list of normalized variants. None = combo rule not checked.
    oracle         optional {name: {oracle_text}} for the regex fallback on cards
                   missing from the snapshot.
    snapshot       optional bracket_cards.json contents (tests); default the checked-in file.
    """
    names = deck_card_names(deck)
    oracle = oracle or {}
    lists = card_lists(snapshot)
    gc_source = "argument"
    if game_changers is None:
        game_changers, gc_source = _default_game_changers(), "snapshot"
    gc_list = list(game_changers)
    notes: List[str] = []

    # Game Changers (commanders count).
    gcs = br.find_game_changers(names, gc_list)
    # keep the deck's spelling so "the exact cards" are the deck's cards
    gc_canon = {name_key(g) for g in gcs}
    gc_cards = [n for n in names if name_key(n) in gc_canon]
    gc = GameChangerFinding(count=len(gc_cards), cards=gc_cards,
                            floor=br.min_bracket_for_game_changers(len(gc_cards)))

    # Mass land denial.
    mld_hits = _hits(names, lists["mass_land_denial"], oracle, LAND_WIPE_RE)
    mld = CardRuleFinding(rule="mass_land_denial", count=len(mld_hits), cards=mld_hits,
                          floor=4 if mld_hits else 1)

    # Combos (Spellbook).
    variants = _combo_variants(combos)
    checked = variants is not None
    variants = variants or []
    two = [_combo_hit(v) for v in variants if is_two_card(v)]
    two.sort(key=lambda c: (not c.early, c.id))
    combo_floor = (4 if any(c.early for c in two) else 3) if two else 1
    combo = ComboFinding(checked=checked, count=len(two), combos=two, floor=combo_floor)
    if not checked:
        notes.append("Two-card combos not checked (no Commander Spellbook data); minimum bracket may be higher.")

    # Extra turns: cards, plus loops from Spellbook combos.
    et_hits = _hits(names, lists["extra_turns"], oracle, EXTRA_TURN_RE)
    loops = [_combo_hit(v) for v in variants if is_extra_turn_loop(v)]
    for v in variants:
        if v.get("requires") and produces_infinite_turns(v):
            needs = ", ".join(t.get("template") or "?" for t in v["requires"])
            notes.append(f"Possible extra-turn loop {' + '.join(v.get('card_names') or [])} (Spellbook {v.get('id')}) "
                         f"also needs: {needs}. Not verified, so it doesn't move the minimum.")
    et_floor = 4 if loops else (2 if et_hits else 1)
    et = ExtraTurnFinding(count=len(et_hits), cards=et_hits, loops=loops, floor=et_floor)
    if len(et_hits) >= 3 and not loops:
        notes.append(f"{len(et_hits)} extra-turn cards: Brackets 2-3 allow only a few, never chained. "
                     "No loop found, so this doesn't move the minimum; check it by hand.")

    # Tutors (advisory).
    tut_hits = _hits(names, lists["tutors"], oracle, None)
    tutors = TutorFinding(count=len(tut_hits), cards=tut_hits)

    floors = []
    for rule, floor, cards in (
        ("game_changers", gc.floor, gc.cards),
        ("mass_land_denial", mld.floor, [h.name for h in mld.cards]),
        ("extra_turns", et.floor, sorted({n for c in loops for n in c.cards}) if loops else [h.name for h in et.cards]),
        ("two_card_combos", combo.floor,
         sorted({n for c in two if (c.early or combo.floor == 3) for n in c.cards})),
    ):
        if floor > 1:
            floors.append(FloorReason(rule=rule, floor=floor, cards=cards))
    floors.sort(key=lambda f: (-f.floor, f.rule))
    minimum = max([1] + [f.floor for f in floors])
    if minimum == 1:
        notes.append("Passes every Bracket 1 hard rule; Exhibition is about theme, so that's your call, "
                     "not a suggestion.")

    snap = snapshot if snapshot is not None else _load_snapshot()
    return BracketFindings(
        minimum_bracket=minimum,
        minimum_bracket_name=br._require(minimum).name,
        floors=floors,
        game_changers=gc, mass_land_denial=mld, extra_turns=et, two_card_combos=combo, tutors=tutors,
        notes=notes,
        data={"rules_as_of": br.RULES_AS_OF, "game_changers_source": gc_source,
              "game_changers_count": len(gc_list), "card_lists_as_of": snap.get("as_of"),
              "deck_cards": len(names)},
    )


# ---------------------------------------------------------------------------
# Convenience: gather the live inputs for one deck (not pure; used for live checks)
# ---------------------------------------------------------------------------


async def check_deck_brackets_live(r, deck: Mapping[str, Any], refresh: bool = False) -> BracketFindings:
    """Live Game Changers (Redis/Scryfall) + Spellbook combos (cached) + the
    tagger's cached oracle text, then the pure check."""
    import asyncio

    from src.server.domain.analytics.interactions import combos as combos_mod
    from src.server.domain.analytics.interactions.tagger import tag_key

    gc = await br.get_game_changers(r)
    # find_my_combos directly (same cache key as deck_combos), not deck_combos:
    # that one also builds the ownership index with KEYS instance:*, unused here.
    client = combos_mod.SpellbookClient(r)
    commanders = [c for c in deck.get("commanders") or [] if c]
    try:
        combo_data = await asyncio.to_thread(client.find_my_combos, commanders,
                                             combos_mod._deck_names(dict(deck)), refresh)
    except combos_mod.SpellbookError:
        combo_data = None
    oracle: Dict[str, Dict[str, Any]] = {}
    names = deck_card_names(deck)
    try:
        raws = r.mget([tag_key(n) for n in names]) if names else []
    except Exception:  # noqa: BLE001 - oracle text is only a fallback
        raws = []
    for n, raw in zip(names, raws):
        try:
            card = (json.loads(raw) or {}).get("card") or {} if raw else {}
        except (TypeError, ValueError):
            continue
        if card.get("oracle_text"):
            oracle[n] = card
    out = check_deck_brackets(deck, game_changers=gc.cards, combos=combo_data, oracle=oracle)
    out.data["game_changers_source"] = gc.source
    if combo_data is not None:
        out.data["combos_fetched_at"] = combo_data.get("fetched_at")
        out.data["combos_stale"] = combo_data.get("stale", False)
    return out

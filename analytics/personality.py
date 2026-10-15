"""PS-2: deck personality scores (Timmy / Johnny / Spike / Vorthos / Melvin).

The rules, weights and bands are docs/PERSONALITY_SPEC.md (PS-1); this
module is that spec in code. Changing a list, weight or band is a spec
change: update the doc and its control row in the same PR and bump
PERSONALITY_VERSION.

Two layers:

* `score_deck_personality(deck, journal=None, **inputs)` is pure and
  deterministic: the deck plus already-loaded inputs (inventory card rows,
  composition, Spellbook combos, interaction graph, engines, EDHREC rows,
  power estimate, Game Changers list) -> PersonalityScores. Any input left
  out (None) scores its signals 0 and adds a note; it never raises.
* `load_inputs(r, deck)` / `score_deck(r, deck)` gather those inputs from
  Redis only. Spellbook and EDHREC are read cache-only (a client that
  refuses to fetch), so scoring never makes a network call. No LLM.
"""
from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, Iterable, List, Literal, Mapping, Optional, Sequence, Tuple, Union

from pydantic import BaseModel, Field

from .composition import _norm, compute_composition

log = logging.getLogger(__name__)

PERSONALITY_VERSION = 1
AXES = ("timmy", "johnny", "spike", "vorthos", "melvin")

# ---------------------------------------------------------------------------
# Output model (spec section 7)
# ---------------------------------------------------------------------------


class AxisEvidence(BaseModel):
    signal: str                        # "T1", "J3", ..., or "journal"
    label: str
    value: Union[int, float, str, None] = None
    sub_score: float = 0.0             # 0..1 after the ramp (journal rows: 0)
    points: float = 0.0                # weight * sub_score, or the journal nudge (+/-)
    cards: List[str] = Field(default_factory=list)
    fields: List[str] = Field(default_factory=list)


class AxisScore(BaseModel):
    score: int
    card_score: float
    journal_nudge: float
    evidence: List[AxisEvidence]


class PersonalityScores(BaseModel):
    version: int = PERSONALITY_VERSION
    timmy: AxisScore
    johnny: AxisScore
    spike: AxisScore
    vorthos: AxisScore
    melvin: AxisScore
    leans_toward: Literal["timmy", "johnny", "spike", "vorthos", "melvin", "balanced"]
    runner_up: Optional[str] = None
    journal_used: bool
    notes: List[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Constant lists (spec section 6)
# ---------------------------------------------------------------------------

SPLASHY = [re.compile(p) for p in (
    r"additional combat", r"extra combat", r"take an extra turn", r"\bdouble (?:the|its|their|that)",
    r"twice that many", r"you win the game", r"can't be countered", r"each opponent loses (?:\d+|x)",
    r"\+x/\+x", r"deals damage equal to", r"without paying (?:its|their) mana cost")]

TUTOR = re.compile(r"search your library for (?:a|an|up to \w+) (?![^.]*\bland\b)[^.]*card")

FAST_MANA = {"Mana Crypt", "Sol Ring", "Mana Vault", "Mox Diamond", "Chrome Mox", "Mox Opal", "Lotus Petal",
             "Jeweled Lotus", "Lion's Eye Diamond", "Grim Monolith", "Dark Ritual", "Cabal Ritual"}

EVERGREEN = {"flying", "trample", "vigilance", "haste", "first strike", "double strike", "deathtouch", "lifelink",
             "reach", "menace", "hexproof", "indestructible", "ward", "defender", "flash", "scry", "equip",
             "enchant", "fight", "mill", "surveil", "investigate", "partner", "partner with", "crew", "protection",
             "transform", "cycling", "kicker", "flashback"}

ABILITY_WORDS = {"adamant", "addendum", "alliance", "battalion", "bloodrush", "celebration", "channel", "chroma",
                 "cohort", "constellation", "converge", "council's dilemma", "coven", "delirium", "descend 4",
                 "descend 8", "domain", "eerie", "eminence", "enrage", "fateful hour", "fathomless descent",
                 "ferocious", "flurry", "formidable", "grandeur", "hellbent", "heroic", "imprint", "inspired",
                 "join forces", "kinship", "landfall", "lieutenant", "magecraft", "metalcraft", "morbid",
                 "pack tactics", "paradox", "parley", "radiance", "raid", "rally", "revolt", "secret council",
                 "spell mastery", "strive", "survival", "sweep", "tempting offer", "threshold", "undergrowth",
                 "valiant", "void", "will of the council"}

DASH_RULES_KEYWORDS = {"companion", "exhaust", "doctor's companion"}

RULES = [re.compile(p) for p in (
    r"\b(?:time|lore|fade|age|charge|oil|verse|quest|level|loyalty) counters?\b",
    r"\bremove (?:a|an|one|x|that many|\w+) [^.]*counters?\b", r"\bproliferate\b",
    r"\bphases? (?:out|in)\b",
    r"\b(?:enter|enters) as a copy\b|\bbecomes? a copy\b|\bcreate[^.]* copy of\b",
    r"\bgains? suspend\b", r"\btime travel\b",
    r"\bcast [^.]*from exile\b|\bplay [^.]*from exile\b", r"\buntap (?:another )?target\b",
    r"\bexchange control\b", r"\bcopy target (?:spell|ability|instant|sorcery)\b",
    r"\bplaneswalk\b", r"\bthe stack\b", r"\bpair\b")]

RULES_KEYWORDS = {"suspend", "vanishing", "fading", "time travel", "storm", "cascade", "foretell", "plot",
                  "adventure", "soulbond", "buyback", "rebound", "split second", "mutate", "ninjutsu", "morph",
                  "disguise", "cloak", "manifest", "dredge", "phasing", "companion", "doctor's companion"}

CANTRIP = re.compile(r"\bdraw a card\b|\bdraw (?:two|three) cards\b|\bscry\b|\blook at the top\b|\bsurveil\b")

ODD_SUBTYPES = {"Saga", "Vehicle", "Class", "Case", "Room", "Attraction", "Contraption", "Spacecraft"}
ODD_TEXT = [re.compile(p) for p in (
    r"you win the game", r"\bphases? out\b", r"\bplaneswalk\b", r"venture into", r"the ring tempts you",
    r"\bvote\b", r"\bcoin\b", r"roll (?:a|\w+) d\d+|\broll a die\b", r"\bdungeon\b", r"\bmonarch\b",
    r"\binitiative\b", r"\bday\b.*\bnight\b")]

LEXICON = {
    "timmy": ["big", "huge", "fatty", "fatties", "stompy", "smash", "swing", "giant", "giants", "massive",
              "overwhelm", "epic", "splashy", "explosive", "swarm"],
    "johnny": ["combo", "combos", "synergy", "engine", "brew", "jank", "janky", "unusual", "novel", "build-around",
               "off-meta", "unique", "weird"],
    "spike": ["optimize", "optimized", "efficient", "competitive", "cedh", "tuned", "consistent", "fast", "tight",
              "meta"],
    "vorthos": ["theme", "themed", "flavor", "flavour", "lore", "story", "character", "characters", "tribal",
                "universe", "canon", "iconic"],
    "melvin": ["rules", "mechanic", "mechanics", "trigger", "triggers", "loop", "loops", "layer", "layers",
               "timing", "puzzle", "counters"],
}
LEXICON_CAP = 4
NUDGE_CAP = 10.0

WIN_NUDGE = {"combat": {"timmy": 4}, "commander_damage": {"timmy": 2}, "combo": {"johnny": 3, "spike": 2},
             "alternate_wincon": {"melvin": 3, "johnny": 2}, "lockout": {"spike": 3}}
ARCH_NUDGE = {"aggro": {"timmy": 3}, "voltron": {"timmy": 3}, "ramp": {"timmy": 3},
              "combo": {"johnny": 3, "spike": 2}, "stax": {"spike": 3}, "control": {"spike": 2},
              "tribal": {"vorthos": 4}, "group_hug": {"johnny": 2}, "spellslinger": {"melvin": 2}}
BRACKET_NUDGE = {1: -3, 2: -1, 3: 0, 4: 2, 5: 4}

NOTE_NO_JOURNAL = "no journal: cards-only score"
NOTE_COMBOS = "combos unavailable"
NOTE_GRAPH = "interaction graph missing or stale"
NOTE_EDHREC = "EDHREC unavailable"
NOTE_POWER = "power estimate unavailable"
NOTE_BRACKETS = "bracket check unavailable"
STALE_UNTAGGED_SHARE = 0.25
MISSING_META_NOTE_OVER = 5

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def ramp(x: float, lo: float, hi: float) -> float:
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def _round(x: float) -> int:
    """floor(x + 0.5), clamped to 0..100 (Python's round() rounds to even)."""
    return int(max(0, min(100, math.floor(x + 0.5))))


def _row_rank(row: Mapping) -> tuple:
    status = row.get("ownership_status")
    s = 0 if status == "in_deck" else 1 if status == "not_owned" else 2
    st = row.get("set") or ""
    return (s, st == "", st, str(row.get("instance_id") or ""))


def _has_meta(row: Mapping) -> bool:
    """build_inventory fills type "Unknown" and empty text when card_meta is missing."""
    if row.get("type_line") or row.get("oracle_text") or row.get("keywords"):
        return True
    return (row.get("type") or "Unknown") != "Unknown"


def pick_card_rows(cards: Union[Mapping[str, Mapping], Iterable[Mapping], None]) -> Dict[str, Mapping]:
    """One row per card name: bound before considered, set ascending (empty last), instance_id."""
    if not cards:
        return {}
    rows = list(cards.values()) if isinstance(cards, Mapping) else list(cards)
    best: Dict[str, Mapping] = {}
    for row in rows:
        if not row or not row.get("name") or not _has_meta(row):
            continue
        cur = best.get(row["name"])
        if cur is None or _row_rank(row) < _row_rank(cur):
            best[row["name"]] = row
    return best


class _Card:
    __slots__ = ("name", "front", "subs", "text", "raw", "mv", "kws", "kws_raw", "set")

    def __init__(self, row: Mapping):
        self.name = row["name"]
        if row.get("type_line"):  # a complete type line (when supplied) beats the primary-type-only row
            tl = row["type_line"].split(" // ")[0]
            front, _, subs = tl.partition(" \u2014 ")
            if not subs:
                front, _, subs = tl.partition(" - ")
            self.front = front
            # Subtypes from the structured list when the row has one: splitting the
            # line on spaces would break multi-word subtypes ("Time Lord").
            self.subs = set(row.get("sub_types") or []) or set(subs.split())
        else:
            self.front = " ".join(list(row.get("super_types") or []) + [row.get("type") or ""])
            self.subs = set(row.get("sub_types") or [])
        self.raw = row.get("oracle_text") or ""
        self.text = _norm(self.raw)
        try:
            self.mv = float(row.get("cmc") or 0)
        except (TypeError, ValueError):
            self.mv = 0.0
        self.kws_raw = list(row.get("keywords") or [])
        self.kws = [k.lower() for k in self.kws_raw]
        self.set = row.get("set") or ""

    @property
    def is_land(self) -> bool:
        return "Land" in self.front and "Creature" not in self.front

    @property
    def is_creature(self) -> bool:
        return "Creature" in self.front

    def flavor_words(self) -> List[str]:
        out = []
        for k in self.kws_raw:
            if k.lower() in ABILITY_WORDS or k.lower() in DASH_RULES_KEYWORDS:
                continue
            if re.search(r"(^|\n)" + re.escape(k) + " \u2014", self.raw):
                out.append(k)
        return out


def _deck_names(deck: Mapping) -> List[str]:
    names = []
    seen = set()
    for c in deck.get("cards") or []:
        n = c.get("name") if isinstance(c, Mapping) else c
        if n and n not in seen:
            seen.add(n)
            names.append(n)
    for n in deck.get("commanders") or []:
        if n and n not in seen:
            seen.add(n)
            names.append(n)
    return names


def _combo_list(combos: Any) -> Optional[List[dict]]:
    if combos is None:
        return None
    if isinstance(combos, Mapping):
        for k in ("included", "in_deck"):
            if isinstance(combos.get(k), list):
                return list(combos[k])
        return None
    return list(combos)


def _engine_list(engines: Any) -> Optional[List[dict]]:
    if engines is None:
        return None
    if isinstance(engines, Mapping):
        return list(engines.get("engines") or [])
    return list(engines)


class _Axis:
    """Evidence rows show rounded numbers; the axis total sums the exact ones,
    so display rounding can never move the final half-up score."""

    def __init__(self):
        self.evidence: List[AxisEvidence] = []
        self.total = 0.0

    def add(self, signal: str, label: str, weight: float, sub: float, value=None, cards=(), fields=()):
        self.total += weight * sub
        self.evidence.append(AxisEvidence(signal=signal, label=label, value=value, sub_score=round(sub, 4),
                                          points=round(weight * sub, 4), cards=sorted(set(cards)),
                                          fields=list(fields)))
        return weight * sub


# ---------------------------------------------------------------------------
# Journal (spec section 4)
# ---------------------------------------------------------------------------


def _resolve_journal(journal: Any) -> Optional[dict]:
    """None / never-saved -> None; a stored record or an API payload -> the public form."""
    if not journal:
        return None
    if isinstance(journal, Mapping) and "exists" in journal:
        if not journal.get("exists"):
            return None
        journal = journal.get("journal") or {}
    try:
        from analytics.journal import public
        return public(journal)
    except Exception as exc:  # noqa: BLE001 - a malformed journal must not break scoring
        log.warning("journal could not be resolved: %s", exc)
        return None


def journal_nudges(j: Mapping) -> Dict[str, List[Tuple[str, float]]]:
    out: Dict[str, List[Tuple[str, float]]] = {a: [] for a in AXES}
    scale = (j.get("target_power_level") or {}).get("scale")
    if isinstance(scale, (int, float)):
        out["spike"].append((f"target_power_level.scale={scale}", max(-5.0, min(5.0, (scale - 6) * 2.5))))
    tb = j.get("target_bracket")
    if tb in BRACKET_NUDGE and BRACKET_NUDGE[tb]:
        out["spike"].append((f"target_bracket={tb}", BRACKET_NUDGE[tb]))
    wc = j.get("preferred_win_conditions") or {}
    for a, v in WIN_NUDGE.get(wc.get("primary"), {}).items():
        out[a].append((f"preferred_win_conditions.primary={wc['primary']}", v))
    for s in wc.get("secondary") or []:
        for a, v in WIN_NUDGE.get(s, {}).items():
            out[a].append((f"preferred_win_conditions.secondary={s}", v / 2))
    for s in wc.get("disliked_or_excluded") or []:
        for a, v in WIN_NUDGE.get(s, {}).items():
            out[a].append((f"preferred_win_conditions.disliked_or_excluded={s}", -v / 2))
    ap = j.get("archetype_preferences") or {}
    for a, v in ARCH_NUDGE.get(ap.get("primary_archetype"), {}).items():
        out[a].append((f"archetype_preferences.primary_archetype={ap['primary_archetype']}", v))
    for s in ap.get("secondary_archetypes") or []:
        for a, v in ARCH_NUDGE.get(s, {}).items():
            out[a].append((f"archetype_preferences.secondary_archetypes={s}", v / 2))
    if (j.get("novelties") or "").strip():
        out["johnny"].append(("novelties (non-empty)", 2))
    text = " ".join([(j.get("deck_vision") or {}).get("description") or "", j.get("novelties") or "",
                     j.get("freeform_notes") or "", " ".join(ap.get("custom_themes") or []),
                     " ".join(wc.get("custom_win_conditions") or [])]).lower()
    for a, words in LEXICON.items():
        hits = sorted({w for w in words if re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", text)})
        if hits:
            out[a].append(("text: " + ", ".join(hits), min(LEXICON_CAP, len(hits))))
    return out


# ---------------------------------------------------------------------------
# leans_toward (spec section 8)
# ---------------------------------------------------------------------------


def leans_toward(scores: Mapping[str, int]) -> Tuple[str, Optional[str]]:
    rank = sorted(AXES, key=lambda a: (-scores[a], AXES.index(a)))
    s = [scores[a] for a in rank]
    if s[0] < 35 or s[0] - s[2] < 8:
        return "balanced", None
    return rank[0], (rank[1] if s[0] - s[1] <= 5 else None)


# ---------------------------------------------------------------------------
# The scorer
# ---------------------------------------------------------------------------


def score_deck_personality(
    deck: Mapping,
    journal: Any = None,
    *,
    cards: Union[Mapping[str, Mapping], Iterable[Mapping], None] = None,
    composition: Optional[Mapping] = None,
    combos: Any = None,
    graph: Optional[Mapping] = None,
    engines: Any = None,
    edhrec: Optional[Mapping] = None,
    power: Optional[float] = None,
    game_changers: Optional[Iterable[str]] = None,
) -> PersonalityScores:
    """Pure: deck + loaded inputs (+ optional journal) -> PersonalityScores.

    cards          inventory rows (`/api/inventory` shape) as a list or {name: row};
                   one row per name is picked deterministically.
    composition    compute_composition() output; computed from `cards` when None.
    combos         Spellbook in-deck variants (list, or a record with `included`/`in_deck`).
    graph          get_deck_graph() output (`counts`, `edges`).
    engines        get_deck_engines() output or its `engines` list.
    edhrec         deck_inclusion() output (`status`, `per_card`).
    power          power_level_estimate (1-10).
    game_changers  Game Changers names; default the checked-in BR-1 snapshot.
    journal        stored journal record, `/api/decks/{id}/journal` payload, or None.
    """
    deck = dict(deck or {})
    notes: List[str] = []
    rows = pick_card_rows(cards)
    names = _deck_names(deck)
    missing = [n for n in names if n not in rows]
    if len(missing) > MISSING_META_NOTE_OVER:
        notes.append(f"{len(missing)} cards without metadata")

    C = {n: _Card(rows[n]) for n in names if n in rows}
    cmd_names = {c for c in deck.get("commanders") or [] if c}  # graph/EDHREC need no metadata
    cmdrs = {c for c in cmd_names if c in C}
    NL = [n for n in C if not C[n].is_land]
    N = len(NL)
    NLC = [n for n in NL if n not in cmdrs]
    creatures = [n for n in NL if C[n].is_creature]
    legendary = [n for n in NL if "Legendary" in C[n].front]

    if composition is None:
        texts = {n: {"type_line": C[n].front + (" \u2014 " + " ".join(sorted(C[n].subs)) if C[n].subs else ""),
                     "oracle_text": C[n].raw, "mana_cost": ""} for n in C}
        composition = compute_composition(deck, texts)
    actual = composition.get("actual") or {}
    by_row = composition.get("cards_by_row") or {}
    ramp_cards = set(by_row.get("ramp") or [])
    inter = int(actual.get("targeted_disruption") or 0) + int(actual.get("mass_disruption") or 0)
    inter_cards = sorted(set(by_row.get("targeted_disruption") or []) | set(by_row.get("mass_disruption") or []))

    combo_list = _combo_list(combos)
    if combo_list is None:
        notes.append(NOTE_COMBOS)

    g_counts = (graph or {}).get("counts") or {}
    nodes = int(g_counts.get("nodes") or 0)
    graph_ok = bool(graph) and nodes > 0 and int(g_counts.get("untagged") or 0) <= STALE_UNTAGGED_SHARE * nodes
    eng_list = _engine_list(engines) if graph_ok else None
    if not graph_ok or eng_list is None:
        notes.append(NOTE_GRAPH)

    edh_ok = bool(edhrec) and edhrec.get("status") == "ok"
    if not edh_ok:
        notes.append(NOTE_EDHREC)
    if power is None:
        notes.append(NOTE_POWER)

    axes = {a: _Axis() for a in AXES}

    # ---------------- Timmy
    t = axes["timmy"]
    big = [n for n in creatures if C[n].mv >= 6]
    t.add("T1", "Big creatures", 30, ramp(len(big), 1, 8), len(big), big)
    t.add("T2", "Creature density", 15, ramp(len(creatures) / N, 0.20, 0.45) if N else 0.0,
          f"{len(creatures)}/{N}", creatures)
    t.add("T3", "Ramp", 15, ramp(int(actual.get("ramp") or 0), 5, 14), int(actual.get("ramp") or 0), ramp_cards)
    splashy = [n for n in NL if any(p.search(C[n].text) for p in SPLASHY)]
    t.add("T4", "Splashy effects", 25, ramp(len(splashy), 1, 8), len(splashy), splashy)
    t.add("T5", "Low interaction", 15, 1 - ramp(inter, 6, 16), inter, inter_cards,
          ["composition.actual.targeted_disruption", "composition.actual.mass_disruption"])

    # ---------------- Johnny
    j = axes["johnny"]
    if combo_list is not None:
        j.add("J1", "Combos in deck", 25, ramp(len(combo_list), 0, 4), len(combo_list),
              {n for c in combo_list for n in c.get("card_names") or []})
    else:
        j.add("J1", "Combos in deck", 25, 0.0, None, fields=[NOTE_COMBOS])
    if graph_ok:
        density = int(g_counts.get("edges") or 0) / nodes
        j.add("J2", "Synergy density", 25, ramp(density, 3, 12), round(density, 2),
              fields=[f"graph.counts.edges={g_counts.get('edges')}", f"graph.counts.nodes={nodes}"])
    else:
        j.add("J2", "Synergy density", 25, 0.0, None, fields=[NOTE_GRAPH])
    if edh_ok:
        inc = {x["name"]: x.get("inclusion") for x in edhrec.get("per_card") or [] if not x.get("land")}
        # EDHREC's own nonland, non-commander rows (spec J3): no inventory metadata needed
        pool = sorted(n for n in inc if n not in cmd_names)
        rare = [n for n in pool if (inc[n] or 0.0) < 0.15]
        j.add("J3", "Off-meta picks", 20, ramp(len(rare) / len(pool), 0.05, 0.30) if pool else 0.0,
              f"{len(rare)}/{len(pool)}", rare)
    else:
        j.add("J3", "Off-meta picks", 20, 0.0, None, fields=[NOTE_EDHREC])
    if graph_ok:
        nb = set()
        for e in graph.get("edges") or []:
            if e.get("source") in cmd_names:
                nb.add(e.get("target"))
            if e.get("target") in cmd_names:
                nb.add(e.get("source"))
        linked = [n for n in NLC if n in nb]
        frac = len(linked) / len(NLC) if NLC else 0.0
        j.add("J4", "Built around the commander", 15, ramp(frac, 0.15, 0.50) if NLC else 0.0, round(frac, 2),
              linked)
    else:
        j.add("J4", "Built around the commander", 15, 0.0, None, fields=[NOTE_GRAPH])
    if eng_list is not None:
        chain = {c for e in eng_list if e.get("kind") == "chain" for c in e.get("cards") or []}
        covered = [n for n in NL if n in chain]
        frac = len(covered) / N if N else 0.0
        j.add("J5", "Engine coverage", 15, ramp(frac, 0.60, 1.00) if N else 0.0, round(frac, 2), covered)
    else:
        j.add("J5", "Engine coverage", 15, 0.0, None, fields=[NOTE_GRAPH])

    # ---------------- Spike
    s = axes["spike"]
    if power is not None:
        s.add("S1", "Power estimate", 10, ramp(float(power), 5.0, 9.0), float(power),
              fields=["playfeel_summary.power_level_estimate"])
    else:
        s.add("S1", "Power estimate", 10, 0.0, None, fields=[NOTE_POWER])
    tutors = [n for n in NL if TUTOR.search(C[n].text)]
    s.add("S2", "Tutors", 15, ramp(len(tutors), 0, 5), len(tutors), tutors)
    fast = [n for n in NL if n in FAST_MANA or (n in ramp_cards and C[n].mv <= 1)]
    s.add("S3", "Fast mana", 15, ramp(len(fast), 0, 4), len(fast), fast)
    s.add("S4", "Interaction", 15, ramp(inter, 6, 16), inter, inter_cards)
    if NLC:
        avg = sum(C[n].mv for n in NLC) / len(NLC)
        s.add("S5", "Efficiency", 15, 1 - ramp(avg, 2.5, 4.0), round(avg, 2), fields=["mean mv of NL\\C"])
    else:
        s.add("S5", "Efficiency", 15, 0.0, None)
    if combo_list is not None:
        compact = [c for c in combo_list if len(c.get("card_names") or []) <= 3]
        s.add("S6", "Compact combos", 15, ramp(len(compact), 0, 3), len(compact),
              {n for c in compact for n in c.get("card_names") or []})
    else:
        s.add("S6", "Compact combos", 15, 0.0, None, fields=[NOTE_COMBOS])
    try:
        from .bracket_check import check_deck_brackets
        kw: Dict[str, Any] = {"oracle": {n: {"oracle_text": C[n].raw} for n in C},
                              "combos": {"included": combo_list} if combo_list is not None else None}
        if game_changers is not None:
            kw["game_changers"] = list(game_changers)
        gcs = list(check_deck_brackets(deck, **kw).game_changers.cards)
        s.add("S7", "Game Changers", 15, ramp(len(gcs), 0, 4), len(gcs), gcs)
    except Exception as exc:  # noqa: BLE001
        log.warning("bracket check failed: %s", exc)
        notes.append(NOTE_BRACKETS)
        s.add("S7", "Game Changers", 15, 0.0, None, fields=[NOTE_BRACKETS])

    # ---------------- Vorthos
    v = axes["vorthos"]
    sub_count: Dict[str, int] = {}
    for n in creatures:
        for st in C[n].subs:
            if st != "Human":
                sub_count[st] = sub_count.get(st, 0) + 1
    top_sub = max(sub_count.items(), key=lambda kv: (kv[1], kv[0])) if sub_count else (None, 0)
    set_pool = [n for n in NL if n not in ramp_cards]
    set_count: Dict[str, int] = {}
    for n in set_pool:
        if C[n].set:  # unknown printing is not a shared set; still counts in the denominator
            set_count[C[n].set] = set_count.get(C[n].set, 0) + 1
    top_set = max(set_count.items(), key=lambda kv: (kv[1], kv[0])) if set_count else (None, 0)
    type_sub = ramp(top_sub[1] / len(creatures), 0.25, 0.60) if creatures else 0.0
    set_sub = ramp(top_set[1] / len(set_pool), 0.30, 0.80) if set_pool else 0.0
    if set_sub > type_sub:
        theme_cards = [n for n in set_pool if C[n].set == top_set[0]]
    else:
        theme_cards = [n for n in creatures if top_sub[0] in C[n].subs]
    v.add("V1", "Shared theme", 35, max(type_sub, set_sub),
          f"type {top_sub[0]} {top_sub[1]}/{len(creatures)}; set {top_set[0]} {top_set[1]}/{len(set_pool)}",
          theme_cards)
    v.add("V2", "Named characters", 25, ramp(len(legendary) / N, 0.10, 0.40) if N else 0.0,
          f"{len(legendary)}/{N}", legendary)
    cmd_words = {st.lower() for c in cmdrs for st in C[c].subs if st != "Human"} | \
                {c.split(",")[0].lower() for c in cmdrs}
    word_res = [re.compile(r"\b" + re.escape(w) + r"s?\b") for w in sorted(cmd_words)]
    refs = [n for n in NLC if any(p.search(C[n].text) for p in word_res) or "doctor's companion" in C[n].kws]
    v.add("V3", "Commander-linked text", 15, ramp(len(refs), 2, 12), len(refs), refs,
          ["commander words: " + ", ".join(sorted(cmd_words))] if cmd_words else [])
    legend_short = {m: m.split(",")[0].split(" // ")[0] for m in legendary}
    all_kw = {k for n in C for k in C[n].kws}
    story, why = [], []
    for n in NL:
        reasons = []
        if C[n].subs & {"Saga", "Background"}:
            reasons.append("saga/background")
        if "partner with" in C[n].kws:
            reasons.append("partner with")
        fw = C[n].flavor_words()
        if fw:
            reasons.append("flavor word " + "/".join(fw))
        named = [m for m, sh in legend_short.items() if m != n and len(sh) > 3 and sh.lower() not in all_kw
                 and re.search(r"\b" + re.escape(sh.lower()) + r"\b", C[n].text)]
        if named:
            reasons.append("names " + "/".join(sorted(named)))
        if reasons:
            story.append(n)
            why.append(f"{n}: {'; '.join(reasons)}")
    v.add("V4", "Story links", 25, ramp(len(story), 1, 8), len(story), story, sorted(why))

    # ---------------- Melvin
    m = axes["melvin"]
    mech: Dict[str, set] = {}
    for n in NL:
        fw = {k.lower() for k in C[n].flavor_words()}
        for k in C[n].kws:
            if k in EVERGREEN or k in ABILITY_WORDS or k in fw:
                continue
            mech.setdefault(k, set()).add(n)
    m.add("M1", "Mechanic diversity", 25, ramp(len(mech), 3, 12), len(mech),
          {n for ns in mech.values() for n in ns}, ["keyword=" + k for k in sorted(mech)])
    rules = [n for n in NL if any(p.search(C[n].text) for p in RULES) or set(C[n].kws) & RULES_KEYWORDS]
    m.add("M2", "Rules-object manipulation", 30, ramp(len(rules) / N, 0.05, 0.30) if N else 0.0,
          f"{len(rules)}/{N}", rules)
    if eng_list is not None:
        loops = [e for e in eng_list if e.get("kind") == "loop"]
        m.add("M3", "Loops", 10, ramp(len(loops), 0, 4), len(loops),
              {c for e in loops for c in e.get("cards") or []},
              sorted(str(e.get("signature")) for e in loops))
    else:
        m.add("M3", "Loops", 10, 0.0, None, fields=[NOTE_GRAPH])
    cantrips = [n for n in NL if C[n].mv <= 2 and CANTRIP.search(C[n].text)]
    m.add("M4", "Cantrips and filtering", 15, ramp(len(cantrips), 2, 10), len(cantrips), cantrips)
    odd = [n for n in NL if (C[n].subs & ODD_SUBTYPES) or "Planeswalker" in C[n].front or "Battle" in C[n].front
           or any(p.search(C[n].text) for p in ODD_TEXT)]
    m.add("M5", "Odd types and rules", 20, ramp(len(odd), 1, 8), len(odd), odd)

    # ---------------- Journal nudges
    jr = _resolve_journal(journal)
    nudges = journal_nudges(jr) if jr is not None else {a: [] for a in AXES}
    if jr is None:
        notes.append(NOTE_NO_JOURNAL)

    out: Dict[str, AxisScore] = {}
    for a in AXES:
        ax = axes[a]
        card_score = ax.total
        if jr is None:
            ax.evidence.append(AxisEvidence(signal="journal", label="Deck Journal", value="no journal"))
            nudge = 0.0
        elif not nudges[a]:
            ax.evidence.append(AxisEvidence(signal="journal", label="Deck Journal", value="no nudge"))
            nudge = 0.0
        else:
            for field, pts in nudges[a]:
                ax.evidence.append(AxisEvidence(signal="journal", label="Deck Journal", value=field,
                                                points=float(pts), fields=[field]))
            nudge = max(-NUDGE_CAP, min(NUDGE_CAP, sum(p for _, p in nudges[a])))
        final = max(0.0, min(100.0, card_score + nudge))
        out[a] = AxisScore(score=_round(final), card_score=round(card_score, 4), journal_nudge=float(nudge),
                           evidence=ax.evidence)

    lean, runner = leans_toward({a: out[a].score for a in AXES})
    return PersonalityScores(**out, leans_toward=lean, runner_up=runner, journal_used=jr is not None,
                             notes=notes)


# ---------------------------------------------------------------------------
# I/O wrapper: Redis only, never a network call
# ---------------------------------------------------------------------------


class _RefusePost:
    def __call__(self, *a, **kw):
        raise RuntimeError("cache-only: no Spellbook fetch")


class _RefuseEDHRec:
    def get_commander_page(self, *a, **kw):
        from .providers.edhrec import EDHRecError
        raise EDHRecError("cache-only: no EDHREC fetch")


def enrich_type_lines(r, rows: List[Mapping]) -> List[dict]:
    """Inventory rows keep only the primary card type (an artifact creature
    reads "Artifact"). Add the complete type line from the tagger's cached
    Scryfall copy (commander_roles.load_cards, Redis-only) where one exists;
    printing fields (set, instance) stay from the row."""
    import commander_roles

    full = commander_roles.load_cards(r, [row.get("name") for row in rows])
    out = []
    for row in rows:
        rec = full.get(row.get("name")) or {}
        if rec.get("type_line") and not rec.get("type_partial"):
            row = {**row, "type_line": rec["type_line"]}
        out.append(dict(row))
    return out


def load_inputs(r, deck: Mapping) -> Dict[str, Any]:
    """Gather score_deck_personality inputs from Redis. Each failed source is None."""
    deck = dict(deck)
    out: Dict[str, Any] = {k: None for k in ("cards", "composition", "combos", "graph", "engines", "edhrec",
                                             "power", "game_changers", "journal")}
    ref = deck.get("registry_id") or deck.get("id") or deck.get("name")

    def attempt(key, fn):
        try:
            out[key] = fn()
        except Exception as exc:  # noqa: BLE001 - a missing source just scores 0
            log.warning("personality input %s failed for %s: %s", key, ref, exc)

    import inventory_view
    from . import insights
    from .interactions import combos as combos_mod, service

    attempt("cards", lambda: inventory_view.build_inventory(r, decks=[str(ref)]))
    if out["cards"]:
        attempt("cards", lambda: enrich_type_lines(r, out["cards"]))
    attempt("composition", lambda: compute_composition(deck, insights.card_texts(r, deck)))

    def _combos():
        # find_my_combos with a client that can't fetch: cache hit (fresh or stale) or SpellbookError
        client = combos_mod.SpellbookClient(r, http_post=_RefusePost())
        rec = client.find_my_combos([c for c in deck.get("commanders") or [] if c], combos_mod._deck_names(deck))
        return list(rec.get("included") or [])
    attempt("combos", _combos)
    attempt("graph", lambda: service.get_deck_graph(r, deck))
    if out["graph"] is not None:
        attempt("engines", lambda: service.get_deck_engines(r, deck, out["graph"])["engines"])

    def _edhrec():
        from . import edhrec_inclusion
        return edhrec_inclusion.deck_inclusion(r, deck, client=_RefuseEDHRec(), card_texts=insights.card_texts(r, deck))
    attempt("edhrec", _edhrec)

    def _power():
        from deck_review import review_deck
        return review_deck(deck=dict(deck)).playfeel_summary.power_level_estimate
    attempt("power", _power)

    def _gc():
        from . import brackets
        cached = brackets._read_cache(r)
        return list(cached.cards) if cached else None
    attempt("game_changers", _gc)

    def _journal():
        from . import journal as journal_mod
        return journal_mod.load_journal(r, deck)
    attempt("journal", _journal)
    return out


def score_deck(r, deck: Mapping) -> PersonalityScores:
    """Score one deck from Redis (cache-only). Never raises on a missing source."""
    inputs = load_inputs(r, deck)
    journal = inputs.pop("journal")
    return score_deck_personality(deck, journal, **inputs)

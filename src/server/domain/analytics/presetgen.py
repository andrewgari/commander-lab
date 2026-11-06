"""Category ratios and preset generation (presetgen-v1, extends composition-v1 §8).

Builds on ``analytics.composition_ai`` (packet, validator E/W/A1-A8, op application) and
``analytics.composition_presets`` (built-ins, auto-select). This module adds:

    reliability(K, n, L)        exact hypergeometric "see one by turn N" (§2)
    land_math(...)              Burgess + Karsten + MDFC, clamped to the A7 window (§3)
    baseline(packet)            deterministic per-deck preset; the AI-off Generate result (§4)
    improve_candidates(packet)  ranked, issue-driven repair ops + info-only deck gaps (§5)
    generate(packet, chain)     Baseline, or an AI draft anchored to it (§6); never raises on AI failure
    improve(packet, chain)      candidates applied (AI-off: top 6) or chosen/reworded by the AI (§6)
    validate_*                  A9-A12 on top of composition_ai's A1-A8 (§7)

Everything except the two LLM calls is pure: same input, byte-identical output. Nothing here
saves, pins a deck, touches the Journal, names a card, or reads flags (A8, §4.2).
"""
from __future__ import annotations

import copy
import json
import math
from fractions import Fraction
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

from src.server.domain.analytics import composition_ai as ca
from src.server.domain.analytics import composition_presets as cp

SPEC_VERSION = "presetgen-v1"

# Internal structural skeleton used by baseline() when nothing is
# auto-selected (no pin, no archetype/theme/MV signal, no user preset
# matches). This is not a user-facing named preset -- just the four core
# refs Baseline always evaluates -- so there is no silent "default preset"
# substitution; auto_select's "no preset selected" result is honoured and
# this skeleton only supplies the shape to generate slots from scratch.
_BASELINE_SKELETON: Dict[str, Any] = {
    "id": None,
    "name": None,
    "slots": [
        {"ref": "core:ramp", "target": 10, "min": None, "max": None, "why": None, "pillar_id": None},
        {"ref": "core:card_advantage", "target": 12, "min": None, "max": None, "why": None, "pillar_id": None},
        {"ref": "group:targeted_interaction", "target": 12, "min": None, "max": None, "why": None, "pillar_id": None},
        {"ref": "core:board_wipe", "target": 6, "min": None, "max": None, "why": None, "pillar_id": None},
    ],
}

# ---------------------------------------------------------------------------------------------
# Config (spec §8, `composition.generate`). Tests may patch values in this dict.
# ---------------------------------------------------------------------------------------------

CONFIG: Dict[str, Any] = {
    "version": SPEC_VERSION,
    "plan_room_target": 20,
    "plan_room_min": ca.PLAN_ROOM_MIN,
    "baseline_slack": 4,
    "max_ops": ca.MAX_OPS,
    "engine_min": 8,
    "coverage_min": 3,
    "cmd_job_target_delta": -2,
    "big_commander_mv": 5,
    "big_commander_ramp": 12,
    "mdfc_land_weight": {"nonmythic": 0.38, "mythic": 0.74},
    "trim_order": ["core:board_wipe", "core:card_advantage", "group:targeted_interaction",
                   "core:removal", "core:counterspell", "core:ramp"],
    # (turn, p*) behind each autotag floor (documentation + test P7; floors stay in autotag config)
    "reliability": {
        "core:ramp": {"turn": 2, "p": 0.60},
        "core:ramp@big_commander": {"turn": 2, "p": 0.70},
        "core:card_advantage": {"turn": 4, "p": 0.55},
        "group:targeted_interaction": {"turn": 4, "p": 0.70},
        "core:board_wipe": {"turn": 7, "p": 0.25},
        "core:finisher": {"turn": 8, "p": 0.25},
        "display_turns": {"core:mana_fixing": 3, "core:removal": 4, "core:counterspell": 4,
                          "group:interaction": 4, "theme": 4, "type": 4, "cat": 4, "core:protection": 5,
                          "core:recursion": 6, "core:tutor": 6, "facet": 6},
    },
}

PRIORITY = {"R1": "P0", "R2": "P0", "R3": "P1", "R4": "P1", "R5": "P1", "R6": "P2", "R7": "P3",
            "R8": "P3", "R9": "P4", "R10": "P4"}
TRIGGER_ORDER = list(PRIORITY)
A12_EXEMPT_TRIGGERS = ("R3", "R5", "R6", "R7")

COVERAGE_REF = "facet:answers_artifact_enchantment"
GENERIC_TAGS = ("commander", "casual", "competitive")

WHY_TEMPLATES = {
    "BASE": "Kept from {base_name}.",
    "RAMP_MV": "Commander costs {mv}; ramp floor is 12, giving {p}% to see one by turn 2.",
    "CMD_JOB": "The commander already does this job, so 2 fewer are needed.",
    "TOKENS_WIPE": "Tokens deck: wipes hit your own board, so cap them at 1.",
    "NO_BLUE": "No blue in the colour identity.",
    "ENGINE": "Main theme {theme_label}: 8 pieces give {p}% to see one by turn 4.",
    "COVERAGE": "Only {count} answers to artifacts or enchantments; 3 is the minimum.",
    "FLOOR": "Raised to the floor of {floor} ({floor_reason}).",
    "TRIM": "Trimmed to keep {R} cards for the deck's own plan.",
    "THEME_OFF": "Theme {theme_label} is not active in this deck.",
    "LANDS": "{lands_math}",
}

SYSTEM_PROMPT = """You draft Commander deck-composition presets for Commander Lab. A preset is a shape, not a list:
it sets counts for categories such as ramp, card draw, interaction, wipes and themes. It never names cards.

The deck's spirit outranks raw power. The Journal thesis and pillars say what the deck is.
Keep room for them: plan room (spells minus the function-slot minimums) must stay at least 15.

You get a deterministic BASELINE and, when improving, a ranked list of CANDIDATES.
- Start from the baseline. Move a baseline slot by more than 4 only when a Journal pillar or the
  commander's own text demands it, and put that pillar's id in pillar_id.
- When improving, prefer candidates and cite candidate_id. An op that isn't a candidate needs a pillar_id.
- Never lower a target just because the deck has fewer cards than the target today. That is a deck gap.
- Never set a slot below its floor unless a pillar or the commander does that job.
- Never add or raise core:tutor. Never target flags (stax, land destruction, extra turns, Game Changers).
- Keep lands inside limits.lands_window.
- Each why is one sentence of at most 160 characters. Give the deck reason or the number.
  Never name a card, and never use popularity, price, "staple", "best", "meta" or EDHREC as a reason.
- Use only refs from the catalog. Return JSON only, in the schema given. No prose outside the JSON."""

_SLOT_SCHEMA = ca._SLOT_SCHEMA

GENERATE_SCHEMA = {"type": "OBJECT", "properties": {
    "preset": {"type": "OBJECT", "properties": {
        "name": {"type": "STRING"}, "description": {"type": "STRING"},
        "structure": {"type": "OBJECT", "properties": {"lands": {"type": "INTEGER"}}, "required": ["lands"]},
        "slots": {"type": "ARRAY", "items": _SLOT_SCHEMA}}, "required": ["name", "structure", "slots"]},
    "lands_math": {"type": "STRING"}, "summary": {"type": "STRING"}},
    "required": ["preset", "lands_math", "summary"]}

IMPROVE_SCHEMA = {"type": "OBJECT", "properties": {
    "ops": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "candidate_id": {"type": "STRING", "nullable": True}, "op": {"type": "STRING"},
        "ref": {"type": "STRING", "nullable": True}, "from": {"type": "OBJECT", "nullable": True},
        "to": {"type": "OBJECT", "nullable": True}, "why": {"type": "STRING"},
        "pillar_id": {"type": "STRING", "nullable": True}}, "required": ["op", "why"]}},
    "summary": {"type": "STRING"}}, "required": ["ops", "summary"]}


class PresetGenError(ValueError):
    """Bad input (422) or an internal invariant broken (a Baseline with an E-code is a bug)."""

    def __init__(self, message: str, issues: Optional[list] = None):
        super().__init__(message)
        self.issues = issues or []


# ---------------------------------------------------------------------------------------------
# §2 Reliability
# ---------------------------------------------------------------------------------------------

def reliability(k: int, n: int, library: int = 99) -> float:
    """P(at least one of ``k`` copies in the top ``n`` of a ``library``-card library). Exact.

    p = 1 - C(L-k, n) / C(L, n). Returns 0.0 for k <= 0 and 1.0 when k >= L - n + 1.
    """
    if k <= 0 or n <= 0:
        return 0.0
    if k >= library - n + 1:
        return 1.0
    miss = Fraction(math.comb(library - k, n), math.comb(library, n))
    return float(1 - miss)


def reliability_turn(ref: str) -> Optional[int]:
    rel = CONFIG["reliability"]
    if ref in rel and isinstance(rel[ref], Mapping):
        return rel[ref]["turn"]
    disp = rel["display_turns"]
    if ref in disp:
        return disp[ref]
    return disp.get(ref.split(":", 1)[0])


def reliability_entries(preset: Mapping, library: int) -> List[dict]:
    """One entry per slot with a lower bound > 0 (§2.1). Max-only slots get none."""
    out = []
    for s in preset.get("slots") or []:
        ref = ca.normalize_ref(s.get("ref"))
        k = ca.lower(s) or 0
        turn = reliability_turn(ref)
        if k <= 0 or turn is None:
            continue
        n = 7 + turn
        out.append({"ref": ref, "turn": turn, "cards_seen": n, "k": k,
                    "p": round(reliability(k, n, library), 3), "library": library})
    return out


# ---------------------------------------------------------------------------------------------
# §3 Land math
# ---------------------------------------------------------------------------------------------

def round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def mdfc_equivalents(deck: Mapping, texts: Mapping[str, Mapping]) -> float:
    """Land-equivalents of spell-front cards with a land back face (Karsten: 0.38, mythic 0.74)."""
    w = CONFIG["mdfc_land_weight"]
    cmdr = {n for n in deck.get("commanders") or []}
    total = 0.0
    for name, qty in ca._qty(deck).items():
        card = texts.get(name) or {}
        tl = str(card.get("type_line") or "")
        if name in cmdr or "//" not in tl:
            continue
        front, back = (p.lower() for p in tl.split("//", 1))
        if "land" in back and "land" not in front:
            total += qty * (w["mythic"] if str(card.get("rarity") or "").lower() == "mythic" else w["nonmythic"])
    return round(total, 2)


def land_math(ci_size: int, commander_mv: float, avg_mv_nonland: float, cheap_ramp_draw: int,
              mdfc_eq: float = 0.0) -> dict:
    """Burgess, Karsten, the MDFC adjustment, the A7 window and the clamped land count."""
    burgess = 31 + ci_size + commander_mv
    karsten = 31.42 + 3.13 * avg_mv_nonland - 0.28 * cheap_ramp_draw
    mean = (burgess + karsten) / 2
    raw = mean - mdfc_eq
    lo = min(burgess, karsten) - ca.LANDS_SLACK
    hi = max(burgess, karsten) + ca.LANDS_SLACK
    lands = min(max(round_half_up(round(raw, 9)), math.ceil(round(lo, 9))), math.floor(round(hi, 9)))
    mdfc_txt = f" − {mdfc_eq:.2f} MDFC" if mdfc_eq else ""
    text = (f"Burgess 31+{ci_size}+{commander_mv:g} = {burgess:g}; "
            f"Karsten 31.42+3.13·{avg_mv_nonland:g}−0.28·{cheap_ramp_draw} = {karsten:.1f}; "
            f"mean {mean:.1f}{mdfc_txt} → {lands}")
    return {"burgess": burgess, "karsten": karsten, "mdfc_eq": mdfc_eq, "mean": mean, "raw": raw,
            "window": [lo, hi], "lands": int(lands), "text": text}


# ---------------------------------------------------------------------------------------------
# Packet enrichment + facts
# ---------------------------------------------------------------------------------------------

def build_packet(deck: Mapping, texts: Mapping[str, Mapping], card_tags: Mapping[str, list],
                 deck_tags: Iterable[str] = (), ident: Optional[Mapping] = None,
                 current_preset: Optional[Mapping] = None, global_custom_ids: Iterable[str] = (),
                 existing_preset_names: Iterable[str] = (), user_presets: Iterable[Mapping] = (),
                 facet_counts: Optional[Mapping[str, int]] = None) -> dict:
    """composition_ai.build_packet plus the presetgen inputs (§6.1 sources).

    ``facet_counts`` is the deck's count per ``facet:*`` ref when a facet classifier exists; a
    missing ref counts as 0, so coverage (M6/R8) fires until it is supplied.
    """
    packet = ca.build_packet(deck, texts, card_tags, deck_tags=deck_tags, ident=ident,
                             current_preset=current_preset, global_custom_ids=global_custom_ids,
                             existing_preset_names=existing_preset_names)
    cmd_tags = set()
    for n in deck.get("commanders") or []:
        cmd_tags |= ca._tagset(card_tags.get(n))
    packet["commander_tags"] = sorted(cmd_tags)
    packet["mdfc_eq"] = mdfc_equivalents(deck, texts)
    packet["facet_counts"] = dict(facet_counts or {})
    packet["user_presets"] = [dict(p) for p in user_presets or []]
    return packet


class Facts:
    """The deck facts presetgen reads, taken from an (enriched) packet. Pure accessors."""

    def __init__(self, packet: Mapping):
        self.packet = packet
        self.commanders = int(packet.get("commanders_count") or 1)
        self.library = 100 - self.commanders
        cmd = packet.get("commander") or {}
        self.commander_mv = cmd.get("mv") or 0
        self.ci = [c.upper() for c in cmd.get("color_identity") or []]
        self.has_blue = "U" in self.ci
        tags = [str(t).lower() for t in (packet.get("autotag") or {}).get("deck_tags") or [] if t]
        self.deck_tags = tags
        self.tokens_active = "tokens" in tags
        self.themes = {c["ref"].split(":", 1)[1] for c in packet.get("catalog") or []
                       if str(c.get("ref", "")).startswith("theme:")}
        self.top_theme = next((t for t in tags if t in self.themes and t not in GENERIC_TAGS), None)
        self.floors: Dict[str, int] = dict(packet.get("floors") or ca.BASE_FLOORS)
        self.commander_tags = set(packet.get("commander_tags") or [])
        ev = packet.get("evaluation") or {}
        self.counts: Dict[str, int] = dict(ev.get("counts") or {})
        self.facet_counts: Dict[str, int] = dict(packet.get("facet_counts") or {})
        self.journal = packet.get("journal") or {}
        self.pillar_ids = {p.get("id") for p in self.journal.get("pillars") or [] if p.get("id")}
        self.user_presets = list(packet.get("user_presets") or [])
        self.deck_name = str(packet.get("deck_name") or "deck")
        self.registry_id = str(packet.get("registry_id") or "")
        self.land = land_math(len(self.ci), self.commander_mv, float(packet.get("avg_mv_nonland") or 0.0),
                              int(packet.get("cheap_ramp_draw") or 0), float(packet.get("mdfc_eq") or 0.0))

    def count(self, ref: str) -> Optional[int]:
        if ref.startswith("facet:"):
            return self.facet_counts.get(ref, 0)
        return self.counts.get(ref)

    def commander_does(self, ref: str) -> bool:
        kind, _, ident = ref.partition(":")
        if kind == "core":
            return ident in self.commander_tags
        if ref == "group:targeted_interaction":
            return bool(self.commander_tags & {"removal", "counterspell"})
        if ref == "group:interaction":
            return bool(self.commander_tags & {"removal", "counterspell", "board_wipe"})
        return False

    def theme_label(self, theme: str) -> str:
        if theme.startswith("kindred:"):
            return "Kindred: " + theme.split(":", 1)[1].replace("_", " ").title()
        return theme.replace("_", " ").title()


# ---------------------------------------------------------------------------------------------
# Slot helpers
# ---------------------------------------------------------------------------------------------

def bounds(slot: Mapping) -> dict:
    return {k: slot[k] for k in ("target", "min", "max") if slot.get(k) is not None}


def _mk(ref: str, b: Mapping, why: Optional[str] = None) -> dict:
    return {"ref": ref, "target": b.get("target"), "min": b.get("min"), "max": b.get("max"),
            "why": why, "pillar_id": None}


def _find(preset: Mapping, ref: str) -> Optional[dict]:
    return next((s for s in preset.get("slots") or [] if ca.normalize_ref(s.get("ref")) == ref), None)


def _lo(slot: Optional[Mapping]) -> int:
    return (ca.lower(slot) or 0) if slot else 0


def _hi(slot: Mapping) -> Optional[int]:
    return ca.upper(slot)


def _shift_lower(slot: dict, delta: int) -> None:
    """Move the lower bound by ``delta``: decrement ``min`` if set, else ``target`` (E5 stays valid)."""
    key = "min" if slot.get("min") is not None else "target"
    if slot.get(key) is None:
        return
    slot[key] = max(0, slot[key] + delta)


def _raise_lower(slot: dict, value: int) -> None:
    if _lo(slot) >= value and slot.get("min") is None and slot.get("target") is None:
        return
    if slot.get("min") is not None:
        slot["min"] = max(slot["min"], value)
    elif slot.get("target") is not None:
        slot["target"] = max(slot["target"], value)
    else:  # max-only
        slot["min"] = value
    for k in ("target", "max"):
        if slot.get(k) is not None and slot[k] < value:
            slot[k] = value


def _raise_to_floor(slot: dict, floor: int) -> None:
    """Every bound below the floor becomes the floor (R2: target-only → target; min → min and target; max → max)."""
    for k in ("target", "min", "max"):
        if slot.get(k) is not None and slot[k] < floor:
            slot[k] = floor


def _valid_bounds(b: Mapping) -> bool:
    t, lo, hi = b.get("target"), b.get("min"), b.get("max")
    if lo is not None and hi is not None and lo > hi:
        return False
    if t is not None and lo is not None and t < lo:
        return False
    if t is not None and hi is not None and t > hi:
        return False
    return True


# ---------------------------------------------------------------------------------------------
# §4.4 Trim (shared by Baseline and R6)
# ---------------------------------------------------------------------------------------------

def _excess(preset: Mapping, slot: Mapping, facts: Facts) -> int:
    ref = ca.normalize_ref(slot.get("ref"))
    floors = facts.floors
    if ref in ("core:removal", "core:counterspell"):
        if _find(preset, "group:targeted_interaction") is not None:
            return 0
        rem, cs = _lo(_find(preset, "core:removal")), _lo(_find(preset, "core:counterspell"))
        return max(0, rem + cs - floors.get("group:targeted_interaction", 0))
    return max(0, _lo(slot) - floors.get(ref, 0))


def trim(preset: dict, facts: Facts, goal: int) -> Tuple[List[str], int]:
    """Decrement slots (largest excess over the modified floor first; ties by trim order) until
    plan room >= goal. Returns (refs touched in order, final plan room). Pillar-locked slots stay."""
    order = CONFIG["trim_order"]
    touched: List[str] = []
    while ca.plan_room(preset) < goal:
        best = None
        for idx, ref in enumerate(order):
            s = _find(preset, ref)
            if s is None or s.get("pillar_id"):
                continue
            ex = _excess(preset, s, facts)
            if ex > 0 and (best is None or ex > best[0]):
                best = (ex, idx, s)
        if best is None:
            break
        _shift_lower(best[2], -1)
        touched.append(ca.normalize_ref(best[2]["ref"]))
    return touched, ca.plan_room(preset)


# ---------------------------------------------------------------------------------------------
# §4 Baseline
# ---------------------------------------------------------------------------------------------

def _why(code: str, data: Mapping) -> str:
    return WHY_TEMPLATES[code].format(**data)


def _floor_reason(ref: str, facts: Facts) -> str:
    if ref == "core:ramp" and facts.commander_mv >= CONFIG["big_commander_mv"]:
        return f"commander MV {int(facts.commander_mv)}"
    if facts.commander_does(ref):
        return "the commander does this job"
    return "autotag health floor"


def baseline(packet: Mapping) -> dict:
    """§4. Deterministic: B0 auto-select (pin ignored), B1 lands, B2 M1-M6, B3 floors, B4 trim."""
    facts = Facts(packet)
    cfg = CONFIG
    sel = cp.auto_select(facts.journal, facts.deck_tags, facts.commander_mv, facts.user_presets, skip_pin=True)
    base = sel["preset"] or _BASELINE_SKELETON
    base_name = base.get("name") or base.get("id") or "Baseline"
    slots = [dict(s, why=None, pillar_id=None) for s in copy.deepcopy(base["slots"])]
    preset = {"structure": {"deck_size": 100, "commanders": facts.commanders, "lands": facts.land["lands"]},
              "slots": slots}
    reasons: Dict[str, Tuple[str, dict]] = {}
    notes: List[dict] = []

    def tag(ref: str, code: str, **data):
        reasons[ref] = (code, data)

    # theme slots for inactive themes can never be counted (W4/R4): drop them, like R4 would.
    for s in list(slots):
        ref = ca.normalize_ref(s["ref"])
        if ref.startswith("theme:") and ref.split(":", 1)[1] not in facts.themes:
            slots.remove(s)

    # M1 big commander ramp
    if facts.commander_mv >= cfg["big_commander_mv"]:
        s = _find(preset, "core:ramp")
        want = cfg["big_commander_ramp"]
        p = reliability(want, 9, facts.library)
        if s is None:
            slots.append(_mk("core:ramp", {"target": want}))
            tag("core:ramp", "RAMP_MV", mv=int(facts.commander_mv), p=f"{p * 100:.1f}")
        elif _lo(s) < want:
            _raise_lower(s, want)
            tag("core:ramp", "RAMP_MV", mv=int(facts.commander_mv), p=f"{p * 100:.1f}")
    # M2 the commander does this job
    for s in slots:
        ref = ca.normalize_ref(s["ref"])
        if ref.startswith(("core:", "group:")) and facts.commander_does(ref) and _lo(s) > 0:
            new = max(_lo(s) + cfg["cmd_job_target_delta"], facts.floors.get(ref, 0))
            if new < _lo(s):
                _shift_lower(s, new - _lo(s))
                tag(ref, "CMD_JOB")
    # M3 tokens: cap wipes at 1
    if facts.tokens_active:
        s = _find(preset, "core:board_wipe")
        if s is not None and (s.get("max") != 1 or s.get("target") is not None or s.get("min") is not None):
            s.update(target=None, min=None, max=1)
            tag("core:board_wipe", "TOKENS_WIPE")
    # M4 no blue: no counterspells / stack answers
    if not facts.has_blue:
        for ref in ("core:counterspell", "facet:answers_stack"):
            s = _find(preset, ref)
            if s is not None:
                slots.remove(s)
                reasons.pop(ref, None)
    # M5 engine
    if facts.top_theme and _find(preset, f"theme:{facts.top_theme}") is None:
        ref = f"theme:{facts.top_theme}"
        slots.append(_mk(ref, {"min": cfg["engine_min"]}))
        tag(ref, "ENGINE", theme_label=facts.theme_label(facts.top_theme),
            p=f"{reliability(cfg['engine_min'], 11, facts.library) * 100:.1f}")
    # M6 coverage
    count = facts.count(COVERAGE_REF) or 0
    if count < cfg["coverage_min"] and _find(preset, COVERAGE_REF) is None:
        slots.append(_mk(COVERAGE_REF, {"min": cfg["coverage_min"]}))
        tag(COVERAGE_REF, "COVERAGE", count=count)
    # B3 floors
    for ref, floor in facts.floors.items():
        s = _find(preset, ref)
        if s is not None and _hi(s) is not None and _hi(s) < floor:
            _raise_to_floor(s, floor)
            tag(ref, "FLOOR", floor=floor, floor_reason=_floor_reason(ref, facts))
    # B4 trim
    before = {ca.normalize_ref(s["ref"]): _lo(s) for s in slots}
    touched, room = trim(preset, facts, cfg["plan_room_target"])
    for ref in dict.fromkeys(touched):
        tag(ref, "TRIM", R=room)
    if room < cfg["plan_room_target"]:
        notes.append({"code": "FIT_BLOCKED", "data": {"R": room}})
    # B5/B6
    name = f"Baseline for {facts.deck_name}"[:ca.NAME_MAX].rstrip()
    for s in slots:
        ref = ca.normalize_ref(s["ref"])
        code, data = reasons.get(ref, ("BASE", {}))
        s["why"] = _why(code, {"base_name": base_name, **data})
        s["pillar_id"] = None
    preset.update({
        "id": None, "schema": ca.SPEC_VERSION, "kind": "user", "name": name,
        "description": "Deterministic baseline from the commander, curve and active themes.",
        "source": f"baseline:{facts.registry_id}", "confidence": "heuristic", "based_on": base["id"],
        "auto_select": None, "version": 1})
    _check_baseline(preset, packet, facts)
    entries = reliability_entries(preset, facts.library)
    return {"preset": preset, "lands_math": facts.land["text"], "reliability": entries,
            "reasons": {r: c for r, (c, _d) in reasons.items()}, "notes": notes,
            "selected": {"preset_id": sel["preset_id"], "reason": sel["reason"]},
            "plan_room": room, "spec_version": SPEC_VERSION}


def _check_baseline(preset: Mapping, packet: Mapping, facts: Facts) -> None:
    """B7: any E-code, W3 or A7 failure on a Baseline is a bug, not user input."""
    ctx = ca.Context(packet)
    ctx.existing_names = set()  # a name clash is a save-time concern, not a baseline defect
    issues = ca.validate_preset(preset, ctx, ai=False, scope="deck")
    bad = [i for i in issues if i["level"] == "error" or i["code"] == "W3"]
    bad += ca._lands_rule(preset["structure"]["lands"], ctx)
    if bad:
        raise PresetGenError("baseline failed validation: " + "; ".join(i["code"] for i in bad), bad)


# ---------------------------------------------------------------------------------------------
# §5 Improve candidates
# ---------------------------------------------------------------------------------------------

class _Candidates:
    """Working copy + one op per ref (merged when the result stays valid, else skipped)."""

    def __init__(self, preset: Mapping):
        self.work = copy.deepcopy(dict(preset))
        self.work.setdefault("slots", [])
        self.items: Dict[str, dict] = {}
        self.order = 0

    def _put(self, trig: str, ref: str, op: str, frm, to, code: str, data: Mapping, facts: Facts):
        self.order += 1
        self.items[ref] = {"trig": trig, "priority": PRIORITY[trig], "op": op, "ref": ref, "from": frm,
                           "to": to, "why": _why(code, {"base_name": "", **data})}

    def lands(self, trig: str, to: int, facts: Facts):
        cur = (self.work.get("structure") or {}).get("lands")
        if cur == to or "structure:lands" in self.items:
            return
        self.work["structure"] = {**(self.work.get("structure") or {}), "lands": to}
        self._put(trig, "structure:lands", "lands", cur, to, "LANDS", {"lands_math": facts.land["text"]}, facts)

    def set_bounds(self, trig: str, ref: str, new: Mapping, code: str, data: Mapping, facts: Facts):
        slot = _find(self.work, ref)
        new = {k: v for k, v in new.items() if v is not None}
        if slot is None or bounds(slot) == new or not _valid_bounds(new):
            return
        ex = self.items.get(ref)
        if ex is None:
            self._put(trig, ref, "set", bounds(slot), new, code, data, facts)
        else:  # one op per ref: merge into the earlier op (the new bounds already passed E5)
            if ex["op"] != "set":
                return
            ex["to"] = new
            if ex["to"] == ex["from"]:
                del self.items[ref]
        slot.update(target=new.get("target"), min=new.get("min"), max=new.get("max"))

    def remove(self, trig: str, ref: str, code: str, data: Mapping, facts: Facts):
        slot = _find(self.work, ref)
        if slot is None or ref in self.items:
            return
        self._put(trig, ref, "remove", bounds(slot), None, code, data, facts)
        self.work["slots"].remove(slot)

    def add(self, trig: str, ref: str, to: Mapping, code: str, data: Mapping, facts: Facts):
        if _find(self.work, ref) is not None or ref in self.items:
            return
        self._put(trig, ref, "add", None, dict(to), code, data, facts)
        self.work["slots"].append(_mk(ref, to))


def improve_candidates(packet: Mapping, preset: Optional[Mapping] = None) -> dict:
    """§5. Ranked repair candidates for ``preset`` (default: ``packet['current_preset']``) plus deck gaps."""
    preset = preset if preset is not None else packet.get("current_preset")
    if not isinstance(preset, Mapping) or not (preset.get("slots") or preset.get("structure")):
        raise PresetGenError("improve needs a current preset; use generate first")
    facts = Facts(packet)
    cfg = CONFIG
    c = _Candidates(preset)
    lands_now = (c.work.get("structure") or {}).get("lands")
    lo, hi = facts.land["window"]
    base_lands = facts.land["lands"]

    # R1 lands outside the A7 window
    if isinstance(lands_now, int) and not (lo <= lands_now <= hi):
        c.lands("R1", base_lands, facts)
    # R2 floored slot whose upper bound is under its modified floor
    for ref, floor in facts.floors.items():
        s = _find(c.work, ref)
        if s is not None and _hi(s) is not None and _hi(s) < floor:
            new = dict(s)
            _raise_to_floor(new, floor)
            c.set_bounds("R2", ref, bounds(new), "FLOOR",
                         {"floor": floor, "floor_reason": _floor_reason(ref, facts)}, facts)
    # R3 tokens: wipes capped at 1
    if facts.tokens_active:
        s = _find(c.work, "core:board_wipe")
        if s is not None and _lo(s) > 0:
            c.set_bounds("R3", "core:board_wipe", {"max": 1}, "TOKENS_WIPE", {}, facts)
    # R4 inactive theme slots
    for s in list(c.work["slots"]):
        ref = ca.normalize_ref(s.get("ref"))
        if ref.startswith("theme:") and ref.split(":", 1)[1] not in facts.themes:
            c.remove("R4", ref, "THEME_OFF", {"theme_label": facts.theme_label(ref.split(":", 1)[1])}, facts)
    # R5 no blue
    if not facts.has_blue:
        for ref in ("core:counterspell", "facet:answers_stack"):
            s = _find(c.work, ref)
            if s is not None and _lo(s) > 0:
                c.remove("R5", ref, "NO_BLUE", {}, facts)
    # R6 plan room below the minimum: trim to the minimum only
    if isinstance(lands_now, int) and ca.plan_room(c.work) < cfg["plan_room_min"]:
        trial = copy.deepcopy(c.work)
        before = {ca.normalize_ref(s["ref"]): bounds(s) for s in trial["slots"]}
        _touched, room = trim(trial, facts, cfg["plan_room_min"])
        for s in sorted(trial["slots"], key=lambda x: ca.normalize_ref(x["ref"])):
            ref = ca.normalize_ref(s["ref"])
            if bounds(s) != before[ref]:
                c.set_bounds("R6", ref, bounds(s), "TRIM", {"R": room}, facts)
    # R7 the commander does this job
    for s in list(c.work["slots"]):
        ref = ca.normalize_ref(s.get("ref"))
        if (ref.startswith(("core:", "group:")) and facts.commander_does(ref) and not s.get("pillar_id")
                and _lo(s) >= facts.floors.get(ref, 0) + 2):
            new = dict(s)
            _shift_lower(new, cfg["cmd_job_target_delta"])
            c.set_bounds("R7", ref, bounds(new), "CMD_JOB", {}, facts)
    # R8 coverage
    count = facts.count(COVERAGE_REF) or 0
    if count < cfg["coverage_min"]:
        c.add("R8", COVERAGE_REF, {"min": cfg["coverage_min"]}, "COVERAGE", {"count": count}, facts)
    # R9 engine
    if facts.top_theme:
        ref = f"theme:{facts.top_theme}"
        c.add("R9", ref, {"min": cfg["engine_min"]}, "ENGINE",
              {"theme_label": facts.theme_label(facts.top_theme),
               "p": f"{reliability(cfg['engine_min'], 11, facts.library) * 100:.1f}"}, facts)
    # R10 lands inside the window but far from Baseline
    if isinstance(lands_now, int) and lo <= lands_now <= hi and abs(lands_now - base_lands) >= 2:
        c.lands("R10", base_lands, facts)

    items = sorted(c.items.values(), key=lambda i: (i["priority"], TRIGGER_ORDER.index(i["trig"]), i["ref"]))
    candidates = []
    for n, it in enumerate(items, 1):
        rec = {"id": f"c{n}", "trig": it["trig"], "priority": it["priority"], "op": it["op"], "ref": it["ref"],
               "from": it["from"], "to": it["to"], "why": it["why"]}
        candidates.append(rec)
    notes = []
    if ca.plan_room(c.work) < cfg["plan_room_min"] and isinstance(lands_now, int):
        notes.append({"code": "FIT_BLOCKED", "data": {"R": ca.plan_room(c.work)}})
    return {"candidates": candidates, "deck_gaps": deck_gaps(c.work, facts), "notes": notes,
            "lands_math": facts.land["text"]}


def deck_gaps(preset: Mapping, packet_or_facts) -> List[dict]:
    """§5.3: rows whose count is `low` (under the modified floor) or `short` (under the band).
    Information only; improve never lowers a target to hide a gap."""
    facts = packet_or_facts if isinstance(packet_or_facts, Facts) else Facts(packet_or_facts)
    out = []
    for s in preset.get("slots") or []:
        ref = ca.normalize_ref(s.get("ref"))
        count = facts.count(ref)
        lower = ca.lower(s)
        if count is None or lower is None or not ref.startswith(("core:", "group:", "type:", "facet:", "theme:")):
            continue
        band_lo = s["min"] if s.get("min") is not None else max(0, s["target"] - ca.BAND)
        floor = facts.floors.get(ref, 0)
        if count >= max(band_lo, floor):
            continue
        status = "low" if floor and count < floor else "short"
        turn = reliability_turn(ref)
        entry = {"ref": ref, "status": status, "count": count, "lower": lower,
                 "need": max(band_lo, floor) - count}
        if turn is not None:
            n = 7 + turn
            entry.update(turn=turn, p_now=round(reliability(count, n, facts.library), 3),
                         p_at_lower=round(reliability(lower, n, facts.library), 3))
        out.append(entry)
    return sorted(out, key=lambda e: e["ref"])


# ---------------------------------------------------------------------------------------------
# §7 Validator rules A9-A12
# ---------------------------------------------------------------------------------------------

def _real_pillar(pid: Any, packet_or_ctx) -> Optional[str]:
    ids = packet_or_ctx.pillar_ids if hasattr(packet_or_ctx, "pillar_ids") else set()
    return pid if pid and pid in ids else None


def _is_function(ref: str) -> bool:
    return ref.startswith(("core:", "group:"))


def check_a9(draft: Mapping, base: Mapping, ctx: ca.Context) -> List[dict]:
    """Generate: every core/group slot stays within ±baseline_slack of Baseline unless a real pillar justifies it."""
    slack = CONFIG["baseline_slack"]
    out = []
    drafted = {ca.normalize_ref(s.get("ref")): s for s in draft.get("slots") or [] if isinstance(s, Mapping)}
    based = {ca.normalize_ref(s["ref"]): s for s in base.get("slots") or [] if _is_function(ca.normalize_ref(s["ref"]))}
    for ref, b in based.items():
        d = drafted.get(ref)
        if d is not None and _real_pillar(d.get("pillar_id"), ctx):
            continue
        d_lo, d_hi = (ca.lower(d) or 0, ca.upper(d)) if d is not None else (0, 0)
        b_lo, b_hi = _lo(b), ca.upper(b)
        if abs(d_lo - b_lo) > slack or (d_hi is not None and b_hi is not None and abs(d_hi - b_hi) > slack):
            out.append(ca._issue("A9", "error", ref, f"{ref} is more than {slack} from the baseline without a pillar.",
                                 baseline=bounds(b), draft=bounds(d) if d else None))
    for ref, d in drafted.items():
        if _is_function(ref) and ref not in based and not _real_pillar(d.get("pillar_id"), ctx):
            out.append(ca._issue("A9", "error", ref, f"{ref} is not in the baseline and needs a pillar_id."))
    return out


def check_a11_preset(draft: Mapping, current: Optional[Mapping]) -> List[dict]:
    s = _find(draft, "core:tutor")
    cur = _lo(_find(current or {}, "core:tutor"))
    if s is not None and _lo(s) > cur:
        return [ca._issue("A11", "error", "core:tutor", "Never add or raise tutors.")]
    return []


def _same_op(op: Mapping, cand: Mapping) -> bool:
    if op.get("op") != cand["op"]:
        return False
    if cand["op"] == "lands":
        return op.get("to") == cand["to"]
    if ca.normalize_ref(op.get("ref")) != cand["ref"]:
        return False
    if cand["op"] == "remove":
        return True
    return bounds(op.get("to") or {}) == bounds(cand["to"] or {})


def apply_op(preset: Mapping, op: Mapping) -> Tuple[Optional[dict], Optional[str]]:
    """composition_ai.apply_op, but ``to`` of a `set` is the slot's COMPLETE new bounds (omitted = null)."""
    if op.get("op") == "set" and isinstance(op.get("to"), Mapping):
        to = ca._clean_bounds(op["to"]) or {}
        op = {**op, "to": {k: to.get(k) for k in ("target", "min", "max")}}
    return ca.apply_op(preset, op)


def _codes(issues: Iterable[Mapping]) -> List[str]:
    return sorted({i["code"] for i in issues})


def validate_ops(preset: Mapping, ops: Iterable[Any], packet: Mapping,
                 candidates: Iterable[Mapping] = ()) -> dict:
    """Apply AI ops one by one (A1-A12). A failing op is dropped with its codes; the set is re-checked after."""
    ctx = ca.Context(packet)
    facts = Facts(packet)
    cands = {c["id"]: c for c in candidates}
    max_ops = CONFIG["max_ops"]
    working = copy.deepcopy(dict(preset))
    accepted: List[dict] = []
    dropped: List[dict] = []
    for i, raw in enumerate(ops):
        if not isinstance(raw, Mapping):
            dropped.append({"op_index": i, "op": raw, "codes": [], "reason": "not an object"})
            continue
        if len(accepted) >= max_ops:
            dropped.append({"op_index": i, "op": dict(raw), "codes": ["A4"], "reason": f"A4: at most {max_ops} ops"})
            continue
        pid = _real_pillar(raw.get("pillar_id"), ctx)
        op = {**raw, "pillar_id": pid}
        cand = cands.get(op.get("candidate_id")) if op.get("candidate_id") else None
        matched = bool(cand and _same_op(op, cand))
        issues: List[dict] = []
        if not matched and not pid:
            issues.append(ca._issue("A10", "error", ca.normalize_ref(op.get("ref")) or None,
                                    "Op must match a candidate or cite a real pillar_id."))
        new, why_not = apply_op(working, op)
        if new is None:
            dropped.append({"op_index": i, "op": dict(raw), "codes": _codes(issues), "reason": why_not})
            continue
        before = {ca._issue_key(x) for x in ca.error_issues(ca.validate_preset(working, ctx, ai=True))}
        after = ca.error_issues(ca.validate_preset(new, ctx, ai=True))
        # Candidate-matched ops may only be valid as a set (e.g. R2 then R6 for plan room): W3 is judged at the end.
        issues += [x for x in after if ca._issue_key(x) not in before and not (matched and x["code"] == "W3")]
        issues += ca._op_rule_issues(op, working, new, ctx)
        ref = ca.normalize_ref(op.get("ref"))
        if op.get("op") in ("add", "set") and ref == "core:tutor":
            issues += check_a11_preset(new, working)
        if op.get("op") in ("set", "remove") and ref != "structure:lands":
            old_slot, new_slot = _find(working, ref), _find(new, ref)
            old_lo, new_lo = _lo(old_slot), _lo(new_slot)
            count = facts.count(ref)
            exempt = matched and cand["trig"] in A12_EXEMPT_TRIGGERS
            if new_lo < old_lo and count is not None and count < old_lo and not exempt and not pid:
                issues.append(ca._issue("A12", "error", ref, "Do not lower a target to match the deck's current count.",
                                        count=count, lower=old_lo))
        if issues:
            dropped.append({"op_index": i, "op": dict(raw), "codes": _codes(issues),
                            "reason": "; ".join(x["message"] for x in issues), "issues": issues})
            continue
        rec = {k: op.get(k) for k in ("op", "ref", "from", "to", "why", "pillar_id") if k in op}
        if op.get("candidate_id") and matched:
            rec["candidate_id"] = op["candidate_id"]
        if op.get("op") in ("set", "remove"):
            rec["from"] = bounds(_find(working, ref) or {})
        elif op.get("op") == "lands":
            rec["from"] = (working.get("structure") or {}).get("lands")
        working = new
        accepted.append(rec)
    return {"preset": working, "ops": accepted, "dropped": dropped}


def apply_candidates(preset: Mapping, candidates: Iterable[Mapping], limit: Optional[int] = None) -> dict:
    """AI-off Improve: the first ``limit`` (default 6) candidates, applied structurally in rank order."""
    limit = CONFIG["max_ops"] if limit is None else limit
    working = copy.deepcopy(dict(preset))
    applied: List[dict] = []
    for cand in list(candidates)[:limit]:
        op = {k: cand.get(k) for k in ("op", "ref", "from", "to", "why")}
        new, why_not = apply_op(working, op)
        if new is None:
            continue
        working = new
        applied.append({**op, "candidate_id": cand["id"], "pillar_id": None})
    return {"preset": working, "ops": applied}


# ---------------------------------------------------------------------------------------------
# §6 AI calls
# ---------------------------------------------------------------------------------------------

def _limits(facts: Facts) -> dict:
    lo, hi = facts.land["window"]
    return {"max_ops": CONFIG["max_ops"], "plan_room_min": CONFIG["plan_room_min"],
            "baseline_slack": CONFIG["baseline_slack"], "why_max": ca.WHY_MAX,
            "lands_window": [round(lo, 3), round(hi, 3)]}


def prompt_packet(packet: Mapping, mode: str, base: Mapping, candidates: List[dict],
                  gaps: List[dict], reliab: List[dict]) -> dict:
    """§6.1: the packet minus card names (except the commanders') and internal validation keys."""
    facts = Facts(packet)
    drop = {"deck_card_names", "existing_preset_names", "registry_id", "deck_name", "user_presets",
            "commander_tags", "mdfc_eq", "facet_counts", "commander_does"}
    out = {k: v for k, v in packet.items() if k not in drop}
    out.update(mode=mode,
               baseline={"structure": base["preset"]["structure"],
                         "slots": [{k: s.get(k) for k in ("ref", "target", "min", "max")}
                                   for s in base["preset"]["slots"]], "lands_math": base["lands_math"]},
               candidates=candidates, reliability=reliab, deck_gaps=gaps,
               floors=dict(facts.floors), limits=_limits(facts))
    return out


def _ask(chain, packet_json: dict, schema: dict, purpose: str, on_call=None):
    """Single turn, no tools, JSON only. Returns (data, provider, model) or raises CompositionAIUnavailable."""
    if chain is None or not chain.available():
        raise ca.CompositionAIUnavailable("no_llm", "no LLM provider is configured")
    prompt = ("PACKET:\n" + json.dumps(packet_json, ensure_ascii=False, default=str)
              + "\n\nSCHEMA:\n" + json.dumps(schema))
    try:
        data, provider, model = chain.generate_json(SYSTEM_PROMPT, prompt, schema=schema, purpose=purpose,
                                                    on_call=on_call)
    except Exception as exc:  # any provider failure falls back to the deterministic result
        raise ca.CompositionAIUnavailable("llm_error", f"{type(exc).__name__}: {exc}"[:200])
    if data is None:
        raise ca.CompositionAIUnavailable("llm_failed", "every LLM provider failed")
    if not isinstance(data, Mapping):
        raise ca.CompositionAIUnavailable("bad_response", "the LLM did not return a JSON object")
    return data, provider, model


def _draft_meta() -> dict:
    return ca._draft_meta()


def _summary(text: Any) -> str:
    return " ".join(str(text or "").split())[:ca.SUMMARY_MAX]


def generate(packet: Mapping, chain=None, on_call: Optional[Callable] = None) -> dict:
    """Baseline, or an AI draft anchored to it. Never raises on an AI failure: it falls back to Baseline
    with ``ai.status`` = "off" (no LLM) or "fallback" (+ reason, issues)."""
    base = baseline(packet)
    facts = Facts(packet)
    result = {"mode": "generate", "preset": base["preset"], "baseline": base, "lands_math": base["lands_math"],
              "reliability": base["reliability"], "summary": "", **_draft_meta()}
    if chain is None or not chain.available():
        result["ai"] = {"status": "off", "reason": "no_llm"}
        return result
    try:
        data, provider, model = _ask(chain, prompt_packet(packet, "generate", base, [], [], base["reliability"]),
                                     GENERATE_SCHEMA, "presetgen_generate", on_call)
    except ca.CompositionAIUnavailable as exc:
        result["ai"] = {"status": "fallback", "reason": exc.kind}
        return result
    raw = data.get("preset") if isinstance(data.get("preset"), Mapping) else data
    ctx = ca.Context(packet)
    preset = ca.make_preset(raw, ctx, f"ai:{model}", based_on=base["preset"]["based_on"])
    issues: List[dict] = []
    if not preset["slots"] or not isinstance(preset["structure"].get("lands"), int):
        issues.append(ca._issue("E4", "error", None, "The draft has no slots or no lands."))
    else:
        issues += ca.validate_preset(preset, ctx, ai=True)
        issues += check_a9(preset, base["preset"], ctx)
        issues += check_a11_preset(preset, None)
    errors = ca.error_issues(issues)
    if errors:
        result["ai"] = {"status": "fallback", "reason": "invalid_draft", "codes": _codes(errors), "issues": errors}
        return result
    preset["issues"] = issues
    result.update(preset=preset, lands_math=" ".join(str(data.get("lands_math") or base["lands_math"]).split())[:300],
                  reliability=reliability_entries(preset, facts.library), summary=_summary(data.get("summary")))
    result["ai"] = {"status": "ok", "model": model, "provider": provider}
    return result


def improve(packet: Mapping, chain=None, on_call: Optional[Callable] = None,
            preset: Optional[Mapping] = None) -> dict:
    """Repair the current preset. AI-off: apply the top 6 candidates. With an LLM: it chooses and rewords
    candidates (or adds pillar-justified ops); invalid ops are dropped; failure falls back to the top 6."""
    current = preset if preset is not None else packet.get("current_preset")
    found = improve_candidates(packet, current)
    cands, gaps = found["candidates"], found["deck_gaps"]
    facts = Facts(packet)
    out = {"mode": "improve", "candidates": cands, "deck_gaps": gaps, "notes": found["notes"], **_draft_meta()}

    def finish(res: dict, ai: dict, summary: str) -> dict:
        p = res["preset"]
        if res["ops"]:
            p["version"] = int(p.get("version") or 0) + 1
        ctx = ca.Context(packet)
        ctx.existing_names = set()
        p["issues"] = ca.validate_preset(p, ctx, ai=False, scope="deck")
        out.update(preset=p, ops=res["ops"], dropped=res.get("dropped", []), summary=summary, ai=ai,
                   reliability=reliability_entries(p, facts.library))
        return out

    if not cands:
        return finish({"preset": copy.deepcopy(dict(current)), "ops": []},
                      {"status": "skipped", "reason": "no_candidates"}, "No preset issues found.")

    def deterministic(ai: dict) -> dict:
        res = apply_candidates(current, cands)
        return finish(res, ai, f"Applied the top {len(res['ops'])} of {len(cands)} preset fixes.")

    if chain is None or not chain.available():
        return deterministic({"status": "off", "reason": "no_llm"})
    base = baseline(packet)
    try:
        data, provider, model = _ask(chain, prompt_packet(packet, "improve", base, cands, gaps, base["reliability"]),
                                     IMPROVE_SCHEMA, "presetgen_improve", on_call)
    except ca.CompositionAIUnavailable as exc:
        return deterministic({"status": "fallback", "reason": exc.kind})
    ops = data.get("ops")
    if not isinstance(ops, list):
        return deterministic({"status": "fallback", "reason": "bad_response"})
    res = validate_ops(current, ops, packet, cands)
    ctx = ca.Context(packet)
    ctx.existing_names = set()
    new_errors = {ca._issue_key(i) for i in ca.error_issues(ca.validate_preset(res["preset"], ctx, ai=True))} \
        - {ca._issue_key(i) for i in ca.error_issues(ca.validate_preset(current, ctx, ai=True))}
    if new_errors:
        return deterministic({"status": "fallback", "reason": "set_invalid"})
    if ops and not res["ops"]:
        return deterministic({"status": "fallback", "reason": "all_ops_rejected",
                              "dropped": [{"op_index": d["op_index"], "codes": d["codes"]} for d in res["dropped"]]})
    ai = {"status": "ok", "model": model, "provider": provider,
          "dropped": [{"op_index": d["op_index"], "codes": d["codes"]} for d in res["dropped"]]}
    return finish(res, ai, _summary(data.get("summary")))

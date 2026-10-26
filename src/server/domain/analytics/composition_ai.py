"""AI-assisted composition preset generation and improvement (composition-v1 §8).

The LLM only DRAFTS. Every draft is checked by a deterministic validator (E-rules, W1-W3 and
the AI rules A1-A8) before the caller sees it, and nothing here saves anything: drafts are
never stored, never pin a deck and never touch the Journal (A8).

    build_packet(...)        deck + tags + journal -> the §8.2 input packet
    mirror_preset(packet)    deterministic draft from the deck's current counts (§8.1, no AI)
    generate_preset(...)     LLM draft of a whole preset; falls back to Mirror when no LLM
    improve_preset(...)      LLM ops against the current preset; invalid ops are dropped
    validate_preset(...)     E1-E8, W1-W3, A2/A3/A5/A6/A7 as `issues`

Presets name no cards (principle 3), so nothing the AI returns can cut a card.
"""
from __future__ import annotations

import copy
import hashlib
import re
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Tuple

SPEC_VERSION = "composition-v1"

NAME_MAX, DESC_MAX, WHY_MAX = 40, 400, 160
MAX_SLOTS, MAX_OPS, SUMMARY_MAX = 24, 6, 280
PLAN_ROOM_MIN = 15
LANDS_WARN_LO, LANDS_WARN_HI = 33, 42
LANDS_SLACK = 2
BAND = 1

CORE_REFS = ("ramp", "mana_fixing", "card_advantage", "tutor", "recursion", "removal",
             "counterspell", "board_wipe", "protection", "finisher")
GROUP_REFS = ("targeted_interaction", "interaction")
TYPE_REFS = ("creature", "artifact", "enchantment", "instant", "sorcery", "planeswalker", "battle",
             "instant_sorcery")
FACET_REFS = ("answers_creature", "answers_artifact", "answers_enchantment", "answers_artifact_enchantment",
              "answers_planeswalker", "flexible", "answers_stack", "instant_speed")
FLAG_NAMES = ("game_changer", "stax", "mass_land_denial", "extra_turns")
ZONE_NAMES = ("sideboard", "maybeboard", "considering")

LABELS = {
    "ramp": "Ramp", "mana_fixing": "Fixing", "card_advantage": "Card Draw", "tutor": "Tutors",
    "recursion": "Recursion", "removal": "Removal", "counterspell": "Counterspells",
    "board_wipe": "Board Wipes", "protection": "Protection", "finisher": "Win Cons",
    "targeted_interaction": "Targeted interaction", "interaction": "All interaction",
}

# Health floors (C6). Keys are slot refs; targeted interaction floors the group slot.
BASE_FLOORS = {"core:ramp": 10, "core:card_advantage": 7, "group:targeted_interaction": 10,
               "core:board_wipe": 2, "core:finisher": 2}

# subset -> supersets (E7)
SUPERSETS: Dict[str, Tuple[str, ...]] = {
    "core:removal": ("group:targeted_interaction", "group:interaction"),
    "core:counterspell": ("group:targeted_interaction", "group:interaction"),
    "group:targeted_interaction": ("group:interaction",),
    "core:board_wipe": ("group:interaction",),
    "type:instant": ("type:instant_sorcery",),
    "type:sorcery": ("type:instant_sorcery",),
    **{f"facet:{f}": ("group:interaction",) for f in FACET_REFS},
}
# Slots counted in plan room: function (core + group) slots only (W3).
_MEMBERS_OF = {"group:targeted_interaction": ("core:removal", "core:counterspell"),
               "group:interaction": ("core:removal", "core:counterspell", "core:board_wipe")}

BANNED_WHY = re.compile(r"\b(edhrec|staple|best|auto-?include|meta|salt|price)\b|\$", re.I)

ISSUE_LEVELS = ("error", "warning", "info")


class CompositionAIError(ValueError):
    """Bad input (422 territory)."""


class CompositionAIUnavailable(RuntimeError):
    """No LLM, every provider failed, or the answer was unusable (503/502). ``kind`` says which."""

    def __init__(self, kind: str, detail: str = "", issues: Optional[list] = None):
        super().__init__(detail or kind)
        self.kind = kind
        self.issues = issues or []


def _issue(code: str, level: str, ref: Optional[str], message: str, **data) -> dict:
    return {"code": code, "level": level, "ref": ref, "message": message, "data": data}


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------------------------
# Slots
# ---------------------------------------------------------------------------------------------

def normalize_ref(ref: Any) -> str:
    ref = str(ref or "").strip().lower() if not str(ref or "").startswith("cat:") else str(ref).strip()
    return "structure:lands" if ref == "core:lands" else ref


def lower(slot: Mapping) -> Optional[int]:
    v = slot.get("min")
    return v if v is not None else slot.get("target")


def upper(slot: Mapping) -> Optional[int]:
    v = slot.get("max")
    return v if v is not None else slot.get("target")


def build_catalog(themes: Iterable[str] = (), custom_ids: Iterable[str] = ()) -> List[dict]:
    """The refs an AI draft may use (A2). Themes are the deck's active themes; customs are global ones."""
    cat = [{"ref": f"core:{c}", "label": LABELS[c]} for c in CORE_REFS]
    cat += [{"ref": f"group:{g}", "label": LABELS[g]} for g in GROUP_REFS]
    cat += [{"ref": f"type:{t}", "label": t.replace("_", "/").title()} for t in TYPE_REFS]
    cat += [{"ref": f"facet:{f}", "label": f.replace("_", " ").capitalize()} for f in FACET_REFS]
    cat += [{"ref": f"theme:{t}", "label": f"Theme: {t}"} for t in sorted({str(t).lower() for t in themes if t})]
    cat += [{"ref": f"cat:{c}", "label": f"Custom: {c}"} for c in sorted(set(custom_ids)) if str(c).startswith("g-")]
    return cat


def static_ref_error(ref: str, valid_themes: Optional[Iterable[str]] = None) -> Optional[str]:
    """E3 for the closed vocabularies. Themes and cat:* need the catalog, so they pass here."""
    if not ref or ":" not in ref:
        return f"'{ref}' is not a category ref (<kind>:<id>)."
    kind, ident = ref.split(":", 1)
    if ref.lower() in FLAG_NAMES or ident.lower() in FLAG_NAMES:
        return "Flags are shown, never targeted."
    if ident.lower() in ZONE_NAMES:
        return "Zones are never targeted."
    if ref == "derived:plan":
        return "Plan is what's left."
    if kind == "structure":
        return "Lands live in structure.lands; structure refs are not slots."
    if kind == "core":
        return None if ident in CORE_REFS else f"Unknown core category '{ident}'."
    if kind == "group":
        return None if ident in GROUP_REFS else f"Unknown group '{ident}'."
    if kind == "type":
        return None if ident in TYPE_REFS else f"Unknown card type '{ident}'."
    if kind == "facet":
        return None if ident in FACET_REFS else f"Unknown facet '{ident}'."
    if kind == "theme":
        if valid_themes is not None and ident not in set(valid_themes):
            return "Unknown theme. Valid: " + ", ".join(sorted(valid_themes)) if valid_themes else "No themes are active."
        return None
    if kind == "cat":
        if ident.startswith("d-"):
            return "Deck categories live in deck overrides."
        return None if ident.startswith("g-") else f"Unknown custom category '{ident}'."
    return f"Unknown ref kind '{kind}'."


# ---------------------------------------------------------------------------------------------
# Context + validation
# ---------------------------------------------------------------------------------------------

class Context:
    """Everything the validator needs, taken from the §8.2 packet."""

    def __init__(self, packet: Mapping):
        self.packet = packet
        self.commanders_count = int(packet.get("commanders_count") or 1)
        self.catalog_refs = {normalize_ref(c.get("ref")) for c in packet.get("catalog") or []}
        self.themes = {r.split(":", 1)[1] for r in self.catalog_refs if r.startswith("theme:")}
        self.pillar_ids = {p.get("id") for p in (packet.get("journal") or {}).get("pillars") or [] if p.get("id")}
        self.floors: Dict[str, int] = dict(packet.get("floors") or BASE_FLOORS)
        self.commander_does: Mapping[str, bool] = packet.get("commander_does") or {}
        lm = packet.get("land_math") or {}
        self.burgess, self.karsten = lm.get("burgess"), lm.get("karsten")
        cmd = packet.get("commander") or {}
        self.protected = [n for n in (packet.get("deck_card_names") or []) + list(cmd.get("names") or []) if n]
        self.existing_names = {str(n).lower() for n in packet.get("existing_preset_names") or []}

    @property
    def nonland_for(self):
        return lambda lands: 100 - self.commanders_count - lands

    def land_range(self) -> Optional[Tuple[float, float]]:
        vals = [v for v in (self.burgess, self.karsten) if isinstance(v, (int, float))]
        if not vals:
            return None
        return min(vals) - LANDS_SLACK, max(vals) + LANDS_SLACK


def plan_room(preset: Mapping) -> int:
    """nonland minus the lower bounds of function slots (W3). Group + members count max(group, sum members)."""
    st = preset.get("structure") or {}
    nonland = 100 - int(st.get("commanders") or 1) - int(st.get("lands") or 0)
    low = {normalize_ref(s.get("ref")): (lower(s) or 0) for s in preset.get("slots") or [] if isinstance(s, Mapping)}
    function = {r: v for r, v in low.items() if r.startswith(("core:", "group:"))}
    total = 0
    counted: set = set()
    for g in ("group:interaction", "group:targeted_interaction"):
        if g in function:
            members = [m for m in _MEMBERS_OF[g] if m in function and m not in counted]
            total += max(function[g], sum(function[m] for m in members))
            counted.add(g)
            counted.update(_MEMBERS_OF[g])
    # targeted_interaction is a subset of interaction: if both exist its lower is inside the wider one.
    total_rest = sum(v for r, v in function.items() if r not in counted)
    return nonland - (total + total_rest)


def validate_preset(preset: Any, ctx: Optional[Context] = None, *, ai: bool = False,
                    scope: str = "preset") -> List[dict]:
    """E1-E8 (+ W1-W3). With ``ai`` and a context, adds A2, A3 (W3 as error), A5, A6, A7 (and A1 = all E)."""
    issues: List[dict] = []
    if not isinstance(preset, Mapping):
        return [_issue("E1", "error", None, "A preset must be a JSON object.")]
    name = " ".join(str(preset.get("name") or "").split())
    if not 1 <= len(name) <= NAME_MAX:
        issues.append(_issue("E1", "error", None, f"Name must be 1-{NAME_MAX} characters."))
    elif ctx and name.lower() in ctx.existing_names:
        issues.append(_issue("E1", "error", None, "Another preset already uses that name."))
    if len(str(preset.get("description") or "")) > DESC_MAX:
        issues.append(_issue("E1", "error", None, f"Description is at most {DESC_MAX} characters."))

    st = preset.get("structure")
    st = st if isinstance(st, Mapping) else {}
    cmdrs, lands = st.get("commanders"), st.get("lands")
    if st.get("deck_size") != 100 or isinstance(st.get("deck_size"), bool):
        issues.append(_issue("E2", "error", None, "deck_size must be 100."))
    if not _is_int(cmdrs) or cmdrs not in (1, 2):
        issues.append(_issue("E2", "error", None, "commanders must be 1 or 2."))
    lands_ok = _is_int(lands) and _is_int(cmdrs) and 0 <= lands <= 100 - cmdrs
    if not lands_ok:
        issues.append(_issue("E2", "error", None, "lands must be an integer between 0 and 100 - commanders."))

    slots = preset.get("slots")
    if not isinstance(slots, list):
        issues.append(_issue("E4", "error", None, "slots must be a list."))
        slots = []
    if len(slots) > MAX_SLOTS:
        issues.append(_issue("E8", "error", None, f"At most {MAX_SLOTS} slots."))

    seen: set = set()
    good: Dict[str, Mapping] = {}
    for s in slots:
        if not isinstance(s, Mapping):
            issues.append(_issue("E4", "error", None, "Every slot must be an object."))
            continue
        raw = s.get("ref")
        ref = normalize_ref(raw)
        err = static_ref_error(ref, ctx.themes if ctx and ctx.themes is not None and ai else None)
        if ref == "structure:lands":
            err = "Lands live in structure.lands; structure refs are not slots."
        if err:
            issues.append(_issue("E3", "error", ref or None, err))
            continue
        if ref in seen:
            issues.append(_issue("E4", "error", ref, "Duplicate ref."))
            continue
        seen.add(ref)
        vals = {k: s.get(k) for k in ("target", "min", "max")}
        bad = [k for k, v in vals.items() if v is not None and (not _is_int(v) or not 0 <= v <= 99)]
        if bad:
            issues.append(_issue("E5", "error", ref, f"{', '.join(bad)} must be an integer 0-99 or null."))
            continue
        if all(v is None for v in vals.values()):
            issues.append(_issue("E5", "error", ref, "Set at least one of target, min, max."))
            continue
        t, lo, hi = vals["target"], vals["min"], vals["max"]
        if (lo is not None and hi is not None and lo > hi) or (t is not None and lo is not None and t < lo) \
                or (t is not None and hi is not None and t > hi):
            issues.append(_issue("E5", "error", ref, "Need min <= target <= max."))
            continue
        why = s.get("why")
        if why is not None and (not isinstance(why, str) or len(why) > WHY_MAX):
            issues.append(_issue("E1", "error", ref, f"why is at most {WHY_MAX} characters."))
        good[ref] = s

    if lands_ok:
        avail = 100 - cmdrs - lands
        for ref, s in good.items():
            lo = lower(s)
            if lo is not None and lo > avail:
                issues.append(_issue("E6", "error", ref, f"Needs {lo} but only {avail} spells are available.",
                                     lower=lo, available=avail))
    for ref, s in good.items():
        for sup in SUPERSETS.get(ref, ()):
            if sup in good and lower(s) is not None and upper(good[sup]) is not None \
                    and lower(s) > upper(good[sup]):
                issues.append(_issue("E7", "error", ref,
                                     f"{ref} needs at least {lower(s)} but {sup} allows at most {upper(good[sup])}.",
                                     superset=sup))

    # -- warnings -----------------------------------------------------------------------------
    if lands_ok:
        if lands < LANDS_WARN_LO or lands > LANDS_WARN_HI:
            issues.append(_issue("W1", "warning", None, f"{lands} lands is outside {LANDS_WARN_LO}-{LANDS_WARN_HI}."))
        if ctx and scope == "deck":
            rng = ctx.land_range()
            if rng and lands < rng[0] + LANDS_SLACK - LANDS_SLACK and lands < min(
                    v for v in (ctx.burgess, ctx.karsten) if v is not None) - LANDS_SLACK:
                issues.append(_issue("W1", "warning", None, "Lands are below both land formulas.",
                                     burgess=ctx.burgess, karsten=ctx.karsten))
    floors = ctx.floors if ctx else BASE_FLOORS
    for ref, floor in floors.items():
        s = good.get(ref)
        if s is not None and upper(s) is not None and upper(s) < floor:
            issues.append(_issue("W2", "warning", ref, f"{ref} tops out at {upper(s)}, below the health floor {floor}.",
                                 floor=floor))
    if lands_ok:
        room = plan_room({"structure": st, "slots": list(good.values())})
        if room < PLAN_ROOM_MIN:
            issues.append(_issue("W3", "error" if ai else "warning", None,
                                 f"Plan room is {room}; keep at least {PLAN_ROOM_MIN} spells for the thesis.",
                                 plan_room=room))

    # -- AI rules ------------------------------------------------------------------------------
    if ai and ctx:
        issues += _ai_rules(preset, good, ctx, lands if lands_ok else None)
    return issues


def _why_problem(why: Any, ctx: Context) -> Optional[str]:
    if not isinstance(why, str) or not why.strip():
        return "a short reason (why) is required"
    if len(why) > WHY_MAX:
        return f"why is at most {WHY_MAX} characters"
    if BANNED_WHY.search(why):
        return "why must not cite EDHREC, staples, price or meta"
    for n in ctx.protected:
        if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", why, re.I):
            return f"why must not name a card ({n})"
    return None


def _ai_rules(preset: Mapping, good: Mapping[str, Mapping], ctx: Context, lands: Optional[int]) -> List[dict]:
    out: List[dict] = []
    for ref, s in good.items():
        if ctx.catalog_refs and ref not in ctx.catalog_refs:
            out.append(_issue("A2", "error", ref, "Ref is not in the catalog for this deck."))
        prob = _why_problem(s.get("why"), ctx)
        if prob:
            out.append(_issue("A6", "error", ref, prob.capitalize() + "."))
        floor = ctx.floors.get(ref)
        lo = lower(s)
        if floor is not None and lo is not None and lo < floor and not _floor_excused(ref, s, ctx):
            out.append(_issue("A5", "error", ref, f"Below the health floor {floor} needs a pillar_id or a commander "
                              "that does this job.", floor=floor))
    out += _floor_removals(good, ctx)
    out += _lands_rule(lands, ctx)
    return out


def _floor_excused(ref: str, slot: Mapping, ctx: Context) -> bool:
    return bool(slot.get("pillar_id") in ctx.pillar_ids and slot.get("pillar_id")) or bool(ctx.commander_does.get(ref))


def _floor_removals(good: Mapping[str, Mapping], ctx: Context) -> List[dict]:
    return []  # slots that are absent are not floored; removal of a floored slot is checked per-op.


def _lands_rule(lands: Optional[int], ctx: Context) -> List[dict]:
    rng = ctx.land_range()
    if lands is None or rng is None:
        return []
    if not rng[0] <= lands <= rng[1]:
        return [_issue("A7", "error", None,
                       f"Lands must be within {rng[0]:g}-{rng[1]:g} (formulas +-{LANDS_SLACK}).",
                       burgess=ctx.burgess, karsten=ctx.karsten, lands=lands)]
    return []


def error_issues(issues: Iterable[dict]) -> List[dict]:
    return [i for i in issues if i["level"] == "error"]


# ---------------------------------------------------------------------------------------------
# Preset assembly
# ---------------------------------------------------------------------------------------------

def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:24] or "preset"


def new_preset_id(name: str, salt: str = "") -> str:
    h = hashlib.sha1(f"{name}|{salt}|{_now()}".encode()).hexdigest()[:6]
    return f"p-{_slug(name)}-{h}"


def _clean_slot(s: Mapping, ai: bool) -> dict:
    out = {"ref": normalize_ref(s.get("ref")), "target": s.get("target"), "min": s.get("min"),
           "max": s.get("max"), "why": " ".join(str(s.get("why") or "").split()) or None,
           "pillar_id": s.get("pillar_id") or None}
    return out


def make_preset(draft: Mapping, ctx: Context, source: str, *, based_on: Optional[str] = None,
                confidence: str = "heuristic") -> dict:
    name = " ".join(str(draft.get("name") or "").split())
    st = draft.get("structure") if isinstance(draft.get("structure"), Mapping) else {}
    lands = st.get("lands", draft.get("lands"))
    now = _now()
    return {
        "id": new_preset_id(name, source), "schema": SPEC_VERSION, "kind": "user", "name": name,
        "description": " ".join(str(draft.get("description") or "").split()),
        "source": source, "confidence": confidence, "based_on": based_on,
        "structure": {"deck_size": 100, "commanders": ctx.commanders_count, "lands": lands},
        "slots": [_clean_slot(s, True) for s in draft.get("slots") or [] if isinstance(s, Mapping)],
        "auto_select": None, "version": 1, "created_at": now, "updated_at": now,
    }


# ---------------------------------------------------------------------------------------------
# Packet (§8.2)
# ---------------------------------------------------------------------------------------------

def _qty(deck: Mapping) -> Dict[str, int]:
    q: Dict[str, int] = {}
    for c in deck.get("cards") or []:
        if isinstance(c, Mapping) and c.get("name"):
            q[c["name"]] = q.get(c["name"], 0) + int(c.get("quantity") or 1)
    return q


def _tagset(entries: Optional[Iterable[Mapping]]) -> set:
    return {str(e.get("tag", "")).lower() for e in entries or [] if isinstance(e, Mapping)}


def _front_land(card: Optional[Mapping]) -> bool:
    return bool(card) and "land" in ((card.get("type_line") or "").split("//")[0]).lower()


def _mv(card: Optional[Mapping]) -> Optional[float]:
    if not card:
        return None
    v = card.get("cmc", card.get("mana_value"))
    return float(v) if isinstance(v, (int, float)) else None


def count_refs(deck: Mapping, texts: Mapping[str, Mapping], card_tags: Mapping[str, list]) -> Dict[str, int]:
    """Counts by quantity (C2), commander excluded (C3). Group slots count distinct cards by quantity."""
    cmdr = {n for n in deck.get("commanders") or [] if isinstance(n, str)}
    qty = {n: q for n, q in _qty(deck).items() if n not in cmdr}
    tags = {n: _tagset(card_tags.get(n)) for n in qty}
    counts: Dict[str, int] = {}
    counts["structure:lands"] = sum(q for n, q in qty.items() if _front_land(texts.get(n)))
    for c in CORE_REFS:
        counts[f"core:{c}"] = sum(q for n, q in qty.items() if c in tags[n])
    tgt = {"removal", "counterspell"}
    counts["group:targeted_interaction"] = sum(q for n, q in qty.items() if tags[n] & tgt)
    counts["group:interaction"] = sum(q for n, q in qty.items() if tags[n] & (tgt | {"board_wipe"}))
    for t in TYPE_REFS:
        want = ("instant", "sorcery") if t == "instant_sorcery" else (t,)
        counts[f"type:{t}"] = sum(
            q for n, q in qty.items()
            if texts.get(n) and not _front_land(texts[n])
            and any(w in ((texts[n].get("type_line") or "").split("//")[0].split("—")[0]).lower() for w in want))
    return counts


def land_math(deck: Mapping, texts: Mapping[str, Mapping], card_tags: Mapping[str, list]) -> dict:
    """Burgess = 31 + |CI| + commander MV. Karsten = 31.42 + 3.13*avgMV - 0.28*cheap."""
    cmdrs = [n for n in deck.get("commanders") or [] if n]
    ci: set = set()
    for n in cmdrs:
        ci.update((texts.get(n) or {}).get("color_identity") or [])
    mvs = [m for m in (_mv(texts.get(n)) for n in cmdrs) if m is not None]
    cmdr_mv = max(mvs) if mvs else 0
    qty = {n: q for n, q in _qty(deck).items() if n not in cmdrs}
    total_mv, cards, cheap = 0.0, 0, 0
    for n, q in qty.items():
        c = texts.get(n)
        if not c or _front_land(c):
            continue
        m = _mv(c) or 0.0
        total_mv += m * q
        cards += q
        if m <= 2 and _tagset(card_tags.get(n)) & {"ramp", "card_advantage"}:
            cheap += q
    avg = round(total_mv / cards, 2) if cards else 0.0
    return {"burgess": 31 + len(ci) + int(cmdr_mv), "karsten": round(31.42 + 3.13 * avg - 0.28 * cheap, 1),
            "avg_mv_nonland": avg, "cheap_ramp_draw": cheap, "commander_mv": int(cmdr_mv)}


def modified_floors(commander_mv: int, commander_does: Mapping[str, bool], tokens_active: bool) -> Dict[str, int]:
    floors = dict(BASE_FLOORS)
    if commander_mv >= 5:
        floors["core:ramp"] += 2
    for ref in list(floors):
        if commander_does.get(ref):
            floors[ref] -= 3
    if tokens_active:
        floors["core:board_wipe"] = 0
    return {k: max(0, v) for k, v in floors.items()}


def build_packet(deck: Mapping, texts: Mapping[str, Mapping], card_tags: Mapping[str, list],
                 deck_tags: Iterable[str] = (), ident: Optional[Mapping] = None,
                 current_preset: Optional[Mapping] = None, global_custom_ids: Iterable[str] = (),
                 existing_preset_names: Iterable[str] = ()) -> dict:
    """The §8.2 packet. Pet cards are not sent; ``deck_card_names`` is for validation only and is
    stripped before the prompt is built."""
    cmdrs = [n for n in deck.get("commanders") or [] if n]
    if not cmdrs:
        raise CompositionAIError("the deck has no commander")
    lm = land_math(deck, texts, card_tags)
    counts = count_refs(deck, texts, card_tags)
    tags = [str(t).lower() for t in deck_tags if t]
    themes = [t for t in tags if t not in ("commander", "casual", "competitive")]
    cdoes = {}
    for ref in BASE_FLOORS:
        ids = ({"removal", "counterspell"} if ref == "group:targeted_interaction" else {ref.split(":", 1)[1]})
        cdoes[ref] = any(_tagset(card_tags.get(n)) & ids for n in cmdrs)
    floors = modified_floors(lm["commander_mv"], cdoes, "tokens" in tags)
    ident = ident or {}
    pillars = [{"id": p.get("id"), "name": p.get("name")} for p in ident.get("pillars") or []
               if isinstance(p, Mapping) and p.get("state") not in ("rejected", "archived")]
    cmd_ci: set = set()
    for n in cmdrs:
        cmd_ci.update((texts.get(n) or {}).get("color_identity") or [])
    arche = ident.get("archetype_preferences") or {}
    return {
        "commander": {"names": cmdrs, "mv": lm["commander_mv"], "color_identity": sorted(cmd_ci),
                      "oracle_text": [((texts.get(n) or {}).get("oracle_text") or "")[:400] for n in cmdrs]},
        "commanders_count": min(2, len(cmdrs)),
        "journal": {"thesis": str(ident.get("thesis") or "")[:600], "pillars": pillars,
                    "primary_archetype": arche.get("primary_archetype"),
                    "secondary_archetypes": arche.get("secondary_archetypes") or [],
                    "themes": arche.get("custom_themes") or [], "kindred": arche.get("kindred") or []},
        "autotag": {"deck_tags": tags, "health": []},
        "evaluation": {"counts": counts, "lands": counts["structure:lands"]},
        "avg_mv_nonland": lm["avg_mv_nonland"], "cheap_ramp_draw": lm["cheap_ramp_draw"],
        "land_math": {"burgess": lm["burgess"], "karsten": lm["karsten"]},
        "catalog": build_catalog(themes, global_custom_ids),
        "current_preset": dict(current_preset) if current_preset else {},
        "floors": floors, "commander_does": cdoes,
        "deck_name": deck.get("name") or "",
        "deck_card_names": sorted(set(_qty(deck)) - set(cmdrs)),
        "registry_id": deck.get("registry_id") or f"{deck.get('source', 'archidekt')}:{deck.get('source_id', '')}",
        "existing_preset_names": sorted(existing_preset_names),
    }


# ---------------------------------------------------------------------------------------------
# Mirror (§8.1) - deterministic, no AI
# ---------------------------------------------------------------------------------------------

def mirror_preset(packet: Mapping) -> dict:
    """Draft from the deck as it is: lands = current count, target = current count for every
    non-zero core and group slot. Still validated (as a plain preset, not as AI output)."""
    ctx = Context(packet)
    counts = (packet.get("evaluation") or {}).get("counts") or {}
    lands = int(counts.get("structure:lands", (packet.get("evaluation") or {}).get("lands", 36)))
    slots = []
    for ref in [f"core:{c}" for c in CORE_REFS] + [f"group:{g}" for g in GROUP_REFS]:
        n = counts.get(ref, 0)
        if n and n > 0:
            slots.append({"ref": ref, "target": int(n), "min": None, "max": None,
                          "why": "Matches the deck as built.", "pillar_id": None})
    name = f"Mirror of {packet.get('deck_name') or 'deck'}"[:NAME_MAX].strip()
    draft = {"name": name, "description": "Targets copied from the deck's current counts.",
             "structure": {"lands": lands}, "slots": slots}
    preset = make_preset(draft, ctx, f"mirror:{packet.get('registry_id') or ''}", confidence="observed")
    preset["issues"] = validate_preset(preset, ctx, ai=False)
    return preset


# ---------------------------------------------------------------------------------------------
# LLM plumbing
# ---------------------------------------------------------------------------------------------

_SLOT_SCHEMA = {"type": "OBJECT", "properties": {
    "ref": {"type": "STRING"}, "target": {"type": "INTEGER", "nullable": True},
    "min": {"type": "INTEGER", "nullable": True}, "max": {"type": "INTEGER", "nullable": True},
    "why": {"type": "STRING"}, "pillar_id": {"type": "STRING", "nullable": True}},
    "required": ["ref", "why"]}

GENERATE_SCHEMA = {"type": "OBJECT", "properties": {
    "name": {"type": "STRING"}, "description": {"type": "STRING"},
    "structure": {"type": "OBJECT", "properties": {"lands": {"type": "INTEGER"}}, "required": ["lands"]},
    "slots": {"type": "ARRAY", "items": _SLOT_SCHEMA}, "lands_math": {"type": "STRING"}},
    "required": ["name", "structure", "slots", "lands_math"]}

IMPROVE_SCHEMA = {"type": "OBJECT", "properties": {
    "ops": {"type": "ARRAY", "items": {"type": "OBJECT", "properties": {
        "op": {"type": "STRING"}, "ref": {"type": "STRING", "nullable": True},
        "from": {"type": "OBJECT", "nullable": True}, "to": {"type": "OBJECT", "nullable": True},
        "why": {"type": "STRING"}, "pillar_id": {"type": "STRING", "nullable": True}},
        "required": ["op", "why"]}},
    "summary": {"type": "STRING"}}, "required": ["ops", "summary"]}

_RULES = (
    "Rules (a validator rejects anything that breaks them):\n"
    f"- Use only refs from the catalog. A slot sets target, min and/or max as integers 0-99; min <= target <= max.\n"
    "- A preset is a SHAPE: counts per category. Never name cards.\n"
    f"- Keep plan room >= {PLAN_ROOM_MIN}: (100 - commanders - lands) minus the sum of the lower bounds "
    "of core:/group: slots. group:targeted_interaction already contains removal and counterspell.\n"
    "- Lands must stay within 2 of the Burgess/Karsten range given in the packet.\n"
    "- Do not set a floored slot below its floor (packet.floors) unless a journal pillar justifies it "
    "(then set pillar_id to that pillar's id) or the commander does that job.\n"
    f"- Every slot/op needs a 'why' of at most {WHY_MAX} characters, based on the commander, the journal "
    "and the deck's own numbers. No card names; no mention of EDHREC, staples, price, salt or meta.\n"
    "- Use theme:/type:/facet: slots as min-only floors for the deck's engine, sparingly.\n"
    "Return JSON only.")

GENERATE_SYSTEM = (
    "You design a deck COMPOSITION PRESET for a Magic: The Gathering Commander deck: how many lands, "
    "ramp, card draw, interaction and so on, as category counts.\n"
    "Ground it in the commander, the journal (thesis, pillars, archetype) and the deck's current counts.\n"
    + _RULES + "\nShape: {name (<= 40 chars), description, structure:{lands}, slots:[{ref,target,min,max,why,"
    "pillar_id}], lands_math: one line working both land formulas}")

IMPROVE_SYSTEM = (
    "You improve an existing composition preset for a Magic: The Gathering Commander deck, using the "
    "deck's category ratios against the preset.\n"
    f"Return at most {MAX_OPS} ops, most valuable first; no no-op changes.\n"
    "Ops: {op:'set', ref, from:{..}, to:{target?,min?,max?}, why, pillar_id?}, {op:'add', ref, to:{..}, why}, "
    "{op:'remove', ref, why, pillar_id?}, {op:'lands', from:int, to:int, why}.\n"
    + _RULES + "\nAlso return summary (<= 280 chars).")


def _prompt_packet(packet: Mapping) -> dict:
    return {k: v for k, v in packet.items()
            if k not in ("deck_card_names", "existing_preset_names", "registry_id", "deck_name", "evaluation")} | {
        "evaluation": packet.get("evaluation")}


def _call(chain, system: str, packet: Mapping, task: str, schema: dict, purpose: str, on_call=None):
    import json
    if chain is None or not chain.available():
        raise CompositionAIUnavailable("no_llm", "no LLM provider is configured")
    prompt = task + "\n\nPACKET:\n" + json.dumps(_prompt_packet(packet), ensure_ascii=False, default=str)
    data, provider, model = chain.generate_json(system, prompt, schema=schema, purpose=purpose, on_call=on_call)
    if data is None:
        raise CompositionAIUnavailable("llm_failed", "every LLM provider failed")
    if not isinstance(data, Mapping):
        raise CompositionAIUnavailable("bad_response", "the LLM did not return a JSON object")
    return data, provider, model


def _draft_meta(**kw) -> dict:
    # A8: drafts are never saved, never pin a deck, never touch the Journal.
    return {"draft": True, "saved": False, "pins_deck": False, **kw}


# ---------------------------------------------------------------------------------------------
# Generate
# ---------------------------------------------------------------------------------------------

def generate_preset(packet: Mapping, chain, on_call: Optional[Callable] = None, *, fallback: bool = True) -> dict:
    """LLM draft of a whole preset. With no LLM (or when every provider fails) and ``fallback``,
    returns the deterministic Mirror draft with ``fallback: "mirror"``. An AI draft that breaks
    A1-A8 is rejected: CompositionAIUnavailable("invalid_draft", issues=[...])."""
    ctx = Context(packet)
    try:
        data, provider, model = _call(
            chain, GENERATE_SYSTEM, packet, "TASK: generate a preset for this deck.", GENERATE_SCHEMA,
            "composition_generate", on_call)
    except CompositionAIUnavailable as exc:
        if fallback and exc.kind in ("no_llm", "llm_failed"):
            return {"preset": mirror_preset(packet), "fallback": "mirror", "reason": exc.kind, "ai": False,
                    **_draft_meta()}
        raise
    preset = make_preset(data, ctx, f"ai:{model}")
    if not preset["slots"]:
        raise CompositionAIUnavailable("bad_response", "the LLM returned no slots")
    issues = validate_preset(preset, ctx, ai=True)
    if error_issues(issues):
        raise CompositionAIUnavailable("invalid_draft", "the AI draft broke the composition rules", issues)
    preset["issues"] = issues
    lm = " ".join(str(data.get("lands_math") or "").split())[:300]
    return {"preset": preset, "lands_math": lm, "provider": provider, "model": model, "ai": True, "fallback": None,
            **_draft_meta()}


# ---------------------------------------------------------------------------------------------
# Improve
# ---------------------------------------------------------------------------------------------

def _clean_bounds(d: Any) -> Optional[dict]:
    if not isinstance(d, Mapping):
        return None
    return {k: d.get(k) for k in ("target", "min", "max") if k in d}


def apply_op(preset: Mapping, op: Mapping) -> Tuple[Optional[dict], Optional[str]]:
    """Returns (new preset, None) or (None, reason). Pure; checks structure only (no rules)."""
    kind = op.get("op")
    new = copy.deepcopy(dict(preset))
    slots = new.setdefault("slots", [])
    if kind == "lands":
        cur = (new.get("structure") or {}).get("lands")
        to = op.get("to")
        if not _is_int(to):
            return None, "lands op needs an integer 'to'"
        if to == cur:
            return None, "no-op: lands unchanged"
        if op.get("from") is not None and op.get("from") != cur:
            return None, f"stale: lands are {cur}, not {op.get('from')}"
        new["structure"] = {**(new.get("structure") or {}), "lands": to}
        return new, None
    ref = normalize_ref(op.get("ref"))
    idx = next((i for i, s in enumerate(slots) if normalize_ref(s.get("ref")) == ref), None)
    if kind == "remove":
        if idx is None:
            return None, f"{ref} is not in the preset"
        del slots[idx]
        return new, None
    to = _clean_bounds(op.get("to"))
    if kind == "add":
        if idx is not None:
            return None, f"{ref} is already in the preset"
        if not to:
            return None, "add needs 'to'"
        slots.append({"ref": ref, "target": to.get("target"), "min": to.get("min"), "max": to.get("max"),
                      "why": op.get("why"), "pillar_id": op.get("pillar_id") or None})
        return new, None
    if kind == "set":
        if idx is None:
            return None, f"{ref} is not in the preset"
        if not to:
            return None, "set needs 'to'"
        old = slots[idx]
        merged = {k: old.get(k) for k in ("target", "min", "max")}
        merged.update(to)
        if all(merged[k] == old.get(k) for k in merged):
            return None, "no-op: nothing changes"
        frm = _clean_bounds(op.get("from"))
        if frm and any(old.get(k) != v for k, v in frm.items()):
            return None, "stale: 'from' does not match the preset"
        slots[idx] = {**old, **merged, "why": op.get("why"), "pillar_id": op.get("pillar_id") or old.get("pillar_id")}
        return new, None
    return None, f"unknown op '{kind}'"


def _op_rule_issues(op: Mapping, before: Mapping, after: Mapping, ctx: Context) -> List[dict]:
    out: List[dict] = []
    prob = _why_problem(op.get("why"), ctx)
    if prob:
        out.append(_issue("A6", "error", normalize_ref(op.get("ref")) or None, prob.capitalize() + "."))
    ref = normalize_ref(op.get("ref"))
    floor = ctx.floors.get(ref)
    if floor is not None and op.get("op") in ("set", "remove", "add"):
        slot = next((s for s in after.get("slots") or [] if normalize_ref(s.get("ref")) == ref), None)
        lo = lower(slot) if slot else 0
        if (lo or 0) < floor:
            excused = (op.get("pillar_id") in ctx.pillar_ids and op.get("pillar_id")) or ctx.commander_does.get(ref)
            if not excused:
                out.append(_issue("A5", "error", ref, f"Going below the health floor {floor} needs a pillar_id "
                                  "from the journal or a commander that does this job.", floor=floor))
    if op.get("op") == "lands":
        out += _lands_rule((after.get("structure") or {}).get("lands"), ctx)
    if op.get("pillar_id") and op.get("pillar_id") not in ctx.pillar_ids:
        out.append(_issue("A5", "error", ref or None, "pillar_id is not one of the deck's pillars."))
    if ref and ctx.catalog_refs and op.get("op") in ("add", "set") and ref not in ctx.catalog_refs:
        out.append(_issue("A2", "error", ref, "Ref is not in the catalog for this deck."))
    return out


def _issue_key(i: Mapping) -> Tuple:
    return (i["code"], i.get("ref"))


def apply_ops(preset: Mapping, ops: Iterable[Mapping], ctx: Context) -> dict:
    """Apply ops one by one. An op that is malformed, a no-op, or introduces a new error (E-rule,
    W3-as-error, A2/A5/A6/A7) is dropped and reported; the rest stand (A4: at most MAX_OPS)."""
    working = copy.deepcopy(dict(preset))
    accepted: List[dict] = []
    dropped: List[dict] = []
    for n, raw in enumerate(ops):
        if not isinstance(raw, Mapping):
            dropped.append({"op": raw, "reason": "not an object", "issues": []})
            continue
        if len(accepted) >= MAX_OPS:
            dropped.append({"op": dict(raw), "reason": f"A4: at most {MAX_OPS} ops", "issues": []})
            continue
        new, why_not = apply_op(working, raw)
        if new is None:
            dropped.append({"op": dict(raw), "reason": why_not, "issues": []})
            continue
        baseline = {_issue_key(i) for i in error_issues(validate_preset(working, ctx, ai=True))}
        after = validate_preset(new, ctx, ai=True)
        bad = [i for i in error_issues(after) if _issue_key(i) not in baseline]
        bad += _op_rule_issues(raw, working, new, ctx)
        if bad:
            dropped.append({"op": dict(raw), "reason": "; ".join(i["message"] for i in bad), "issues": bad})
            continue
        working = new
        accepted.append({k: raw.get(k) for k in ("op", "ref", "from", "to", "why", "pillar_id") if k in raw})
    return {"preset": working, "ops": accepted, "dropped": dropped}


def improve_preset(packet: Mapping, chain, on_call: Optional[Callable] = None) -> dict:
    """LLM ops against ``packet['current_preset']``. Returns the accepted ops, the dropped ones with
    reasons, a summary, and the resulting preset (a draft: not saved, deck not pinned)."""
    current = packet.get("current_preset")
    if not isinstance(current, Mapping) or not current.get("slots") and not current.get("structure"):
        raise CompositionAIError("improve needs a current preset; use generate or mirror first")
    ctx = Context(packet)
    data, provider, model = _call(chain, IMPROVE_SYSTEM, packet, "TASK: improve the current preset.",
                                  IMPROVE_SCHEMA, "composition_improve", on_call)
    ops = data.get("ops")
    if not isinstance(ops, list):
        raise CompositionAIUnavailable("bad_response", "the LLM returned no ops list")
    res = apply_ops(current, ops, ctx)
    summary = " ".join(str(data.get("summary") or "").split())[:SUMMARY_MAX]
    preset = res["preset"]
    if res["ops"]:
        preset["version"] = int(preset.get("version") or 0) + 1
        preset["updated_at"] = _now()
    preset["issues"] = validate_preset(preset, ctx, ai=False)
    return {"preset": preset, "ops": res["ops"], "dropped": res["dropped"], "summary": summary,
            "provider": provider, "model": model, "ai": True, **_draft_meta()}

"""
Deck composition data models (composition-v1): presets, deck composition
records, validation (E1-E9, W1-W3, I1) and auto-selection (spec section 6).

Pure module: no Redis, no I/O. Persistence lives in `composition_store.py`.
Presets are plain JSON-able dicts (the project stores JSON in Redis; there is
no SQL). A preset is a *shape*: structure (deck size, commanders, lands) plus
slots (category ref + target/min/max). Presets never name cards.

Ref grammar `<kind>:<id>` with kinds structure/type/core/group/facet/theme/cat.

Validation issues: {code, level: error|warning|info, ref|None, message, data}.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

SPEC_VERSION = "composition-v1"
DECK_SIZE = 100
MAX_USER_PRESETS = 50
MAX_SLOTS = 24
MAX_OVERRIDES = 12
MIN_PLAN_ROOM = 15
MAX_NAME = 40
MAX_DESCRIPTION = 400
MAX_WHY = 160

KIND_USER = "user"
CONFIDENCES = ("exact", "model", "heuristic", "empirical", "observed")
MODE_AUTO = "auto"
MODE_PINNED = "pinned"

CORE_IDS = ("ramp", "mana_fixing", "card_advantage", "tutor", "recursion", "removal",
            "counterspell", "board_wipe", "protection", "finisher")
GROUP_IDS = ("targeted_interaction", "interaction")
TYPE_IDS = ("creature", "artifact", "enchantment", "instant", "sorcery", "planeswalker",
            "battle", "instant_sorcery")
FACET_IDS = ("answers_creature", "answers_artifact", "answers_enchantment",
             "answers_artifact_enchantment", "answers_planeswalker", "flexible",
             "answers_stack", "instant_speed")
FLAG_IDS = ("game_changer", "stax", "mass_land_denial", "extra_turns")
# Autotag floors (spec C6). Keyed by ref; a preset can't change them.
FLOORS = {"core:ramp": 10, "core:card_advantage": 7, "group:targeted_interaction": 10,
          "core:board_wipe": 2, "core:finisher": 2}

# Subset -> supersets (E7).
SUPERSETS: Dict[str, Tuple[str, ...]] = {
    "core:removal": ("group:targeted_interaction", "group:interaction"),
    "core:counterspell": ("group:targeted_interaction", "group:interaction"),
    "group:targeted_interaction": ("group:interaction",),
    "core:board_wipe": ("group:interaction",),
    "type:instant": ("type:instant_sorcery",),
    "type:sorcery": ("type:instant_sorcery",),
    **{f"facet:{f}": ("group:interaction",) for f in FACET_IDS},
}
# Slots counted toward "plan room" (W3): core + group slots (+ theme/type/facet/cat).
_FUNCTION_REFS_FOR_ROOM_EXCLUDE = ("group:interaction",)


def _themes() -> Tuple[str, ...]:
    from autotag.engine import THEME_TAGS
    return tuple(THEME_TAGS)


# ---------------------------------------------------------------------------
# Issues
# ---------------------------------------------------------------------------


def issue(code: str, level: str, message: str, ref: Optional[str] = None, **data) -> dict:
    return {"code": code, "level": level, "ref": ref, "message": message, "data": data}


def errors_of(issues: Iterable[dict]) -> List[dict]:
    return [i for i in issues if i["level"] == "error"]


class CompositionValidationError(ValueError):
    """Carries every issue; the API maps it to HTTP 422."""

    def __init__(self, issues: List[dict]):
        self.issues = issues
        super().__init__("; ".join(f"{i['code']}: {i['message']}" for i in errors_of(issues)) or "invalid")


# ---------------------------------------------------------------------------
# Refs
# ---------------------------------------------------------------------------


def normalize_ref(ref: Any) -> str:
    ref = str(ref or "").strip().lower() if not str(ref or "").startswith("cat:") else str(ref).strip()
    if ref == "core:lands":
        return "structure:lands"
    return ref


def check_ref(ref: str, scope: str = "preset") -> Optional[dict]:
    """E3 for one ref. scope = 'preset' (no deck categories) or 'deck'."""
    kind, _, ident = ref.partition(":")
    if not ident:
        return issue("E3", "error", f"'{ref}' is not a <kind>:<id> reference.", ref)
    if kind == "structure":
        return issue("E4", "error", "Lands live in structure.lands; structure refs are not slots.", ref)
    if ref in (f"flag:{f}" for f in FLAG_IDS) or kind == "flag" or ref in FLAG_IDS:
        return issue("E3", "error", "Flags are shown, never targeted.", ref)
    if ref == "derived:plan":
        return issue("E3", "error", "Plan is what's left.", ref)
    if kind in ("zone", "archetype", "genre"):
        return issue("E3", "error", f"'{kind}' refs can't be targeted.", ref)
    if kind == "core" and ident in CORE_IDS:
        return None
    if kind == "group" and ident in GROUP_IDS:
        return None
    if kind == "type" and ident in TYPE_IDS:
        return None
    if kind == "facet" and ident in FACET_IDS:
        return None
    if kind == "theme":
        if ident in _themes():
            return None
        # kindred:<type> themes: ident includes the colon, e.g. "kindred:Elf"
        if re.fullmatch(r"kindred:[a-z][a-z \-':]*", ident, re.IGNORECASE):
            return None
        return issue("E3", "error", f"Unknown theme '{ident}'. Valid: {', '.join(_themes())}, kindred:<type>.", ref)
    if kind == "cat":
        if ident.startswith("g-"):
            return None
        if ident.startswith("d-"):
            if scope == "deck":
                return None
            return issue("E3", "error", "Deck categories live in deck overrides.", ref)
        return issue("E3", "error", "Custom category ids start with g- (global) or d- (deck).", ref)
    return issue("E3", "error", f"Unknown category ref '{ref}'.", ref)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def collapse_ws(text: Any) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def spells_for(structure: Mapping) -> int:
    """nonland = 100 - commanders - lands (derived, never stored)."""
    return DECK_SIZE - int(structure["commanders"]) - int(structure["lands"])


def slot_bounds(slot: Mapping) -> Tuple[Optional[int], Optional[int]]:
    lower = slot.get("min") if slot.get("min") is not None else slot.get("target")
    upper = slot.get("max") if slot.get("max") is not None else slot.get("target")
    return lower, upper


def band_of(slot: Mapping, band: int = 1) -> Tuple[Optional[int], Optional[int]]:
    """Display band (C5): lo/hi, falling back to target +- band."""
    t = slot.get("target")
    lo = slot.get("min") if slot.get("min") is not None else (t - band if t is not None else None)
    hi = slot.get("max") if slot.get("max") is not None else (t + band if t is not None else None)
    return lo, hi


# ---------------------------------------------------------------------------
# Built-in presets (code constants, locked)
# ---------------------------------------------------------------------------


def empty_auto_select() -> dict:
    return {"archetypes": [], "themes": [], "commander_mv_min": None, "priority": 0}


# Legacy AN-3 template row -> ref (spec section 9). `plan` is dropped (derived).
LEGACY_ROW_REFS = {
    "lands": None,  # -> structure.lands / deck `lands`
    "ramp": "core:ramp",
    "card_advantage": "core:card_advantage",
    "targeted_disruption": "group:targeted_interaction",
    "mass_disruption": "core:board_wipe",
    "plan": None,
}


# ---------------------------------------------------------------------------
# Preset validation
# ---------------------------------------------------------------------------


def _check_slot_numbers(slot: Mapping, ref: str, out: List[dict]) -> bool:
    vals = {k: slot.get(k) for k in ("target", "min", "max")}
    ok = True
    for k, v in vals.items():
        if v is not None and (not _is_int(v) or not 0 <= v <= 99):
            out.append(issue("E5", "error", f"{k} must be null or a whole number 0-99.", ref, field=k))
            ok = False
    if not ok:
        return False
    if all(v is None for v in vals.values()):
        out.append(issue("E5", "error", "Set at least one of target, min, max.", ref))
        return False
    t, lo, hi = vals["target"], vals["min"], vals["max"]
    if (lo is not None and t is not None and lo > t) or (t is not None and hi is not None and t > hi) \
            or (lo is not None and hi is not None and lo > hi):
        out.append(issue("E5", "error", "Need min <= target <= max.", ref))
        return False
    return True


def plan_room(preset: Mapping) -> int:
    """Spells left after the lower bounds of the non-umbrella slots (W3).

    When a group and its members are both present, count max(group lower,
    sum of member lowers)."""
    slots = {normalize_ref(s.get("ref")): s for s in preset.get("slots") or []}
    lowers = {r: (slot_bounds(s)[0] or 0) for r, s in slots.items()}
    members: Dict[str, int] = {}
    counted_members = set()
    for sub, supers in SUPERSETS.items():
        if sub in lowers:
            for sup in supers:
                if sup in lowers:
                    members[sup] = members.get(sup, 0) + lowers[sub]
                    counted_members.add(sub)
    total = 0
    for r, lo in lowers.items():
        if r in counted_members:
            continue
        total += max(lo, members.get(r, 0))
    return spells_for(preset["structure"]) - total


def validate_preset(preset: Mapping, existing: Iterable[Mapping] = (), *, ai: bool = False) -> List[dict]:
    """All issues for a preset. `existing` = other presets (built-in + user,
    excluding this one) for E1 name uniqueness."""
    out: List[dict] = []
    name = collapse_ws(preset.get("name"))
    if not 1 <= len(name) <= MAX_NAME:
        out.append(issue("E1", "error", f"Name must be 1-{MAX_NAME} characters.", None))
    else:
        taken = {collapse_ws(p.get("name")).lower() for p in existing}
        if name.lower() in taken:
            out.append(issue("E1", "error", f"A preset named '{name}' already exists.", None))
    if len(str(preset.get("description") or "")) > MAX_DESCRIPTION:
        out.append(issue("E1", "error", f"Description is over {MAX_DESCRIPTION} characters.", None))
    if preset.get("confidence", "heuristic") not in CONFIDENCES:
        out.append(issue("E1", "error", f"confidence must be one of {', '.join(CONFIDENCES)}.", None))

    st = preset.get("structure") or {}
    structure_ok = True
    if st.get("deck_size") != DECK_SIZE or not _is_int(st.get("deck_size")):
        out.append(issue("E2", "error", f"deck_size must be {DECK_SIZE}.", None)); structure_ok = False
    if st.get("commanders") not in (1, 2) or not _is_int(st.get("commanders")):
        out.append(issue("E2", "error", "commanders must be 1 or 2.", None)); structure_ok = False
    elif not _is_int(st.get("lands")) or not 0 <= st["lands"] <= DECK_SIZE - st["commanders"]:
        out.append(issue("E2", "error", f"lands must be a whole number 0-{DECK_SIZE - st['commanders']}.", None))
        structure_ok = False
    if structure_ok and (st["lands"] < 33 or st["lands"] > 42):
        out.append(issue("W1", "warning", "Lands outside 33-42.", None, lands=st["lands"]))

    slots = preset.get("slots") or []
    if len(slots) > MAX_SLOTS:
        out.append(issue("E8", "error", f"At most {MAX_SLOTS} slots.", None, count=len(slots)))
    seen: Dict[str, dict] = {}
    for s in slots:
        ref = normalize_ref(s.get("ref"))
        bad = check_ref(ref, "preset")
        if bad:
            out.append(bad)
            continue
        if ref in seen:
            out.append(issue("E4", "error", "Duplicate ref.", ref))
            continue
        if not _check_slot_numbers(s, ref, out):
            continue
        if len(str(s.get("why") or "")) > MAX_WHY:
            out.append(issue("E1", "error", f"why is over {MAX_WHY} characters.", ref))
        if ai and not collapse_ws(s.get("why")):
            out.append(issue("A1", "error", "AI slots need a why.", ref))
        seen[ref] = s
        if structure_ok:
            lower, _ = slot_bounds(s)
            if lower is not None and lower > spells_for(st):
                out.append(issue("E6", "error", "Lower bound exceeds the spells available.", ref,
                                 lower=lower, spells=spells_for(st)))
    for sub, supers in SUPERSETS.items():
        if sub not in seen:
            continue
        for sup in supers:
            if sup not in seen:
                continue
            sub_lo = slot_bounds(seen[sub])[0]
            sup_hi = slot_bounds(seen[sup])[1]
            if sub_lo is not None and sup_hi is not None and sub_lo > sup_hi:
                out.append(issue("E7", "error", f"{sub} needs {sub_lo} but {sup} allows only {sup_hi}.", sub,
                                 superset=sup))
    for ref, s in seen.items():  # W2 (preset scope)
        floor = FLOORS.get(ref)
        if floor is None:
            continue
        a = preset.get("auto_select") or {}
        if ref == "core:ramp" and (a.get("commander_mv_min") or 0) >= 5:
            floor += 2
        if ref == "core:board_wipe" and "tokens" in (a.get("themes") or []):
            floor = 0
        upper = slot_bounds(s)[1]
        if upper is not None and upper < floor:
            out.append(issue("W2", "warning", f"Upper bound {upper} is below the autotag floor {floor}.", ref,
                             floor=floor))
    out.extend(validate_auto_select(preset.get("auto_select")))
    if structure_ok and not errors_of(out):
        room = plan_room({**preset, "structure": st})
        if room < MIN_PLAN_ROOM:
            out.append(issue("A3" if ai else "W3", "error" if ai else "warning",
                             f"Plan room is {room}; keep at least {MIN_PLAN_ROOM} spells for the deck's thesis.",
                             None, plan_room=room))
    return out


def validate_auto_select(a: Any) -> List[dict]:
    if a is None:
        return []
    out: List[dict] = []
    if not isinstance(a, Mapping):
        return [issue("E3", "error", "auto_select must be an object.", None)]
    from deck_genre import ARCHETYPE_IDS
    for arch in a.get("archetypes") or []:
        if arch not in ARCHETYPE_IDS:
            out.append(issue("E3", "error", f"Unknown archetype '{arch}'.", None, archetype=arch))
    for th in a.get("themes") or []:
        if th != "kindred:*" and check_ref(f"theme:{th}"):
            out.append(issue("E3", "error", f"Unknown theme '{th}'.", None, theme=th))
    mv = a.get("commander_mv_min")
    if mv is not None and (not _is_int(mv) or not 0 <= mv <= 20):
        out.append(issue("E5", "error", "commander_mv_min must be null or a whole number 0-20.", None))
    if not _is_int(a.get("priority", 0)):
        out.append(issue("E5", "error", "priority must be a whole number.", None))
    return out


def normalize_preset(raw: Mapping) -> dict:
    """Canonical shape with defaults; refs normalised. Does not validate."""
    a = raw.get("auto_select") or {}
    return {
        "id": raw.get("id"), "schema": SPEC_VERSION, "kind": raw.get("kind") or KIND_USER,
        "name": collapse_ws(raw.get("name")), "description": str(raw.get("description") or ""),
        "source": raw.get("source") or "user", "confidence": raw.get("confidence") or "heuristic",
        "based_on": raw.get("based_on"),
        "structure": {"deck_size": (raw.get("structure") or {}).get("deck_size", DECK_SIZE),
                      "commanders": (raw.get("structure") or {}).get("commanders", 1),
                      "lands": (raw.get("structure") or {}).get("lands")},
        "slots": [{"ref": normalize_ref(s.get("ref")), "target": s.get("target"), "min": s.get("min"),
                   "max": s.get("max"), "why": str(s.get("why") or ""), "pillar_id": s.get("pillar_id")}
                  for s in raw.get("slots") or []],
        "auto_select": {"archetypes": list(a.get("archetypes") or []), "themes": list(a.get("themes") or []),
                        "commander_mv_min": a.get("commander_mv_min"), "priority": a.get("priority", 0)},
        "version": raw.get("version", 1), "created_at": raw.get("created_at"), "updated_at": raw.get("updated_at"),
    }


def user_preset_id(name: str, token: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", collapse_ws(name).lower()).strip("-")[:24].strip("-") or "preset"
    return f"p-{slug}-{token}"


# ---------------------------------------------------------------------------
# Deck composition record (spec 4.2)
# ---------------------------------------------------------------------------


def default_deck_composition() -> dict:
    return {"mode": MODE_AUTO, "preset_id": None, "lands": None, "overrides": [], "updated_at": None}


def validate_deck_composition(rec: Mapping, presets_by_id: Mapping[str, Mapping]) -> List[dict]:
    out: List[dict] = []
    mode = rec.get("mode")
    if mode not in (MODE_AUTO, MODE_PINNED):
        return [issue("E3", "error", "mode must be 'auto' or 'pinned'.", None)]
    overrides = rec.get("overrides") or []
    if mode == MODE_AUTO:
        if overrides or rec.get("lands") is not None or rec.get("preset_id"):
            out.append(issue("E3", "error", "Auto mode has no preset, lands or overrides.", None))
        return out
    pid = rec.get("preset_id")
    if pid not in presets_by_id:
        out.append(issue("E3", "error", f"Unknown preset '{pid}'.", None, preset_id=pid))
    if len(overrides) > MAX_OVERRIDES:
        out.append(issue("E8", "error", f"At most {MAX_OVERRIDES} overrides.", None))
    lands = rec.get("lands")
    base = presets_by_id.get(pid)
    if lands is not None:
        cmd = int((base or {}).get("structure", {}).get("commanders", 1) or 1)
        if not _is_int(lands) or not 0 <= lands <= DECK_SIZE - cmd:
            out.append(issue("E2", "error", f"lands must be a whole number 0-{DECK_SIZE - cmd}.", None))
    seen = set()
    for o in overrides:
        ref = normalize_ref(o.get("ref"))
        bad = check_ref(ref, "deck")
        if bad:
            out.append(bad)
            continue
        if ref in seen:
            out.append(issue("E4", "error", "Duplicate override ref.", ref))
            continue
        seen.add(ref)
        if not o.get("remove"):
            _check_slot_numbers(o, ref, out)
    return out


# ---------------------------------------------------------------------------
# Auto-selection (spec section 6)
# ---------------------------------------------------------------------------


def _journal_archetypes(journal: Optional[Mapping]) -> Tuple[Optional[str], List[str]]:
    ap = (journal or {}).get("archetype_preferences") or {}
    primary = ap.get("primary_archetype") or None
    secondary = list(ap.get("secondary_archetypes") or [])
    # Legacy phantom: bare 'midrange' with nothing else counts as unset.
    if primary == "midrange" and not secondary and not ap.get("excluded_archetypes") \
            and not ap.get("custom_themes"):
        primary = None
    return primary, secondary


def _theme_match(pattern: str, tag: str) -> bool:
    return tag.startswith("kindred:") if pattern == "kindred:*" else pattern == tag


def resolve_preset(record: Optional[Mapping], user_presets: Iterable[Mapping], *,
                   journal: Optional[Mapping] = None, deck_tags: Iterable[str] = (),
                   commander_mv: Optional[float] = None) -> dict:
    """Returns {preset_id, mode, reason: {step, signal}, also_matched, issues}.

    Never writes anything. No built-in presets exist; only user presets are
    considered, sorted by priority desc then id asc. When nothing matches (or
    a pinned preset no longer exists), preset_id is None ("no preset
    selected") instead of silently falling back to a hardcoded default."""
    record = record or default_deck_composition()
    user_presets_list: List[dict] = list(user_presets)  # type: ignore[arg-type]
    by_id = {p["id"]: p for p in user_presets_list}
    if record.get("mode") == MODE_PINNED:
        pid = record.get("preset_id")
        if pid in by_id:
            return {"preset_id": pid, "mode": MODE_PINNED, "reason": {"step": 1, "signal": "pinned"},
                    "also_matched": [], "issues": []}
        return {"preset_id": None, "mode": MODE_PINNED,
                "reason": {"step": 1, "signal": "pinned preset missing"}, "also_matched": [],
                "issues": [issue("I3", "info", f"Pinned preset '{pid}' no longer exists.", None, stale_pin=pid)]}

    def ordered() -> List[dict]:
        key = lambda p: (-int((p.get("auto_select") or {}).get("priority") or 0), p["id"])  # noqa: E731
        return list(sorted(user_presets_list, key=key))

    primary, secondary = _journal_archetypes(journal)
    tags = list(deck_tags)
    steps: List[Tuple[int, str, List[Tuple[dict, str]]]] = []
    if primary:
        steps.append((2, "primary", [(p, f"journal primary: {primary}") for p in ordered()
                                     if primary in (p.get("auto_select") or {}).get("archetypes", [])]))
    sec_hits: List[Tuple[dict, str]] = []
    for a in secondary:
        sec_hits += [(p, f"journal secondary: {a}") for p in ordered()
                     if a in (p.get("auto_select") or {}).get("archetypes", [])]
    steps.append((3, "secondary", sec_hits))
    tag_hits: List[Tuple[dict, str]] = []
    for t in tags:
        tag_hits += [(p, f"deck tag: {t}") for p in ordered()
                     if any(_theme_match(pt, t) for pt in (p.get("auto_select") or {}).get("themes", []))]
    steps.append((4, "tag", tag_hits))
    mv_hits = []
    if commander_mv is not None:
        mv_hits = [(p, f"commander MV {int(commander_mv) if float(commander_mv).is_integer() else commander_mv}")
                   for p in ordered()
                   if (p.get("auto_select") or {}).get("commander_mv_min") is not None
                   and commander_mv >= p["auto_select"]["commander_mv_min"]]
    steps.append((5, "mv", mv_hits))
    for step, _name, hits in steps:
        if hits:
            first, signal = hits[0]
            also = []
            for p, _s in hits[1:]:
                if p["id"] != first["id"] and p["id"] not in also:
                    also.append(p["id"])
            return {"preset_id": first["id"], "mode": MODE_AUTO, "reason": {"step": step, "signal": signal},
                    "also_matched": also, "issues": []}
    return {"preset_id": None, "mode": MODE_AUTO, "reason": {"step": 6, "signal": "no preset selected"},
            "also_matched": [], "issues": []}


# ---------------------------------------------------------------------------
# Legacy migration helper (pure)
# ---------------------------------------------------------------------------


def legacy_override_to_record(legacy: Mapping, now: str) -> Tuple[dict, Optional[int]]:
    """AN-3 `composition:template:*` override -> deck composition record.

    Returns (record, dropped_plan). There is no built-in default preset to pin
    to anymore, so the migrated record carries the raw overrides with no
    preset_id ("no preset selected"); callers must handle that gracefully."""
    overrides = []
    lands = None
    dropped = None
    for row, value in legacy.items():
        if row not in LEGACY_ROW_REFS:
            continue
        if row == "plan":
            dropped = int(value)
        elif row == "lands":
            lands = int(value)
        else:
            overrides.append({"ref": LEGACY_ROW_REFS[row], "target": int(value), "min": None, "max": None})
    overrides.sort(key=lambda o: o["ref"])
    return ({"mode": MODE_PINNED, "preset_id": None, "lands": lands,
             "overrides": overrides, "updated_at": now}, dropped)

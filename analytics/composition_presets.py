"""Built-in composition presets and auto-selection (composition-v1 §5-§6).

Pure data + a pure function. Built-ins are code constants (kind "builtin"); user presets are
passed in by the caller. Nothing here touches Redis, the Journal or the LLM.

    builtin_presets()        deep copies of the nine built-ins, in a stable order
    get_builtin(preset_id)   one built-in, or None
    auto_select(...)         §6 steps 2-6 (step 1, the pin, is skipped for Baseline: ``skip_pin``)
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Mapping, Optional

SPEC_VERSION = "composition-v1"
FALLBACK_ID = "tcz_2025"

# Legacy phantom (DECK_GENRE_TAXONOMY §0): a bare "midrange" primary counts as unset.
PHANTOM_PRIMARY = "midrange"


def _s(ref: str, target: Optional[int] = None, min: Optional[int] = None, max: Optional[int] = None) -> dict:
    return {"ref": ref, "target": target, "min": min, "max": max, "why": None, "pillar_id": None}


def _auto(archetypes=(), themes=(), mv_min=None, priority=50) -> dict:
    return {"archetypes": list(archetypes), "themes": list(themes), "commander_mv_min": mv_min,
            "priority": priority}


def _preset(pid: str, name: str, source: str, confidence: str, lands: int, slots: List[dict],
            auto: Optional[dict] = None, description: str = "") -> dict:
    return {"id": pid, "schema": SPEC_VERSION, "kind": "builtin", "name": name, "description": description,
            "source": source, "confidence": confidence, "based_on": None,
            "structure": {"deck_size": 100, "commanders": 1, "lands": lands},
            "slots": slots, "auto_select": auto, "version": 1}


_TCZ = "The Command Zone ep. 658 (2025-02-18)"
_MTGDS = "MtGDS, The Deck of Many Things, EDHREC (2022-07-05)"

# Order matters only for display; auto-select sorts by (priority desc, id asc).
_BUILTINS: List[dict] = [
    _preset("tcz_2025", "Command Zone 2025", _TCZ, "heuristic", 38,
            [_s("core:ramp", 10), _s("core:card_advantage", 12), _s("group:targeted_interaction", 12),
             _s("core:board_wipe", 6)],
            description="The Command Zone template; the default fallback."),
    _preset("standard_36", "Standard 36", _MTGDS, "empirical", 36,
            [_s("core:ramp", 12), _s("core:card_advantage", 7), _s("group:targeted_interaction", 10),
             _s("core:board_wipe", 2)],
            description="36 lands, 63 spells, 1 commander (EDHREC medians)."),
    _preset("big_mana", "Big Mana", "heuristic", "heuristic", 38,
            [_s("core:ramp", 14), _s("core:card_advantage", 10), _s("group:targeted_interaction", 10),
             _s("core:board_wipe", 3)],
            _auto(["ramp"], [], 6)),
    _preset("go_wide", "Go Wide", "heuristic", "heuristic", 36,
            [_s("core:ramp", 10), _s("core:card_advantage", 10), _s("group:targeted_interaction", 10),
             _s("core:board_wipe", max=1), _s("core:protection", min=3), _s("core:finisher", min=3)],
            _auto(["tokens", "aristocrats", "tribal"], ["tokens", "sacrifice", "kindred:*"])),
    _preset("spellslinger", "Spellslinger", "heuristic", "heuristic", 36,
            [_s("core:ramp", 10), _s("core:card_advantage", 12), _s("group:targeted_interaction", 12),
             _s("core:board_wipe", 3), _s("type:instant_sorcery", min=25), _s("type:creature", max=15)],
            _auto(["spellslinger", "storm"], ["spellslinger"])),
    _preset("control", "Control", "heuristic", "heuristic", 38,
            [_s("core:ramp", 10), _s("core:card_advantage", 12), _s("group:targeted_interaction", 15),
             _s("core:board_wipe", 5), _s("facet:instant_speed", min=10), _s("core:finisher", min=2)],
            _auto(["control", "stax", "pillowfort"])),
    _preset("voltron", "Voltron", "heuristic", "heuristic", 37,
            [_s("core:ramp", 10), _s("core:card_advantage", 10), _s("core:protection", 10),
             _s("group:targeted_interaction", 10), _s("core:board_wipe", 2), _s("theme:voltron", min=10),
             _s("type:creature", max=20)],
            _auto(["voltron"], ["voltron"])),
    _preset("graveyard", "Graveyard", "heuristic", "heuristic", 37,
            [_s("core:ramp", 10), _s("core:card_advantage", 8), _s("core:recursion", 8),
             _s("group:targeted_interaction", 10), _s("core:board_wipe", 2), _s("theme:graveyard", min=10)],
            _auto(["graveyard", "reanimator"], ["graveyard"])),
    _preset("landfall", "Landfall", "heuristic", "heuristic", 38,
            [_s("core:ramp", 12), _s("core:card_advantage", 10), _s("group:targeted_interaction", 10),
             _s("core:board_wipe", 2), _s("theme:lands_matter", min=10)],
            _auto(["landfall"], ["lands_matter"])),
]


def builtin_presets() -> List[dict]:
    return copy.deepcopy(_BUILTINS)


def get_builtin(preset_id: str) -> Optional[dict]:
    for p in _BUILTINS:
        if p["id"] == preset_id:
            return copy.deepcopy(p)
    return None


def effective_primary(journal: Mapping) -> Optional[str]:
    """Primary archetype with the legacy "midrange" phantom treated as unset."""
    primary = str(journal.get("primary_archetype") or "").lower() or None
    if primary == PHANTOM_PRIMARY and not (journal.get("secondary_archetypes") or journal.get("excluded_archetypes")
                                           or journal.get("themes") or journal.get("custom_themes")):
        return None
    return primary


def _theme_match(pattern: str, tag: str) -> bool:
    if pattern.endswith(":*"):
        return tag.startswith(pattern[:-1])
    return pattern == tag


def _ordered(user_presets: Iterable[Mapping]) -> List[dict]:
    """User presets first, then built-ins; each group by priority desc, id asc."""
    def key(p):
        return (-int((p.get("auto_select") or {}).get("priority", 50)), str(p.get("id")))
    users = sorted((dict(p) for p in user_presets or [] if p.get("auto_select")), key=key)
    built = sorted((p for p in builtin_presets() if p.get("auto_select")), key=key)
    return users + built


def auto_select(journal: Mapping, deck_tags: Iterable[str], commander_mv: float,
                user_presets: Iterable[Mapping] = (), *, pin: Optional[str] = None,
                skip_pin: bool = False) -> dict:
    """§6. Returns {preset, preset_id, mode, reason: {step, signal}, also_matched}.

    ``skip_pin`` ignores a pinned preset (Baseline's B0). ``pin`` is only honoured without it.
    Auto-selection never writes anything.
    """
    users = list(user_presets or [])
    by_id: Dict[str, dict] = {p["id"]: p for p in builtin_presets()}
    by_id.update({str(p.get("id")): copy.deepcopy(dict(p)) for p in users if p.get("id")})
    if pin and not skip_pin:
        if pin in by_id:
            return _result(by_id[pin], "pinned", 1, "pinned", [])
    cands = _ordered(users)
    tags = [str(t).lower() for t in deck_tags or [] if t]
    primary = effective_primary(journal)
    secondary = [str(a).lower() for a in journal.get("secondary_archetypes") or []]

    def archetype_hits(arch: str) -> List[dict]:
        return [p for p in cands if arch in [a.lower() for a in (p["auto_select"].get("archetypes") or [])]]

    found: Optional[tuple] = None
    if primary and archetype_hits(primary):
        hits = archetype_hits(primary)
        found = (2, f"journal primary: {primary}", hits)
    if not found:
        for sec in secondary:
            hits = archetype_hits(sec)
            if hits:
                found = (3, f"journal secondary: {sec}", hits)
                break
    if not found:
        for tag in tags:
            hits = [p for p in cands if any(_theme_match(str(t).lower(), tag)
                                            for t in p["auto_select"].get("themes") or [])]
            if hits:
                found = (4, f"deck tag: {tag}", hits)
                break
    if not found:
        hits = [p for p in cands if (p["auto_select"].get("commander_mv_min") is not None
                                     and commander_mv >= p["auto_select"]["commander_mv_min"])]
        if hits:
            found = (5, f"commander MV {int(commander_mv)}", hits)
    if not found:
        return _result(by_id[FALLBACK_ID], "auto", 6, "default", [])
    step, signal, hits = found
    return _result(hits[0], "auto", step, signal, [p["id"] for p in hits[1:]])


def _result(preset: Mapping, mode: str, step: int, signal: str, also: List[str]) -> dict:
    return {"preset": copy.deepcopy(dict(preset)), "preset_id": preset["id"], "mode": mode,
            "reason": {"step": step, "signal": signal}, "also_matched": also}

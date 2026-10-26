"""Composition preset auto-selection (composition-v1 §6).

Pure functions only. Presets are plain user-created data passed in by the
caller (there are no built-ins and nothing here re-seeds one). Nothing here
touches Redis, the Journal or the LLM.

    auto_select(...)   §6 steps 2-6 (step 1, the pin, is skipped for Baseline: ``skip_pin``)
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Mapping, Optional

SPEC_VERSION = "composition-v1"

# Legacy phantom (DECK_GENRE_TAXONOMY §0): a bare "midrange" primary counts as unset.
PHANTOM_PRIMARY = "midrange"


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
    """User presets, sorted by priority desc, id asc."""
    def key(p):
        return (-int((p.get("auto_select") or {}).get("priority", 50)), str(p.get("id")))
    return sorted((dict(p) for p in user_presets or [] if p.get("auto_select")), key=key)


def auto_select(journal: Mapping, deck_tags: Iterable[str], commander_mv: float,
                user_presets: Iterable[Mapping] = (), *, pin: Optional[str] = None,
                skip_pin: bool = False) -> dict:
    """§6. Returns {preset, preset_id, mode, reason: {step, signal}, also_matched}.

    ``skip_pin`` ignores a pinned preset (Baseline's B0). ``pin`` is only honoured without it.
    Auto-selection never writes anything. When nothing matches, preset is None
    ("no preset selected") -- there is no hardcoded default to fall back to.
    """
    users = list(user_presets or [])
    by_id: Dict[str, dict] = {str(p.get("id")): copy.deepcopy(dict(p)) for p in users if p.get("id")}
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
        return _no_selection()
    step, signal, hits = found
    return _result(hits[0], "auto", step, signal, [p["id"] for p in hits[1:]])


def _no_selection() -> dict:
    return {"preset": None, "preset_id": None, "mode": "auto",
            "reason": {"step": 6, "signal": "no preset selected"}, "also_matched": []}


def _result(preset: Mapping, mode: str, step: int, signal: str, also: List[str]) -> dict:
    return {"preset": copy.deepcopy(dict(preset)), "preset_id": preset["id"], "mode": mode,
            "reason": {"step": step, "signal": signal}, "also_matched": also}

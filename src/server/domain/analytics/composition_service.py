"""Service layer for the composition preset API (CRUD + archetype auto-selection).

Thin glue between the HTTP routes and composition_store / composition_models.
Everything here is synchronous; routes call it via asyncio.to_thread.
"""
from __future__ import annotations

from typing import Any, List, Mapping, Optional

from src.server.domain.analytics import composition_store as store
from src.server.domain.analytics.composition_repository import get_preset_repository
from src.server.domain.analytics.repository_interfaces import NotFoundError
from src.server.domain.analytics.composition_models import CompositionValidationError, resolve_preset


class PresetNotFound(KeyError):
    pass


def user_presets(r) -> List[dict]:
    return list(get_preset_repository(r).list())


def list_all(r, kind: Optional[str] = None) -> List[dict]:
    if kind in ("builtin",):
        return []
    return list(get_preset_repository(r).list())


def get(r, preset_id: str) -> dict:
    p = get_preset_repository(r).get(preset_id)
    if p is None:
        raise PresetNotFound(preset_id)
    return p


def create(r, body: Mapping) -> dict:
    return get_preset_repository(r).create(dict(body))


def update(r, preset_id: str, body: Mapping) -> dict:
    try:
        return get_preset_repository(r).update(preset_id, dict(body))
    except NotFoundError as exc:
        raise PresetNotFound(preset_id) from exc


def delete(r, preset_id: str) -> None:
    if not get_preset_repository(r).delete(preset_id):
        raise PresetNotFound(preset_id)


def _deck_tag_names(r, registry_id: str) -> List[str]:
    import src.server.domain.tags.deck_tags as tag_store
    out: List[str] = []
    try:
        entries = tag_store.get_deck_tags(r, registry_id)
    except Exception:  # noqa: BLE001
        return out
    for t in entries:
        name = t.get("tag") if isinstance(t, Mapping) else t
        if isinstance(name, str) and name not in out:
            out.append(name)
    return out


def _commander_mv(r, deck: Mapping) -> Optional[float]:
    from src.server.domain.analytics import categories, insights
    try:
        return float(categories.commander_mv(dict(deck), insights.card_texts(r, dict(deck))))
    except Exception:  # noqa: BLE001
        return None


def resolve_for_deck(r, deck: Mapping, registry_id: str) -> dict:
    """Archetype-based auto-selection (spec section 6) for one deck. Read-only."""
    from src.server.domain.analytics import journal as journal_mod
    record = store.get_deck_composition(r, registry_id)
    journal = journal_mod.load_journal(r, deck) or {}
    res = resolve_preset(record, user_presets(r), journal=journal,
                         deck_tags=_deck_tag_names(r, registry_id),
                         commander_mv=_commander_mv(r, deck))
    res["preset"] = store.get_preset(r, res["preset_id"])
    res["record"] = record
    return res


def set_deck_selection(r, registry_id: str, body: Mapping) -> dict:
    """PUT body: {mode:'auto'} or {mode:'pinned', preset_id, lands?, overrides?}."""
    rec = {"mode": body.get("mode"), "preset_id": body.get("preset_id"),
           "lands": body.get("lands"), "overrides": body.get("overrides") or []}
    if rec["mode"] == "auto":
        rec.update(preset_id=None, lands=None, overrides=[])
    return store.set_deck_composition(r, registry_id, rec)


def reset_deck_selection(r, registry_id: str) -> dict:
    return set_deck_selection(r, registry_id, {"mode": "auto"})


# ---------------------------------------------------------------------------
# Brew template resolution: form pick > profile default > app default
# ---------------------------------------------------------------------------

SOURCE_FORM, SOURCE_PROFILE, SOURCE_APP = "form", "profile", "app"
_CHOICE_PROFILE = ("", "profile", "profile_default")
_CHOICE_APP = ("app", "app_default")


def _norm_needle(text: str) -> str:
    return str(text or "").lower().replace("_", " ").replace("-", " ").strip()


def _band(target: Optional[int], lo: Optional[int], hi: Optional[int]) -> tuple:
    """(low, high) card-count range: explicit min/max, else target +- 1; unbounded sides are 0 / 100."""
    from src.server.domain.analytics.composition_models import DECK_SIZE
    low = lo if lo is not None else (target - 1 if target is not None else 0)
    high = hi if hi is not None else (target + 1 if target is not None else DECK_SIZE)
    return max(int(low), 0), max(int(high), int(low), 0)


def app_default_template() -> dict:
    """The AN-3 Command Zone template (analytics/composition.py) as a brew template."""
    from src.server.domain.analytics import composition as comp
    slots = []
    for key in comp.ROW_KEYS:
        target = int(comp.DEFAULT_TEMPLATE[key])
        derived = key == "plan"
        low, high = _band(target, None, None)
        slots.append({"key": key, "label": comp.ROW_LABELS[key], "target": target, "low": low, "high": high,
                      "needles": [] if derived else [_norm_needle(key)], "rows": [] if derived else [key],
                      "types": [], "derived": derived, "steer": key != "lands"})
    return {"source": SOURCE_APP, "preset_id": None, "name": "App default (Command Zone template)", "slots": slots}


def _ref_rows(ref: str) -> List[str]:
    """Classifier rows (analytics/composition.py) that count toward a preset ref."""
    from src.server.domain.analytics.composition_models import LEGACY_ROW_REFS
    rows = [row for row, legacy_ref in LEGACY_ROW_REFS.items() if legacy_ref == ref and row != "plan"]
    if ref == "core:removal":
        rows.append("targeted_disruption")
    if ref == "group:interaction":
        rows += ["targeted_disruption", "mass_disruption"]
    return rows


def template_from_preset(preset: Mapping, source: str, r=None) -> dict:
    """A composition preset -> brew template. Refs are not validated: any category key works
    (unknown ones simply match cards by tag/category text)."""
    from src.server.domain.analytics.composition_models import normalize_ref
    slots = []
    lands = (preset.get("structure") or {}).get("lands")
    if lands is not None:
        low, high = _band(int(lands), None, None)
        slots.append({"key": "lands", "label": "Lands", "target": int(lands), "low": low, "high": high,
                      "needles": [], "rows": ["lands"], "types": [], "derived": False, "steer": False})
    for sl in preset.get("slots") or []:
        ref = normalize_ref(sl.get("ref"))
        if not ref:
            continue
        ident = ref.partition(":")[2] or ref
        kind = ref.partition(":")[0] if ":" in ref else ""
        low, high = _band(sl.get("target"), sl.get("min"), sl.get("max"))
        if sl.get("target") is None and sl.get("min") is None and sl.get("max") is None:
            low, high = 0, 100
        needles = {_norm_needle(ident), _norm_needle(ident.split(":")[-1])}
        label = ident.replace("_", " ").title()
        if kind == "cat":  # custom category: match its name (and its id without the g-/d- prefix)
            needles.add(_norm_needle(ident.partition("-")[2]))
            try:
                from src.server.domain.analytics import categories as categories_mod
                name = next((c["name"] for c in categories_mod.list_global(r) if c["id"] == ident), "") if r is not None else ""
            except Exception:  # noqa: BLE001
                name = ""
            if name:
                needles.add(_norm_needle(name))
                label = name
        needles = sorted(n for n in needles if n)
        types = []
        if kind == "type":
            types = ["instant", "sorcery"] if ident == "instant_sorcery" else [ident]
            needles = []
        slots.append({"key": ref, "label": label, "target": sl.get("target"),
                      "low": low, "high": high, "needles": needles, "rows": _ref_rows(ref),
                      "types": types, "derived": False, "why": sl.get("why") or ""})
    return {"source": source, "preset_id": preset.get("id"), "name": preset.get("name") or str(preset.get("id")),
            "slots": slots}


def _find_preset(r, preset_id: Optional[str]) -> Optional[dict]:
    if not preset_id:
        return None
    try:
        return get_preset_repository(r).get(str(preset_id))
    except Exception:  # noqa: BLE001
        return None


def resolve_brew_template(r, choice: Optional[str] = None, profile_default: Optional[str] = None) -> dict:
    """Template a Brew builds to. Order: form pick > profile default > app default.

    `choice` is a preset id, or "profile" / "" (use the profile default) or "app" (skip the profile).
    A chosen preset that does not exist raises ValueError (the user asked for it by name); a stale
    profile default silently falls through to the app default.
    """
    choice = str(choice or "").strip()
    if choice and choice not in _CHOICE_PROFILE + _CHOICE_APP:
        preset = _find_preset(r, choice)
        if preset is None:
            raise ValueError(f"composition preset not found: {choice}")
        return template_from_preset(preset, SOURCE_FORM, r)
    if choice not in _CHOICE_APP:
        preset = _find_preset(r, profile_default)
        if preset is not None:
            return template_from_preset(preset, SOURCE_PROFILE, r)
    return app_default_template()


__all__ = ["PresetNotFound", "CompositionValidationError", "app_default_template", "template_from_preset",
           "resolve_brew_template"]

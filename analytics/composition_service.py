"""Service layer for the composition preset API (CRUD + archetype auto-selection).

Thin glue between the HTTP routes and composition_store / composition_models.
Everything here is synchronous; routes call it via asyncio.to_thread.
"""
from __future__ import annotations

from typing import Any, List, Mapping, Optional

from analytics import composition_store as store
from analytics.composition_models import CompositionValidationError, resolve_preset


class PresetNotFound(KeyError):
    pass


def user_presets(r) -> List[dict]:
    return store.list_presets(r)


def list_all(r, kind: Optional[str] = None) -> List[dict]:
    items = store.list_presets(r)
    if kind in ("builtin",):
        return []
    return items


def get(r, preset_id: str) -> dict:
    p = store.get_preset(r, preset_id)
    if p is None:
        raise PresetNotFound(preset_id)
    return p


def create(r, body: Mapping) -> dict:
    return store.create_preset(r, body)


def update(r, preset_id: str, body: Mapping) -> dict:
    try:
        return store.update_preset(r, preset_id, body)
    except ValueError as exc:
        if isinstance(exc, CompositionValidationError):
            raise
        raise PresetNotFound(preset_id) from exc


def delete(r, preset_id: str) -> None:
    if not store.delete_preset(r, preset_id):
        raise PresetNotFound(preset_id)


def _deck_tag_names(r, registry_id: str) -> List[str]:
    import tags as tag_store
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
    from analytics import categories, insights
    try:
        return float(categories.commander_mv(dict(deck), insights.card_texts(r, dict(deck))))
    except Exception:  # noqa: BLE001
        return None


def resolve_for_deck(r, deck: Mapping, registry_id: str) -> dict:
    """Archetype-based auto-selection (spec section 6) for one deck. Read-only."""
    from analytics import journal as journal_mod
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


__all__ = ["PresetNotFound", "CompositionValidationError"]

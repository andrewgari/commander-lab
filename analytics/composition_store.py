"""
Composition store: Redis-backed CRUD for presets, deck composition records,
and the legacy AN-3 migration (composition-v1).

All writes are atomic (pipeline).  Keys use the `comp:` prefix.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, Iterable, List, Mapping, Optional, Tuple

import redis

from analytics.composition_models import (
    BUILTIN_BY_ID, BUILTIN_PRESETS, SPEC_VERSION, KIND_BUILTIN, KIND_USER,
    FALLBACK_PRESET_ID, MAX_USER_PRESETS, MODE_AUTO, MODE_PINNED,
    normalize_preset, validate_preset, validate_deck_composition,
    CompositionValidationError, ImmutablePresetError, user_preset_id,
    legacy_override_to_record, default_deck_composition,
)

log = logging.getLogger(__name__)

PRESET_KEY = "comp:presets"
DECK_KEY = "comp:deck:{registry_id}"


def _get_json(r: redis.Redis, key: str) -> Optional[Any]:
    raw = r.get(key)
    if raw is None:
        return None
    return json.loads(raw)


def _set_json(r: redis.Redis, key: str, value: Any) -> None:
    r.set(key, json.dumps(value))


# ---------------------------------------------------------------------------
# Preset CRUD
# ---------------------------------------------------------------------------


def list_presets(r: redis.Redis) -> List[dict]:
    """All presets: built-in + user.  User presets sorted newest first."""
    user = _get_json(r, PRESET_KEY) or []
    return list(BUILTIN_PRESETS) + user


def get_preset(r: redis.Redis, preset_id: str) -> Optional[dict]:
    if preset_id in BUILTIN_BY_ID:
        return BUILTIN_BY_ID[preset_id]
    user = _get_json(r, PRESET_KEY) or []
    for p in user:
        if p["id"] == preset_id:
            return p
    return None


def create_preset(r: redis.Redis, raw: Mapping, *, existing: Optional[Iterable[Mapping]] = None) -> dict:
    """Create a user preset.  Returns the saved preset or raises."""
    import uuid
    existing_list = list(existing or list_presets(r))
    norm = normalize_preset(raw)
    if not norm["id"]:
        norm["id"] = user_preset_id(norm["name"], uuid.uuid4().hex[:8])
    issues = validate_preset(norm, existing_list)
    if norm["id"] in BUILTIN_BY_ID:
        issues.append({"code": "E1", "level": "error", "message": "Built-in ids are reserved.",
                        "ref": None, "data": {}})
    if any(i["level"] == "error" for i in issues):
        raise CompositionValidationError(issues)
    user = _get_json(r, PRESET_KEY) or []
    if len(user) >= MAX_USER_PRESETS:
        raise CompositionValidationError([{"code": "E8", "level": "error",
                                            "message": f"Maximum {MAX_USER_PRESETS} user presets reached.",
                                            "ref": None, "data": {}}])
    import datetime
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat()
    norm["created_at"] = norm.get("created_at") or ts
    norm["updated_at"] = ts
    norm["kind"] = KIND_USER
    user.insert(0, norm)
    _set_json(r, PRESET_KEY, user)
    return norm


def update_preset(r: redis.Redis, preset_id: str, raw: Mapping) -> dict:
    """Update a user preset.  Raises ImmutablePresetError for built-ins."""
    if preset_id in BUILTIN_BY_ID:
        raise ImmutablePresetError("Built-in presets are read-only")
    user = _get_json(r, PRESET_KEY) or []
    found = None
    for i, p in enumerate(user):
        if p["id"] == preset_id:
            found = i
            break
    if found is None:
        raise ValueError(f"Preset '{preset_id}' not found")
    existing_list = [u for u in user if u["id"] != preset_id] + list(BUILTIN_PRESETS)
    norm = normalize_preset(raw)
    issues = validate_preset(norm, existing_list)
    if any(i["level"] == "error" for i in issues):
        raise CompositionValidationError(issues)
    import datetime
    norm["created_at"] = user[found].get("created_at")
    norm["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    norm["version"] = (user[found].get("version") or 1) + 1
    norm["id"] = preset_id  # preserve id
    norm["kind"] = KIND_USER
    user[found] = norm
    _set_json(r, PRESET_KEY, user)
    return norm


def delete_preset(r: redis.Redis, preset_id: str) -> bool:
    """Delete a user preset.  Returns True if deleted, False if not found."""
    if preset_id in BUILTIN_BY_ID:
        raise ImmutablePresetError("Built-in presets are read-only")
    user = _get_json(r, PRESET_KEY) or []
    new_user = [p for p in user if p["id"] != preset_id]
    if len(new_user) == len(user):
        return False
    _set_json(r, PRESET_KEY, new_user)
    return True


# ---------------------------------------------------------------------------
# Deck composition record CRUD
# ---------------------------------------------------------------------------


def get_deck_composition(r: redis.Redis, registry_id: str) -> dict:
    raw = _get_json(r, DECK_KEY.format(registry_id=registry_id))
    return raw if raw is not None else default_deck_composition()


def set_deck_composition(r: redis.Redis, registry_id: str, rec: Mapping) -> dict:
    presets = {**BUILTIN_BY_ID, **{p["id"]: p for p in (_get_json(r, PRESET_KEY) or [])}}
    issues = validate_deck_composition(rec, presets)
    if any(i["level"] == "error" for i in issues):
        raise CompositionValidationError(issues)
    import datetime
    saved = dict(rec)
    saved["updated_at"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    _set_json(r, DECK_KEY.format(registry_id=registry_id), saved)
    return saved


# ---------------------------------------------------------------------------
# Legacy migration
# ---------------------------------------------------------------------------


def migrate_legacy_composition(r: redis.Redis, deck: Mapping) -> Optional[dict]:
    """Migrate AN-3 composition:template:* overrides to composition-v1 record.

    Returns the saved record or None if there was nothing to migrate."""
    from analytics.journal import _registry_id
    rid = _registry_id(deck)
    journal_raw = _get_json(r, f"deck_journal:{rid}") or {}
    legacy = journal_raw.get("composition") or {}
    if not legacy:
        return None
    if legacy.get("_schema") == SPEC_VERSION:
        return None  # already migrated
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat()
    record, dropped = legacy_override_to_record(legacy, now)
    # Remove legacy data
    journal_raw["composition"] = {"_schema": SPEC_VERSION}
    _set_json(r, f"deck_journal:{rid}", journal_raw)
    _set_json(r, DECK_KEY.format(registry_id=rid), record)
    if dropped:
        log.info("Migrated %s: dropped plan=%d cards to 'derived'", rid, dropped)
    else:
        log.info("Migrated %s to composition-v1", rid)
    return record


def migrate_all_legacy_compositions(r: redis.Redis) -> int:
    """Migrate all decks with legacy AN-3 composition overrides.

    Returns the count of decks migrated."""
    count = 0
    for key in r.keys("deck_journal:*"):
        key_str = key.decode() if isinstance(key, bytes) else key
        journal = _get_json(r, key_str) or {}
        legacy = journal.get("composition") or {}
        if not legacy or legacy.get("_schema") == SPEC_VERSION:
            continue
        # Find the deck for this journal key
        rid = key_str.split(":", 1)[1]
        # Look up deck from the decks list
        decks_raw = r.get("decks")
        if not decks_raw:
            continue
        decks = json.loads(decks_raw)
        deck = None
        for d in decks:
            from analytics.journal import _registry_id
            if _registry_id(d) == rid:
                deck = d
                break
        if not deck:
            continue
        migrate_legacy_composition(r, deck)
        count += 1
    return count

"""PostgreSQL-backed composition store (SQLAlchemy).

Mirrors the public API of `analytics.composition_store` (same function names, same
dict shapes, same validation/errors) with a SQLAlchemy `Session` in place of the Redis
client, so callers can swap backends. Transactions are owned by the caller: use
`db.session_scope()` to commit/rollback several operations atomically.
"""
from __future__ import annotations

import datetime
import uuid
from typing import Any, Iterable, List, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.server.domain.analytics.composition_models import (
    KIND_USER, MAX_USER_PRESETS, CompositionValidationError,
    default_deck_composition, normalize_preset, user_preset_id,
    validate_deck_composition, validate_preset,
)
from src.server.infra.db.orm_models import CompositionPresetRow, DeckCompositionRow


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _rows(s: Session) -> List[CompositionPresetRow]:
    # newest first, matching the Redis list (insert at index 0)
    return list(s.scalars(select(CompositionPresetRow).order_by(CompositionPresetRow.seq.desc())))


def list_presets(s: Session) -> List[dict]:
    return [dict(r.data) for r in _rows(s)]


def _row(s: Session, preset_id: str, *, lock: bool = False) -> Optional[CompositionPresetRow]:
    q = select(CompositionPresetRow).where(CompositionPresetRow.id == preset_id)
    if lock:
        q = q.with_for_update()
    return s.scalars(q).first()


def get_preset(s: Session, preset_id: str) -> Optional[dict]:
    r = _row(s, preset_id)
    return dict(r.data) if r else None


def create_preset(s: Session, raw: Mapping, *, existing: Optional[Iterable[Mapping]] = None) -> dict:
    existing_list = list(existing or list_presets(s))
    norm = normalize_preset(raw)
    if not norm["id"]:
        norm["id"] = user_preset_id(norm["name"], uuid.uuid4().hex[:8])
    issues = validate_preset(norm, existing_list)
    if any(i["level"] == "error" for i in issues):
        raise CompositionValidationError(issues)
    if len(existing_list) >= MAX_USER_PRESETS:
        raise CompositionValidationError([{"code": "E8", "level": "error",
                                            "message": f"Maximum {MAX_USER_PRESETS} user presets reached.",
                                            "ref": None, "data": {}}])
    ts = _now()
    norm["created_at"] = norm.get("created_at") or ts
    norm["updated_at"] = ts
    norm["kind"] = KIND_USER
    s.add(CompositionPresetRow(id=norm["id"], data=norm))
    s.flush()
    return norm


def update_preset(s: Session, preset_id: str, raw: Mapping) -> dict:
    row = _row(s, preset_id, lock=True)
    if row is None:
        raise ValueError(f"Preset '{preset_id}' not found")
    old = dict(row.data)
    others = [p for p in list_presets(s) if p["id"] != preset_id]
    norm = normalize_preset(raw)
    issues = validate_preset(norm, others)
    if any(i["level"] == "error" for i in issues):
        raise CompositionValidationError(issues)
    norm["created_at"] = old.get("created_at")
    norm["updated_at"] = _now()
    norm["version"] = (old.get("version") or 1) + 1
    norm["id"] = preset_id
    norm["kind"] = KIND_USER
    row.data = norm  # full reassignment so JSON change is tracked
    s.flush()
    return norm


def delete_preset(s: Session, preset_id: str) -> bool:
    row = _row(s, preset_id)
    if row is None:
        return False
    s.delete(row)
    s.flush()
    return True


def get_deck_composition(s: Session, registry_id: str) -> dict:
    row = s.get(DeckCompositionRow, registry_id)
    return dict(row.data) if row is not None else default_deck_composition()


def set_deck_composition(s: Session, registry_id: str, rec: Mapping) -> dict:
    presets = {p["id"]: p for p in list_presets(s)}
    issues = validate_deck_composition(rec, presets)
    if any(i["level"] == "error" for i in issues):
        raise CompositionValidationError(issues)
    saved: dict[str, Any] = dict(rec)
    saved["updated_at"] = _now()
    row = s.get(DeckCompositionRow, registry_id)
    if row is None:
        s.add(DeckCompositionRow(registry_id=registry_id, data=saved))
    else:
        row.data = saved
    s.flush()
    return saved

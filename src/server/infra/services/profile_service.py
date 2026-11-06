"""Profile override write path (ARCHITECTURE.md §4.2, §8 Slice 2).

Every profile_overrides write also appends a profile_snapshots row
(trigger=override) so profile history is complete — the two writes commit in
one transaction, so history can never hold an override without its snapshot
or vice versa.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from src.server.domain.profile import overrides as overrides_mod
from src.server.infra.db import repositories as repo


def set_override(session: Session, key: str, value) -> dict:
    """Validate, store, and snapshot one override change.

    Raises overrides.OverrideError (a ValueError) on unknown keys or bad
    values. Returns {"override": row, "snapshot_id": id}.
    """
    normalized = overrides_mod.validate(key, value)
    row = repo.upsert_override(session, key, normalized)
    overrides = repo.get_overrides(session)
    latest = repo.latest_snapshot(session)
    snap = overrides_mod.override_snapshot(
        latest["metrics"] if latest else None, overrides, key
    )
    snapshot_id = repo.append_snapshot(
        session, "override", snap["inputs"], snap["metrics"], snap["summary_text"]
    )
    return {"override": row, "snapshot_id": snapshot_id}

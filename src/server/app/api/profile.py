"""Profile endpoints: latest snapshot, browsable history, and override
controls (Slice 2). Every override write appends a profile_snapshots row
(ARCHITECTURE.md §4.2)."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.server.app.api import schemas
from src.server.app.api.deps import db_session
from src.server.domain.profile import overrides as overrides_mod
from src.server.infra.db import repositories as repo
from src.server.infra.services import profile_service

router = APIRouter()


@router.get("/profile/latest", response_model=schemas.ProfileLatestResponse)
def profile_latest(session: Session = Depends(db_session)):
    snapshot = repo.latest_snapshot(session)
    if snapshot is None:
        return schemas.ProfileLatestResponse(
            available=False, reason="no profile snapshots yet", snapshot=None
        )
    return schemas.ProfileLatestResponse(
        available=True, reason=None, snapshot=schemas.ProfileSnapshot(**snapshot)
    )


@router.get("/profile/history", response_model=schemas.ProfileHistoryResponse)
def profile_history(limit: int = 50, session: Session = Depends(db_session)):
    """Profile snapshots, newest first — the browsable profile history."""
    limit = max(1, min(limit, 200))
    snapshots = repo.list_snapshots(session, limit=limit)
    return schemas.ProfileHistoryResponse(
        snapshots=[schemas.ProfileSnapshot(**s) for s in snapshots]
    )


@router.get("/profile/overrides", response_model=schemas.ProfileOverridesResponse)
def profile_overrides(session: Session = Depends(db_session)):
    rows = repo.get_overrides(session)
    return schemas.ProfileOverridesResponse(
        overrides={k: schemas.ProfileOverride(**v) for k, v in rows.items()},
        known_keys={k: label for k, (label, _v) in overrides_mod.OVERRIDE_KEYS.items()},
    )


@router.put("/profile/overrides/{key}", response_model=schemas.OverridePutResponse)
def put_profile_override(key: str, body: schemas.OverridePutRequest,
                         session: Session = Depends(db_session)):
    """Set (or clear, with null) one override. Always appends a snapshot."""
    try:
        result = profile_service.set_override(session, key, body.value)
    except overrides_mod.OverrideError as e:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_override", "reason": str(e)},
        )
    return schemas.OverridePutResponse(
        override=schemas.ProfileOverride(**result["override"]),
        snapshot_id=result["snapshot_id"],
    )

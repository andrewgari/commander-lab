"""Profile endpoint: the latest profile snapshot (Slice 1)."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from src.server.app.api import schemas
from src.server.app.api.deps import db_session
from src.server.infra.db import repositories as repo

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

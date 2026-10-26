"""Profile snapshot read model (Slice 1). Thin: repository → pydantic."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.server.app.api.deps import DbSession
from src.server.infra.db import repositories as repo

router = APIRouter(prefix="/api/v2/profile", tags=["profile-v2"])


class SnapshotOut(BaseModel):
    id: int
    created_at: Optional[str]
    trigger: str
    inputs: dict
    metrics: dict
    summary_text: str


class SnapshotListResponse(BaseModel):
    snapshots: list[SnapshotOut]


@router.get("/latest", response_model=SnapshotOut)
def latest_profile_snapshot(session: Session = DbSession) -> SnapshotOut:
    snap = repo.latest_snapshot(session)
    if snap is None:
        raise HTTPException(status_code=404, detail="no profile snapshots yet")
    return SnapshotOut(**snap)


@router.get("/snapshots", response_model=SnapshotListResponse)
def list_profile_snapshots(limit: int = 50, session: Session = DbSession) -> SnapshotListResponse:
    return SnapshotListResponse(
        snapshots=[SnapshotOut(**s) for s in repo.list_snapshots(session, limit=limit)]
    )

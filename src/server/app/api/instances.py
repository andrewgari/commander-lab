"""Card-instance read model (Slice 1). Thin: repository → pydantic."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.server.app.api.deps import DbSession
from src.server.infra.db import repositories as repo

router = APIRouter(prefix="/api/v2/instances", tags=["instances-v2"])


class InstanceOut(BaseModel):
    id: str
    oracle_id: str
    status: str
    deck_id: Optional[int]
    finish: str
    condition: str
    printing_id: Optional[str]
    price_paid: Optional[float]
    acquired_at: Optional[str]
    notes: Optional[str]


class InstanceListResponse(BaseModel):
    instances: list[InstanceOut]
    counts: dict[str, int]


@router.get("", response_model=InstanceListResponse)
def list_card_instances(status: Optional[str] = None, oracle_id: Optional[str] = None,
                        session: Session = DbSession) -> InstanceListResponse:
    return InstanceListResponse(
        instances=[InstanceOut(**i) for i in repo.list_instances(session, status, oracle_id)],
        counts=repo.instance_counts(session),
    )

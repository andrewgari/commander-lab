"""One-way Archidekt import endpoint (Slice 1). Thin router over the importer."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.server.app.api.deps import DbSession
from src.server.infra.providers import ProviderError
from src.server.workers import importer

router = APIRouter(prefix="/api/v2/imports", tags=["imports-v2"])


class ArchidektImportRequest(BaseModel):
    identifier: str = Field(..., description="Archidekt deck id or URL")
    snapshot: bool = True


class ArchidektImportResponse(BaseModel):
    deck_id: int
    archidekt_id: str
    name: str
    fingerprint: str | None
    deck_cards: int
    commanders: list[str]
    unresolved_cards: list[str]
    missing_oracle_ids: list[str]
    snapshot_id: int | None


@router.post("/archidekt", response_model=ArchidektImportResponse)
def import_archidekt(body: ArchidektImportRequest, session: Session = DbSession):
    try:
        return importer.import_deck(session, body.identifier, snapshot=body.snapshot)
    except importer.ImportError_ as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except ProviderError as exc:
        raise HTTPException(status_code=502, detail=f"archidekt: {exc}")

"""Card dossier API (docs/architecture/card-dossier-spec.md, adapted to the
/api/v2 layout): one aggregate GET feeds every panel of /card/{oracle_id};
PUT notes upserts Andrew's affinity/notes.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.server.app.api import schemas
from src.server.app.api.deps import db_session
from src.server.infra.services import dossier_service

router = APIRouter()


@router.get("/card/{oracle_id}/dossier", response_model=schemas.CardDossierResponse)
def card_dossier(
    oracle_id: str,
    printing: Optional[str] = Query(default=None),
    session: Session = Depends(db_session),
):
    dossier = dossier_service.get_dossier(session, oracle_id, printing=printing)
    if dossier is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "card_not_found",
                    "reason": f"no card with oracle_id {oracle_id!r}"},
        )
    return schemas.CardDossierResponse(**dossier)


@router.put("/card/{oracle_id}/notes", response_model=schemas.CardNotesResponse)
def put_card_notes(
    oracle_id: str,
    body: schemas.CardNotesPutRequest,
    session: Session = Depends(db_session),
):
    try:
        notes = dossier_service.put_notes(
            session, oracle_id, affinity=body.affinity, notes=body.notes
        )
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_affinity", "reason": str(e)},
        )
    if notes is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "card_not_found",
                    "reason": f"no card with oracle_id {oracle_id!r}"},
        )
    return schemas.CardNotesResponse(affinity=schemas.DossierAffinity(**notes))

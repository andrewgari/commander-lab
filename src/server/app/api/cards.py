"""Card dossier API (docs/architecture/card-dossier-spec.md, adapted to the
/api/v2 layout): one aggregate GET feeds every panel of /card/{oracle_id};
PUT notes upserts Andrew's affinity/notes.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from src.server.app.api import schemas
from src.server.app.api.deps import db_session
from src.server.infra.services import card_images, dossier_service

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


@router.get("/card-images", response_class=FileResponse)
def card_image(name: str = Query(..., min_length=1, max_length=card_images.MAX_NAME_LEN),
               size: str = Query("small")):
    """A card's Scryfall image from the local cache (fetched once, rate-limited, then served from disk).

    The review page and its PDF point every card ``<img>`` here (``cardImage`` in the web app), so a PDF renders
    with no network once the images are cached. 404 = Scryfall has no such card; 503 = not cached and Scryfall
    cannot be asked right now (rate limit, outage, or offline mode); the page shows its "Image unavailable" text."""
    try:
        path = card_images.get_cache().get(name, size)
    except card_images.ImageUnavailable as exc:
        raise HTTPException(status_code=exc.status, detail={"error": "image_unavailable", "reason": exc.reason})
    return FileResponse(path, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=86400, stale-while-revalidate=604800"})

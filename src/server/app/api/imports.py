"""One-way Archidekt import endpoint (Slice 1)."""
from __future__ import annotations

import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.server.app.api import schemas
from src.server.app.api.deps import db_session
from src.server.domain.decks.deck_import import DeckImportError
from src.server.infra.services import import_service

router = APIRouter()


@router.post("/imports/archidekt", response_model=schemas.ImportResponse)
def import_archidekt(body: schemas.ImportRequest, session: Session = Depends(db_session)):
    """Import one Archidekt deck (id or URL) into Postgres.

    One-way: nothing is ever pushed back to Archidekt. Idempotent: re-import
    of an unchanged deck rewrites the same rows and appends no snapshot.
    """
    try:
        # Bind the service's unit-of-work to the injected session's engine so
        # dependency overrides (tests) and the 503 guard in deps both apply.
        from sqlalchemy.orm import sessionmaker

        result = import_service.import_archidekt_deck(
            body.identifier, session_factory=sessionmaker(bind=session.get_bind())
        )
    except ValueError as e:
        if isinstance(e, DeckImportError):
            raise HTTPException(
                status_code=422,
                detail={"error": "import_rejected", "reason": str(e)},
            )
        raise HTTPException(
            status_code=400,
            detail={"error": "bad_identifier", "reason": str(e)},
        )
    except requests.HTTPError as e:
        status = e.response.status_code if e.response is not None else None
        raise HTTPException(
            status_code=502,
            detail={
                "error": "archidekt_unavailable",
                "reason": f"Archidekt returned HTTP {status or 'error'} for {body.identifier!r}",
            },
        )
    except requests.RequestException as e:
        raise HTTPException(
            status_code=502,
            detail={"error": "archidekt_unavailable", "reason": str(e)},
        )
    return schemas.ImportResponse(**result)

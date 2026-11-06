"""One-way import endpoints: Archidekt decks (Slice 1) and on-demand Forge
verbose-log game imports (Slice 4, ARCHITECTURE.md §0)."""
from __future__ import annotations

import datetime

import requests
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.server.app.api import schemas
from src.server.app.api.deps import db_session
from src.server.domain.decks.deck_import import DeckImportError
from src.server.infra.services import import_service
from src.server.workers import forge_import

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


@router.post("/imports/forge-log", response_model=schemas.ForgeLogImportResponse)
def import_forge_log_endpoint(body: schemas.ForgeLogImportRequest,
                              session: Session = Depends(db_session)):
    """Import one verbose Forge log against a deck (Slice 4, on demand only).

    Parses completed game blocks into game_imports/games/game_telemetry,
    writes deterministic analysis notes and appends a profile snapshot with
    trigger='game'. Idempotent: the sha256 of the (normalized) log is the
    fingerprint — re-importing the same log returns the existing import
    (duplicate=true) and writes nothing.
    """
    played_at = None
    if body.played_at:
        try:
            played_at = datetime.datetime.fromisoformat(body.played_at)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail={"error": "bad_played_at",
                        "reason": f"played_at is not ISO-8601: {body.played_at!r}"},
            )
    try:
        # Bind the worker's unit-of-work to the injected session's engine so
        # dependency overrides (tests) and the 503 guard in deps both apply.
        from sqlalchemy.orm import sessionmaker

        result = forge_import.import_forge_log(
            body.deck_id,
            body.log_text,
            session_factory=sessionmaker(bind=session.get_bind()),
            played_at=played_at,
            user_seat=body.seat,
        )
    except forge_import.ForgeImportError as e:
        raise HTTPException(
            status_code=404,
            detail={"error": "unknown_deck", "reason": str(e)},
        )
    if result["status"] == "failed":
        # The failure is recorded on the game_imports row (audit trail).
        raise HTTPException(
            status_code=422,
            detail={"error": "log_unparseable", "reason": result["error"],
                    "import_id": result["import_id"], "duplicate": result["duplicate"]},
        )
    return schemas.ForgeLogImportResponse(**result)

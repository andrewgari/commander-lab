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


# --- Player taste profile + feedback (deck reviewer card 04) ----------------
# Stored in Postgres (a009). Learned proposals never apply on their own:
# only POST .../proposals/{id}/confirm changes the profile.

from src.server.domain.profile import player_profile as pp  # noqa: E402
from src.server.infra.services import player_profile_service as pps  # noqa: E402


def _player_response(session: Session, name: str) -> schemas.PlayerProfileResponse:
    try:
        profile = pps.load_profile(session, name)
    except pp.ProfileNotFound as e:
        raise HTTPException(status_code=404, detail={"error": "profile_not_found", "reason": str(e)})
    data = profile.to_dict()
    prov = data.pop("provenance")
    return schemas.PlayerProfileResponse(
        name=name, profile=data, provenance=prov,
        versions=pps.list_versions(session, name),
        pending_proposals=pps.list_proposals(session, name),
    )


@router.get("/profile/player/{name}", response_model=schemas.PlayerProfileResponse)
def player_profile_get(name: str, session: Session = Depends(db_session)):
    return _player_response(session, name)


@router.post("/profile/player/{name}/seed", response_model=schemas.PlayerProfileResponse)
def player_profile_seed(name: str, session: Session = Depends(db_session)):
    """Create version 1 from profiles/<name>.json if absent (never overwrites)."""
    from pathlib import Path
    seed = Path(pps.DEFAULT_SEED).parent / f"{name}.json"
    if not seed.exists():
        raise HTTPException(status_code=404, detail={"error": "seed_not_found", "reason": f"no seed file for '{name}'"})
    pps.seed_profile(session, name, seed)
    return _player_response(session, name)


@router.put("/profile/player/{name}", response_model=schemas.PlayerProfileResponse)
def player_profile_put(name: str, body: schemas.PlayerProfilePutRequest,
                       session: Session = Depends(db_session)):
    try:
        pps.save_stated_edit(session, name, body.values)
    except pp.ProfileNotFound as e:
        raise HTTPException(status_code=404, detail={"error": "profile_not_found", "reason": str(e)})
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"error": "invalid_profile", "reason": str(e)})
    return _player_response(session, name)


@router.post("/profile/player/{name}/feedback")
def player_profile_feedback(name: str, body: schemas.FeedbackRequest,
                            session: Session = Depends(db_session)):
    try:
        pps.record_feedback(session, name, **body.model_dump())
    except ValueError as e:
        raise HTTPException(status_code=422, detail={"error": "invalid_feedback", "reason": str(e)})
    return {"ok": True}


@router.post("/profile/player/{name}/proposals/generate")
def player_profile_generate(name: str, session: Session = Depends(db_session)):
    try:
        return {"pending": pps.generate_proposals(session, name)}
    except pp.ProfileNotFound as e:
        raise HTTPException(status_code=404, detail={"error": "profile_not_found", "reason": str(e)})


@router.post("/profile/player/{name}/proposals/{proposal_id}/confirm",
             response_model=schemas.PlayerProfileResponse)
def player_profile_confirm(name: str, proposal_id: int, session: Session = Depends(db_session)):
    try:
        pps.confirm_proposal(session, name, proposal_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail={"error": "not_found", "reason": str(e)})
    except (pp.ProvenanceConflict, ValueError) as e:
        raise HTTPException(status_code=409, detail={"error": "cannot_confirm", "reason": str(e)})
    return _player_response(session, name)


@router.post("/profile/player/{name}/proposals/{proposal_id}/dismiss")
def player_profile_dismiss(name: str, proposal_id: int, session: Session = Depends(db_session)):
    try:
        pps.dismiss_proposal(session, name, proposal_id)
    except LookupError as e:
        raise HTTPException(status_code=404, detail={"error": "not_found", "reason": str(e)})
    except ValueError as e:
        raise HTTPException(status_code=409, detail={"error": "cannot_dismiss", "reason": str(e)})
    return {"ok": True}

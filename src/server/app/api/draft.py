"""Draft mode endpoints (Slice 5, ARCHITECTURE.md §7.8/§8): combine decks /
dismantle one to build another — staging-only until Commit.

Every write before Commit lands in the staging tables (drafts / draft_cards /
draft_instance_moves) only; `decks`, `deck_cards` and `card_instances` are
untouched. Commit applies the domain plan in ONE transaction (deck row +
deck_cards + instance moves + instance_events provenance with draft_id).
Abandon deletes every staging row (zero residue).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.server.app.api import schemas
from src.server.app.api.deps import db_session
from src.server.domain.draft import draft_mode, suggestions
from src.server.infra.db import repositories as repo

router = APIRouter()


def _draft_error(e: draft_mode.DraftError) -> HTTPException:
    msg = str(e)
    status = 404 if "not found" in msg.lower() else 409 if "not open" in msg.lower() else 400
    return HTTPException(status_code=status,
                         detail={"error": "draft_error", "reason": msg})


def _state(session: Session, draft_id: str) -> schemas.DraftStateResponse:
    state = repo.get_draft_state(session, draft_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "draft_not_found", "reason": f"no draft with id {draft_id}"},
        )
    cards = state.pop("cards")
    moves = state.pop("moves")
    return schemas.DraftStateResponse(
        draft=schemas.DraftRecord(**state),
        cards=[schemas.DraftCardLine(**c) for c in cards],
        moves=[schemas.DraftMoveLine(**m) for m in moves],
    )


@router.post("/drafts", response_model=schemas.DraftStateResponse, status_code=201)
def create_draft(req: schemas.DraftCreateRequest, session: Session = Depends(db_session)):
    """Open a draft session over 1+ source decks. Optionally prefill staging
    from the source lists (union; commander board wins). Canonical tables are
    NOT touched — the source decks keep their cards and copies."""
    try:
        source_ids = draft_mode.validate_create(
            req.kind, req.source_deck_ids, req.target_name)
    except draft_mode.DraftError as e:
        raise _draft_error(e)
    sources = []
    lists = {}
    for deck_id in source_ids:
        deck = repo.get_deck(session, deck_id)
        if deck is None:
            raise HTTPException(
                status_code=404,
                detail={"error": "deck_not_found",
                        "reason": f"no deck with id {deck_id}"},
            )
        sources.append({"id": deck["id"], "name": deck["name"]})
        lists[deck_id] = repo.get_deck_cards(session, deck_id)
    draft = repo.create_draft(session, {
        "id": draft_mode.new_draft_id(),
        "kind": req.kind,
        "name": req.name,
        "target_name": req.target_name.strip(),
        "source_decks": sources,
        "notes": req.notes,
    })
    if req.prefill:
        for card in draft_mode.prefill_cards(lists):
            repo.upsert_draft_card(session, draft["id"], card)
    return _state(session, draft["id"])


@router.get("/drafts", response_model=schemas.DraftListResponse)
def list_drafts(status: Optional[str] = None, limit: int = 50,
                session: Session = Depends(db_session)):
    """Draft sessions, newest first (optionally one status)."""
    return schemas.DraftListResponse(
        drafts=[schemas.DraftRecord(**d)
                for d in repo.list_drafts(session, status=status, limit=limit)]
    )


@router.get("/drafts/{draft_id}", response_model=schemas.DraftStateResponse)
def get_draft(draft_id: str, session: Session = Depends(db_session)):
    """The full staging view: draft row, staged cards, staged moves."""
    return _state(session, draft_id)


@router.post("/drafts/{draft_id}/cards", response_model=schemas.DraftStateResponse)
def upsert_card(draft_id: str, req: schemas.DraftCardUpsertRequest,
                session: Session = Depends(db_session)):
    """Add/update a staged card row. Staging only."""
    try:
        repo.upsert_draft_card(session, draft_id, req.model_dump())
    except draft_mode.DraftError as e:
        raise _draft_error(e)
    return _state(session, draft_id)


@router.delete("/drafts/{draft_id}/cards/{oracle_id}/{board}",
               response_model=schemas.DraftStateResponse)
def remove_card(draft_id: str, oracle_id: str, board: str,
                session: Session = Depends(db_session)):
    """Remove one staged card row. Staging only."""
    try:
        repo.remove_draft_card(session, draft_id, oracle_id, board)
    except draft_mode.DraftError as e:
        raise _draft_error(e)
    return _state(session, draft_id)


@router.post("/drafts/{draft_id}/moves", response_model=schemas.DraftStateResponse)
def stage_move(draft_id: str, req: schemas.DraftMoveRequest,
               session: Session = Depends(db_session)):
    """Stage a physical-copy move (assign into the result deck / unassign
    back to the collection). The instance itself is NOT touched until Commit."""
    try:
        repo.stage_move(session, draft_id, req.instance_id, req.action)
    except draft_mode.DraftError as e:
        raise _draft_error(e)
    return _state(session, draft_id)


@router.delete("/drafts/{draft_id}/moves/{instance_id}",
               response_model=schemas.DraftStateResponse)
def unstage_move(draft_id: str, instance_id: str,
                 session: Session = Depends(db_session)):
    """Drop a staged move."""
    try:
        repo.unstage_move(session, draft_id, instance_id)
    except draft_mode.DraftError as e:
        raise _draft_error(e)
    return _state(session, draft_id)


@router.get("/drafts/{draft_id}/suggestions",
            response_model=schemas.DraftSuggestionsResponse)
def get_suggestions(draft_id: str, limit: int = 10,
                    session: Session = Depends(db_session)):
    """Tuner weak-performer cuts + reconstruct tiered-pool adds for the staged
    list (own-first; donor pulls only when the donor is left no worse).
    Signals not yet in Postgres (edges, pillar fit, EDHREC, played-with) are
    reported in inputs_unavailable with reasons — never dressed up as 0."""
    state = repo.get_draft_state(session, draft_id)
    if state is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "draft_not_found", "reason": f"no draft with id {draft_id}"},
        )
    source_ids = [s["id"] for s in state["source_decks"]]
    source_lists = {d: repo.get_deck_cards(session, d) for d in source_ids}

    instances = repo.list_instances(session)
    collection_counts: dict = {}
    donor_counts: dict = {}
    for inst in instances:
        oid = inst["oracle_id"]
        if inst["status"] == "in_collection":
            collection_counts[oid] = collection_counts.get(oid, 0) + 1
        elif inst["status"] == "in_deck" and inst["deck_id"] not in source_ids:
            donor_counts.setdefault(oid, {})
            donor_counts[oid][inst["deck_id"]] = donor_counts[oid].get(inst["deck_id"], 0) + 1
    pools = suggestions.build_pools(source_lists, collection_counts, donor_counts)

    oracle_ids = set(pools) | {c["oracle_id"] for c in state["cards"]}
    facts = repo.get_cards(session, oracle_ids)
    # Target color identity: union of the staged list's identities.
    color_identity = sorted({
        col for c in state["cards"] for col in (c.get("color_identity") or [])
    })
    # No scoring signals live in Postgres yet (Slice 6 brings graph/vector);
    # every score is honestly unavailable rather than a dressed-up 0.
    scores = {oid: None for oid in pools}
    adds = suggestions.suggest_adds(
        pools, facts,
        draft_oracle_ids=[c["oracle_id"] for c in state["cards"]],
        color_identity=color_identity, scores=scores, limit=limit,
    )
    cuts = suggestions.weak_cuts(state["cards"], limit=limit)
    return schemas.DraftSuggestionsResponse(
        adds=[schemas.DraftSuggestionAdd(**a) for a in adds["adds"]],
        hidden_donor_pulls=adds["hidden_donor_pulls"],
        hidden_reason=adds["hidden_reason"],
        cuts=[schemas.DraftSuggestionCut(**c) for c in cuts["cuts"]],
        inputs_unavailable={**cuts["inputs_unavailable"],
                            "scores": "edges/fit/EDHREC/played-with signals land in Slice 6"},
        note=cuts["note"],
    )


@router.post("/drafts/{draft_id}/commit", response_model=schemas.DraftCommitResponse)
def commit_draft(draft_id: str, session: Session = Depends(db_session)):
    """Commit: apply the staging plan to the canonical tables in ONE
    transaction — new deck row + deck_cards + instance moves + instance_events
    provenance (kind=assigned|unassigned, from_deck/to_deck, draft_id).
    Any validation failure rolls everything back."""
    try:
        result = repo.commit_draft(session, draft_id)
    except draft_mode.DraftError as e:
        raise _draft_error(e)
    return schemas.DraftCommitResponse(**result)


@router.post("/drafts/{draft_id}/abandon", response_model=schemas.DraftAbandonResponse)
def abandon_draft(draft_id: str, session: Session = Depends(db_session)):
    """Abandon: delete every staging row for the draft. Zero residue."""
    try:
        result = repo.abandon_draft(session, draft_id)
    except draft_mode.DraftError as e:
        raise _draft_error(e)
    return schemas.DraftAbandonResponse(**result)


@router.get("/instances/{instance_id}/provenance",
            response_model=schemas.InstanceProvenanceResponse)
def get_provenance(instance_id: str, session: Session = Depends(db_session)):
    """The provenance path of one physical copy ("this copy moved A→B"),
    from its ordered instance_events (§4.2)."""
    path = repo.instance_provenance(session, instance_id)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "instance_not_found",
                    "reason": f"no instance with id {instance_id}"},
        )
    return schemas.InstanceProvenanceResponse(**path)

"""Reviews endpoints (Slice 3): build a stored review for one deck, list its
history, read one review back. Read-only GETs never build (REVIEW_SPEC:
rebuilds only on a Rebuild click).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.server.app.api import schemas
from src.server.app.api.deps import db_session
from src.server.domain.review import doc as doc_mod
from src.server.infra.db import repositories as repo
from src.server.infra.services import review_service

router = APIRouter()


def _response(review: dict, jobs: list | None = None) -> schemas.ReviewResponse:
    return schemas.ReviewResponse(
        **{k: review[k] for k in
           ("id", "deck_id", "version", "fingerprint", "status",
            "fact_pack", "analysis", "doc", "created_at")},
        # Dashboard panels are derived server-side from the SAME stored row the
        # document renders, so the two views can never show different numbers.
        panels=doc_mod.dashboard_panels(review["fact_pack"], review["analysis"]),
        jobs=[schemas.ReviewJobRecord(**j) for j in (jobs if jobs is not None else [])],
    )


@router.post("/decks/{deck_id}/reviews", response_model=schemas.ReviewResponse, status_code=201)
def build_review(deck_id: str, session: Session = Depends(db_session)):
    """Run the full pipeline and store a new versioned review. An offline
    LLM or unreachable provider degrades the review (status=partial, reason
    recorded per stage) — it never fails the build."""
    review = review_service.build_review(session, deck_id)
    if review is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "deck_not_found", "reason": f"no deck with id {deck_id!r}"},
        )
    jobs = review.pop("jobs", [])
    return _response(review, jobs)


@router.get("/decks/{deck_id}/reviews", response_model=schemas.ReviewListResponse)
def list_reviews(deck_id: str, limit: int = 20, session: Session = Depends(db_session)):
    """Version history (summaries), newest first."""
    if repo.get_deck(session, deck_id) is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "deck_not_found", "reason": f"no deck with id {deck_id!r}"},
        )
    return schemas.ReviewListResponse(
        reviews=[schemas.ReviewSummary(**r)
                 for r in repo.list_reviews_for_deck(session, deck_id, limit=limit)]
    )


@router.get("/decks/{deck_id}/reviews/latest", response_model=schemas.ReviewResponse)
def latest_review(deck_id: str, session: Session = Depends(db_session)):
    """The newest stored review (fact_pack + analysis + doc, one row)."""
    review = repo.latest_review_for_deck(session, deck_id)
    if review is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "review_not_found",
                    "reason": f"no review built for deck {deck_id!r} yet"},
        )
    return _response(review, repo.list_review_jobs(session, review["id"]))


@router.get("/reviews/{review_id}", response_model=schemas.ReviewResponse)
def get_review(review_id: int, session: Session = Depends(db_session)):
    """One stored review by id, with its pipeline audit trail."""
    review = repo.get_review(session, review_id)
    if review is None:
        raise HTTPException(
            status_code=404,
            detail={"error": "review_not_found", "reason": f"no review with id {review_id}"},
        )
    return _response(review, repo.list_review_jobs(session, review_id))

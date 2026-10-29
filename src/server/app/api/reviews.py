"""Reviews endpoints (Slice 3): build a stored review for one deck, list its
history, read one review back. Read-only GETs never build (REVIEW_SPEC:
rebuilds only on a Rebuild click).

Review documents (deck reviewer card 11): ``review_document.v1`` reviews are generated in the background
(``POST /decks/{id}/review-documents`` answers 202 at once), polled by id for visible progress, listed as history per
deck, exported as PDF through the separate ``pdf`` container, and get accept/reject/maybe feedback per swap. The
legacy ``/decks/{id}/reviews`` endpoints above are untouched (DECISIONS D9).
"""
from __future__ import annotations

from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.orm import Session

from src.server.app.api import schemas
from src.server.app.api.deps import db_session
from src.server.domain.review import doc as doc_mod
from src.server.infra.db import repositories as repo
from src.server.infra.db.session import get_session_factory
from src.server.infra.services import card_images, review_document_service as docs, review_pdf, review_service

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


# --- review_document.v1: generate, history, PDF, feedback (deck reviewer card 11) -----------------------------

def _not_found(kind: str, reason: str) -> HTTPException:
    return HTTPException(status_code=404, detail={"error": kind, "reason": reason})


def job_launcher() -> Callable[[int], None]:
    """Starts the background run for a queued document. A dependency so tests can swap in a scripted runner."""
    def launch(document_id: int) -> None:
        docs.start_job(document_id, session_factory=get_session_factory())
    return launch


@router.post("/decks/{deck_id}/review-documents", response_model=schemas.ReviewDocumentStartResponse, status_code=202)
def generate_review_document(deck_id: str, session: Session = Depends(db_session),
                             launch: Callable[[int], None] = Depends(job_launcher)):
    """Queue a new review and return immediately; poll ``GET /review-documents/{id}`` for ``stage`` and the result.
    A review already queued or running for this deck is returned instead of starting a second one."""
    if repo.get_deck(session, deck_id) is None:
        raise _not_found("deck_not_found", f"no deck with id {deck_id!r}")
    row, created = docs.request_review(session, deck_id)
    if created:
        session.commit()                       # the worker must see the row before it starts
        launch(row["id"])
    return schemas.ReviewDocumentStartResponse(**row, already_running=not created)


@router.get("/decks/{deck_id}/review-documents", response_model=schemas.ReviewDocumentListResponse)
def list_review_documents(deck_id: str, limit: int = Query(30, ge=1, le=200), session: Session = Depends(db_session)):
    """Review history for a deck, newest first (summaries: grade, headline, status), plus the running one if any."""
    if repo.get_deck(session, deck_id) is None:
        raise _not_found("deck_not_found", f"no deck with id {deck_id!r}")
    rows = docs.list_for_deck(session, deck_id, limit=limit)
    active = next((r for r in rows if r["status"] in docs.ACTIVE), None)
    return schemas.ReviewDocumentListResponse(documents=[schemas.ReviewDocumentSummary(**r) for r in rows],
                                              active=schemas.ReviewDocumentSummary(**active) if active else None)


@router.get("/decks/{deck_id}/review-documents/latest", response_model=schemas.ReviewDocumentResponse)
def latest_review_document(deck_id: str, session: Session = Depends(db_session)):
    row = docs.latest_for_deck(session, deck_id)
    if row is None:
        raise _not_found("review_not_found", f"no finished review for deck {deck_id!r} yet")
    return schemas.ReviewDocumentResponse(**row)


@router.get("/review-documents/{document_id}", response_model=schemas.ReviewDocumentResponse)
def get_review_document(document_id: int, session: Session = Depends(db_session)):
    row = docs.get_document(session, document_id)
    if row is None:
        raise _not_found("review_not_found", f"no review document with id {document_id}")
    return schemas.ReviewDocumentResponse(**row)


@router.get("/review-documents/{document_id}/print-view", response_model=schemas.ReviewDocumentResponse)
def print_review_document(document_id: int, token: str = Query(...), session: Session = Depends(db_session)):
    """The document for the print page the PDF container loads; needs the short-lived token minted for the export."""
    if not review_pdf.verify_token(document_id, token):
        raise HTTPException(status_code=403, detail={"error": "invalid_print_token",
                                                     "reason": "the print token is invalid or has expired"})
    return get_review_document(document_id, session)


def _ok_document(session: Session, document_id: int) -> dict:
    row = docs.get_document(session, document_id)
    if row is None:
        raise _not_found("review_not_found", f"no review document with id {document_id}")
    if row["status"] != "ok" or not row.get("doc"):
        raise HTTPException(status_code=409, detail={"error": "review_not_ready",
                                                     "reason": f"review {document_id} is {row['status']}, not finished"})
    return row


@router.get("/review-documents/{document_id}/pdf")
def review_document_pdf(document_id: int, session: Session = Depends(db_session)):
    """The review as a PDF: the page you see, printed by Chromium in the pdf container (DECISIONS D4). Card images are
    cached first, so the render itself needs no internet. 503 when the pdf container is not running."""
    row = _ok_document(session, document_id)
    session.commit()                          # release the connection before the (slow) warm-up and render
    names = review_pdf.card_names(row["doc"])
    card_images.get_cache().warm(names, "small")
    try:
        pdf = review_pdf.render(review_pdf.print_url(row["deck_id"], document_id))
    except review_pdf.PdfUnavailable as exc:
        raise HTTPException(status_code=exc.status, detail={"error": "pdf_unavailable", "reason": exc.reason})
    slug = "".join(ch if ch.isalnum() else "-" for ch in row["doc"]["meta"]["deck_name"]).strip("-").lower()[:60] or "review"
    return Response(content=pdf.data, media_type="application/pdf", headers={
        "Content-Disposition": f'attachment; filename="{slug}-review-{document_id}.pdf"',
        "X-Pdf-Pages": str(pdf.pages), "X-Missing-Images": str(pdf.missing_images)})


def _swap_feedback(session: Session, document_id: int, doc: dict) -> dict:
    by_pair = docs.feedback_for_document(session, document_id)
    out = {}
    for swap in doc.get("swaps") or []:
        hit = by_pair.get(f"{swap['cut']['name']}||{swap['add']['name']}")
        if hit:
            out[swap["id"]] = hit
    return out


@router.get("/review-documents/{document_id}/feedback", response_model=schemas.ReviewFeedbackResponse)
def review_document_feedback(document_id: int, session: Session = Depends(db_session)):
    row = _ok_document(session, document_id)
    return schemas.ReviewFeedbackResponse(feedback=_swap_feedback(session, document_id, row["doc"]))


@router.post("/review-documents/{document_id}/feedback", response_model=schemas.ReviewFeedbackResponse)
def post_review_document_feedback(document_id: int, body: schemas.ReviewSwapFeedbackRequest,
                                  session: Session = Depends(db_session)):
    """Record accept / reject / maybe (+ reason) for one swap in card 04's feedback store. The cut and add come from the
    stored document, not the client, so feedback always names the swap that was actually shown."""
    from src.server.infra.services import player_profile_service as pps

    row = _ok_document(session, document_id)
    swap = next((s for s in row["doc"].get("swaps") or [] if s.get("id") == body.swap_id), None)
    if swap is None:
        raise HTTPException(status_code=404, detail={"error": "swap_not_found",
                                                     "reason": f"review {document_id} has no swap {body.swap_id!r}"})
    try:
        pps.record_feedback(session, docs.DEFAULT_PROFILE, deck_id=row["deck_id"], review_id=str(document_id),
                            cut=swap["cut"]["name"], add=swap["add"]["name"], decision=body.decision, reason=body.reason)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail={"error": "invalid_feedback", "reason": str(exc)})
    session.flush()
    return schemas.ReviewFeedbackResponse(feedback=_swap_feedback(session, document_id, row["doc"]))

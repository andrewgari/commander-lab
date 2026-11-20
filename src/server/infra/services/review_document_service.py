"""Review documents (deck reviewer card 11): background generation, history, feedback state.

A review takes minutes (Passes A-E are 8+ model calls), so ``POST .../review-documents`` only inserts a ``queued``
row and returns; a worker thread runs the pipeline and updates ``stage`` as it goes (``running``), then stores the
schema-validated ``review_document.v1`` (``ok``) or the reason it failed (``failed``). Nothing here blocks a request
thread for longer than a couple of queries.

Everything that touches the network or a model is injected (``runner``), so tests drive the whole lifecycle offline.
The default runner (``LiveReviewRunner``) is the production composition of: deck + inventory + player profile from
Postgres -> fact pack and candidate pool (Scryfall, EDHREC, Spellbook) -> ``run_review`` Passes A-E over the
configured model providers -> ``review_document.v1``.
"""
from __future__ import annotations

import datetime as dt
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from src.server.infra.db.orm_models import RecommendationFeedbackRow, ReviewDocumentRow

logger = logging.getLogger(__name__)

ACTIVE = ("queued", "running")
DEFAULT_PROFILE = "andrew"
Progress = Callable[[str], None]


class JobFailed(RuntimeError):
    """The pipeline could not produce a document. ``trace`` (if any) is stored with the failed row."""

    def __init__(self, message: str, trace: Any = None):
        super().__init__(message)
        self.trace = trace


@dataclass
class RunnerResult:
    document: dict
    trace: Any = None


Runner = Callable[[str, Progress], RunnerResult]

_active_ids: set[int] = set()          # jobs this process is running; an ACTIVE row not in here was interrupted
_active_lock = threading.Lock()
_executor: Optional[ThreadPoolExecutor] = None
_executor_lock = threading.Lock()


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _iso(value: Optional[dt.datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def row_dict(row: ReviewDocumentRow, *, include_doc: bool = False) -> dict:
    out = {"id": row.id, "deck_id": row.deck_id, "status": row.status, "stage": row.stage or "",
           "schema_version": row.schema_version, "pipeline_version": row.pipeline_version,
           "fact_pack_hash": row.fact_pack_hash, "error": row.error,
           "created_at": _iso(row.created_at), "started_at": _iso(row.started_at), "finished_at": _iso(row.finished_at)}
    verdict = (row.doc or {}).get("verdict") or {}
    out["grade"], out["headline"] = verdict.get("grade"), verdict.get("headline")
    if include_doc:
        out["doc"] = row.doc
    return out


# ---- lifecycle ---------------------------------------------------------------------------------------------

def _reap_interrupted(session: Session, rows: list[ReviewDocumentRow]) -> None:
    """An ACTIVE row this process is not running was cut off by a restart: mark it failed instead of spinning forever."""
    with _active_lock:
        mine = set(_active_ids)
    for row in rows:
        if row.status in ACTIVE and row.id not in mine:
            row.status, row.stage = "failed", ""
            row.error = "The review was interrupted (the server restarted before it finished). Generate a new review."
            row.finished_at = _now()
    session.flush()


def request_review(session: Session, deck_id: str, *, owner_id: Optional[str] = None) -> tuple[dict, bool]:
    """Create a ``queued`` row for ``deck_id``. Returns ``(row, created)``; when a review for this deck is already
    queued or running that one is returned with ``created=False`` (one deck, one live run: no double spend)."""
    active = session.scalars(select(ReviewDocumentRow).where(
        ReviewDocumentRow.deck_id == deck_id, ReviewDocumentRow.status.in_(ACTIVE))).all()
    _reap_interrupted(session, list(active))
    for row in active:
        if row.status in ACTIVE:
            return row_dict(row), False
    row = ReviewDocumentRow(deck_id=deck_id, owner_id=owner_id, status="queued", stage="Waiting to start")
    session.add(row)
    session.flush()
    return row_dict(row), True


def get_document(session: Session, document_id: int, *, include_doc: bool = True) -> Optional[dict]:
    row = session.get(ReviewDocumentRow, document_id)
    if row is None:
        return None
    _reap_interrupted(session, [row])
    return row_dict(row, include_doc=include_doc)


def list_for_deck(session: Session, deck_id: str, *, limit: int = 50) -> list[dict]:
    rows = session.scalars(select(ReviewDocumentRow).where(ReviewDocumentRow.deck_id == deck_id)
                           .order_by(ReviewDocumentRow.id.desc()).limit(limit)).all()
    _reap_interrupted(session, list(rows))
    return [row_dict(r) for r in rows]


def latest_for_deck(session: Session, deck_id: str) -> Optional[dict]:
    """The newest finished review (``ok``) with its document, else None."""
    row = session.scalars(select(ReviewDocumentRow).where(
        ReviewDocumentRow.deck_id == deck_id, ReviewDocumentRow.status == "ok")
        .order_by(ReviewDocumentRow.id.desc()).limit(1)).first()
    return row_dict(row, include_doc=True) if row else None


def _update(factory: sessionmaker, document_id: int, **fields: Any) -> None:
    with factory() as session:
        row = session.get(ReviewDocumentRow, document_id)
        if row is None:
            return
        for key, value in fields.items():
            setattr(row, key, value)
        session.commit()


def run_job(document_id: int, *, session_factory: sessionmaker, runner: Runner) -> None:
    """The worker body. Never raises: a failure is recorded on the row (and its trace stored when there is one)."""
    from src.server.infra.services import review_trace_service

    with _active_lock:
        _active_ids.add(document_id)
    try:
        with session_factory() as session:
            row = session.get(ReviewDocumentRow, document_id)
            if row is None:
                return
            deck_id = row.deck_id
            row.status, row.stage, row.started_at = "running", "Starting", _now()
            session.commit()

        def progress(stage: str) -> None:
            try:
                _update(session_factory, document_id, stage=stage[:128])
            except Exception:  # noqa: BLE001 - a progress write must never fail the review
                logger.exception("review %s: could not record stage %r", document_id, stage)

        try:
            result = runner(deck_id, progress)
            doc = result.document
            run_id = None
            with session_factory() as session:
                if result.trace is not None:
                    run_id = review_trace_service.store_trace(session, result.trace, document_id=document_id)
                row = session.get(ReviewDocumentRow, document_id)
                row.status, row.stage, row.error = "ok", "", None
                row.doc, row.run_id = doc, run_id
                row.schema_version = doc.get("schema_version")
                row.pipeline_version = (doc.get("meta") or {}).get("pipeline_version")
                row.fact_pack_hash = (doc.get("meta") or {}).get("fact_pack_hash")
                row.finished_at = _now()
                session.commit()
        except Exception as exc:  # noqa: BLE001 - recorded on the row; the page shows the reason
            logger.exception("review %s failed", document_id)
            message = f"{type(exc).__name__}: {exc}"[:1000]
            run_id = None
            trace = getattr(exc, "trace", None)
            try:
                with session_factory() as session:
                    if trace is not None:
                        run_id = review_trace_service.store_trace(session, trace, document_id=document_id)
                    row = session.get(ReviewDocumentRow, document_id)
                    row.status, row.stage, row.error, row.run_id = "failed", "", message, run_id
                    row.finished_at = _now()
                    session.commit()
            except Exception:  # noqa: BLE001
                logger.exception("review %s: could not record the failure", document_id)
    finally:
        with _active_lock:
            _active_ids.discard(document_id)


def _pool() -> ThreadPoolExecutor:
    global _executor
    with _executor_lock:
        if _executor is None:
            _executor = ThreadPoolExecutor(max_workers=int(os.environ.get("REVIEW_JOB_WORKERS", "2")),
                                           thread_name_prefix="review-job")
        return _executor


def start_job(document_id: int, *, session_factory: sessionmaker, runner: Optional[Runner] = None,
              executor: Optional[ThreadPoolExecutor] = None) -> None:
    """Hand the job to a worker thread and return at once."""
    with _active_lock:
        _active_ids.add(document_id)              # visible before the thread starts, so a poll never reaps it
    runner = runner or LiveReviewRunner(session_factory)
    (executor or _pool()).submit(run_job, document_id, session_factory=session_factory, runner=runner)


# ---- feedback ----------------------------------------------------------------------------------------------

def feedback_for_document(session: Session, document_id: int, *, profile: str = DEFAULT_PROFILE) -> dict[str, dict]:
    """Latest decision per swap for this review, keyed ``"<cut>||<add>"`` (card 04's store, ``review_id`` = document id)."""
    rows = session.scalars(select(RecommendationFeedbackRow).where(
        RecommendationFeedbackRow.review_id == str(document_id), RecommendationFeedbackRow.profile == profile)
        .order_by(RecommendationFeedbackRow.id)).all()
    out: dict[str, dict] = {}
    for r in rows:
        out[f"{r.cut}||{r.add}"] = {"cut": r.cut, "add": r.add, "decision": r.decision, "reason": r.reason or ""}
    return out


# ---- the production runner -----------------------------------------------------------------------------------

STAGE_BY_PASS = {"A": "Reading the deck's plan (Pass A)", "B": "Analysing the deck (Pass B)",
                 "C": "Choosing swaps from the candidate pool (Pass C)", "critic": "Checking the findings (Pass D)",
                 "D": "Checking the findings (Pass D)", "E": "Writing the review (Pass E)"}


class _ProgressClient:
    """Wraps an LLMClient and reports the pass of each call as the review's visible stage."""

    def __init__(self, inner: Any, progress: Progress):
        self.inner, self._progress, self._last = inner, progress, None

    def complete(self, request: Any) -> Any:
        key = (getattr(request, "label", "") or "").split(":", 1)[0]
        if key and key != self._last and key in STAGE_BY_PASS:
            self._last = key
            self._progress(STAGE_BY_PASS[key])
        return self.inner.complete(request)


class LiveReviewRunner:
    """Deck + inventory + profile from Postgres -> providers -> ``run_review`` -> ``review_document.v1``.

    Configuration is environment only (no secrets in the repo): ``REVIEW_MODEL_A/B/C/E/CRITIC`` and the provider keys
    are documented in ``review_pipeline/config.py`` and ``infra/llm/provider.py``. A missing key fails the job with the
    provider's own setup hint, before any model call is made."""

    def __init__(self, session_factory: sessionmaker, *, env: Optional[Mapping[str, str]] = None,
                 client: Any = None, fetch_sources: Optional[Callable[..., Any]] = None,
                 profile_name: str = DEFAULT_PROFILE):
        self.session_factory, self.env = session_factory, (os.environ if env is None else env)
        self._client, self._fetch_sources, self.profile_name = client, fetch_sources, profile_name

    # -- inputs ------------------------------------------------------------------------------------------
    def _load_inputs(self, deck_id: str):
        from src.server.domain.review import candidates as review_candidates
        from src.server.domain.review import fact_pack as review_fact_pack
        from src.server.infra.db import repositories as repo
        from src.server.infra.services import player_profile_service as pps

        with self.session_factory() as session:
            deck_row = repo.get_deck(session, deck_id)
            if deck_row is None:
                raise JobFailed(f"no deck with id {deck_id!r}")
            deck_cards = repo.get_deck_cards(session, deck_id)
            instances = repo.list_instances(session)
            wanted = {c["oracle_id"] for c in deck_cards} | {i["oracle_id"] for i in instances if i.get("oracle_id")}
            cards_by_oracle = repo.get_cards(session, wanted)
            names_by_oracle = {oid: c["name"] for oid, c in cards_by_oracle.items()}
            deck_names = {d["id"]: d["name"] for d in repo.list_decks(session, include_archived=True)}
            owned = review_candidates.owned_index(instances, names_by_oracle, deck_names)
            try:
                profile = pps.load_profile(session, self.profile_name)
            except Exception as exc:  # noqa: BLE001 - e.g. ProfileNotFound
                raise JobFailed(f"player profile {self.profile_name!r} is not set up "
                                f"({type(exc).__name__}); open the Profile page to seed it") from exc
        deck = review_fact_pack.deck_for_review(deck_row, deck_cards)
        main = [{"name": c["name"], "quantity": c["quantity"]} for c in deck["cards"]]
        commanders = [{"name": n, "quantity": 1} for n in deck["commanders"]]
        review_deck = {"commanders": commanders, "cards": commanders + main}
        deck_cards_q = [(c["name"], c["quantity"]) for c in review_deck["cards"]]
        return deck_row, review_deck, deck_cards_q, owned, profile

    def _sources(self, review_deck: Mapping[str, Any], intent: Any, profile: Any, owned: Any):
        if self._fetch_sources:
            return self._fetch_sources(review_deck, intent, profile, owned)
        from src.server.domain.analytics import candidates
        from src.server.domain.analytics.fact_pack import build_fact_pack
        from src.server.domain.analytics.fact_sources import default_fetchers, scryfall_search
        from src.server.domain.analytics.providers.edhrec import EDHRecClient

        pack = build_fact_pack(review_deck)
        fetchers = default_fetchers()
        pool = candidates.candidate_pool(
            review_deck, intent, profile, owned=owned or None,
            sources=candidates.CandidateSources(edhrec=lambda names: EDHRecClient().get_commander_page(list(names)),
                                                scryfall_batch=fetchers["scryfall_batch"], scryfall_search=scryfall_search))
        no_identity = [c["name"] for c in pack["cards"] if isinstance(c.get("color_identity"), dict)]
        if not pool.cards or len(no_identity) > max(6, len(pack["cards"]) // 10):
            raise JobFailed(f"card data is incomplete (candidate pool has {len(pool.cards)} cards; {len(no_identity)} deck "
                            f"cards have no Scryfall color identity). Scryfall or EDHREC is rate-limiting or down: "
                            f"try again in a few minutes. Notes: {'; '.join(pool.notes)[:300]}")
        return pack, pool

    def _llm(self, config: Any, writer_model: str):
        from src.server.infra.llm import provider

        specs = [*(config.model(p) for p in ("A", "B", "C", "critic")), writer_model, *config.axis_models.values()]
        status = provider.provider_status(self.env)
        for spec in dict.fromkeys(specs):
            if not any(status.get(c.split(":", 1)[0]) is None for c in provider.fallback_chain(spec)):
                prov = spec.split(":", 1)[0]
                raise JobFailed(f"no model provider is set up for {spec!r}: {status.get(prov) or 'unknown provider'}")
        return self._client or provider.FallbackClient(provider.RoutingClient.from_env(self.env))

    # -- run ---------------------------------------------------------------------------------------------
    def __call__(self, deck_id: str, progress: Progress) -> RunnerResult:
        from src.server.domain.analytics.review_pipeline import bridge, config as cfg, context, runner
        from src.server.domain.intake.user_intent import UserDeckIntent

        progress("Loading the deck, your collection and your profile")
        deck_row, review_deck, deck_cards, owned, profile = self._load_inputs(deck_id)
        config = cfg.PipelineConfig.from_env(self.env)
        writer_model = self.env.get("REVIEW_MODEL_E") or config.model("C")
        client = self._llm(config, writer_model)
        intent = UserDeckIntent()
        progress("Collecting card facts, combos and candidate swaps")
        fact_pack, pool = self._sources(review_deck, intent, profile, owned)
        ctx = context.ReviewContext.build(
            deck_id=deck_id, deck_name=deck_row["name"], fact_pack=fact_pack, deck_cards=deck_cards,
            commanders=[c["name"] for c in review_deck["commanders"]], profile=profile, intent=intent,
            owned=owned or None, extra_foreign_names=[c["name"] for c in pool.cards])
        rp = getattr(profile, "review_preferences", None)
        prefs = ", ".join(str(p) for p in (getattr(rp, "tone", None), *(getattr(rp, "skip", None) or [])) if p)
        metered = _ProgressClient(client, progress)
        traces: list = []
        sink: dict[str, Any] = {}
        try:
            review = runner.run_review(
                ctx=ctx, fact_pack=fact_pack, pool=pool, client=metered, config=config, on_trace=traces.append,
                writer=bridge.make_live_writer(metered, writer_model, prefs), run_passes=bridge.make_run_passes(sink))
            review = bridge.attach_questions(review, sink.get("questions", []))
        except runner.ReviewRunFailed as exc:
            raise JobFailed(str(exc), trace=exc.trace) from exc
        review["meta"]["models"]["E"] = writer_model
        return RunnerResult(review, traces[-1] if traces else None)

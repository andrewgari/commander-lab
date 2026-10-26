"""Slice 3 review-pipeline tables (ARCHITECTURE.md §4.2, §8 Slice 3).

Creates the canonical stored-review model:

- `reviews` — one row per built review. `fact_pack` holds the deterministic
  numbers (code only, §6), `analysis` the candidate-retrieval /
  recommendation output, and `doc` the typed sections (the Theory-doc
  pattern). The styled document and the dashboard both render from
  `fact_pack` + `doc` of the SAME row, so they can never disagree.
  `fingerprint` (sha256 of the decklist) is what "rebuild twice on an
  unchanged deck -> identical facts" is checked against. `status` is
  `partial` when any section is degraded (LLM offline, a provider
  unreachable, card names rejected by the verifier) — the review still
  renders; the reason lives inside the affected section.
- `review_jobs` — pipeline audit trail: one row per stage
  (fact_pack / candidates / writer / verifier / store) with its outcome.

Sequenced after a006_merge_heads (the merge point for a005_draft_mode and
a004_slice2_profile_overrides), keeping a single alembic head.

Idempotent (CREATE TABLE IF NOT EXISTS), same convention as a001-a004.
Enum vocabularies are CHECK constraints (portable across PostgreSQL and the
SQLite used by unit tests):
  review status: complete | partial
"""
from __future__ import annotations

# NOTE: alembic is imported lazily inside upgrade()/downgrade() so this module
# doubles as a plain DDL source for tests in environments where alembic is not
# installed (CI unit-test job installs requirements.txt only).

REVIEWS_DDL = """
CREATE TABLE IF NOT EXISTS reviews (
    id          SERIAL PRIMARY KEY,
    deck_id     VARCHAR(255) NOT NULL REFERENCES decks(id),
    version     INTEGER NOT NULL,                -- per-deck 1..N, assigned at insert
    fingerprint VARCHAR(64) NOT NULL,            -- sha256 of the decklist (review_metrics.fingerprint)
    status      VARCHAR(8) NOT NULL
                CHECK (status IN ('complete','partial')),
    fact_pack   JSONB NOT NULL,                  -- deterministic code-pass output (§6)
    analysis    JSONB NOT NULL,                  -- candidates + ranked adds/cuts + verifier audit
    doc         JSONB NOT NULL,                  -- typed sections; document + dashboard render from this
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_id    VARCHAR(255)
)
"""
REVIEWS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_reviews_deck ON reviews (deck_id, id)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_reviews_deck_version ON reviews (deck_id, version)",
)

REVIEW_JOBS_DDL = """
CREATE TABLE IF NOT EXISTS review_jobs (
    id         SERIAL PRIMARY KEY,
    review_id  INTEGER NOT NULL REFERENCES reviews(id),
    stage      VARCHAR(32) NOT NULL,             -- fact_pack | candidates | writer | verifier | store
    status     VARCHAR(16) NOT NULL,             -- ok | degraded | failed
    error      TEXT,
    ts         TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_id   VARCHAR(255)
)
"""
REVIEW_JOBS_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_review_jobs_review ON review_jobs (review_id)",
)

revision = "a007_create_reviews"
down_revision = "a006_merge_heads"


def upgrade() -> None:
    from alembic import op

    op.execute(REVIEWS_DDL)
    for stmt in REVIEWS_INDEXES:
        op.execute(stmt)
    op.execute(REVIEW_JOBS_DDL)
    for stmt in REVIEW_JOBS_INDEXES:
        op.execute(stmt)


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS review_jobs")
    op.execute("DROP TABLE IF EXISTS reviews")

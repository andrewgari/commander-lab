"""Review run trace tables (deck reviewer card 08; DECISIONS.md D2).

- ``review_runs``      — one row per pipeline run: models, prompt versions, inputs hashes, cost, status.
- ``review_run_steps`` — one row per model call/attempt: input hash, prompt version, model, request,
  raw response, tokens, latency, cost, verdict. ``drops`` and ``finding_steps`` live on the run so
  "which prompt version and model produced finding X" is answerable from the database alone.

``document_id`` is a plain nullable integer: ``review_documents`` does not exist yet, so the FK is
added when it lands. Idempotent DDL, same convention as a001–a009.
"""
from __future__ import annotations

RUNS_DDL = """
CREATE TABLE IF NOT EXISTS review_runs (
    id              SERIAL PRIMARY KEY,
    owner_id        VARCHAR(255),
    document_id     INTEGER,
    deck_id         VARCHAR(255) NOT NULL,
    started_at      VARCHAR(40) NOT NULL,
    finished_at     VARCHAR(40),
    status          VARCHAR(16) NOT NULL CHECK (status IN ('running','ok','failed')),
    models          JSONB NOT NULL DEFAULT '{}',
    prompt_versions JSONB NOT NULL DEFAULT '{}',
    inputs          JSONB NOT NULL DEFAULT '{}',
    drops           JSONB NOT NULL DEFAULT '[]',
    finding_steps   JSONB NOT NULL DEFAULT '{}',
    cost_usd        DOUBLE PRECISION,
    cost_complete   BOOLEAN NOT NULL DEFAULT FALSE,
    error           TEXT
)
"""

RUNS_INDEX_DDL = "CREATE INDEX IF NOT EXISTS ix_review_runs_deck ON review_runs (deck_id)"

STEPS_DDL = """
CREATE TABLE IF NOT EXISTS review_run_steps (
    id                 SERIAL PRIMARY KEY,
    run_id             INTEGER NOT NULL REFERENCES review_runs(id) ON DELETE CASCADE,
    idx                INTEGER NOT NULL,
    pass               VARCHAR(16) NOT NULL,
    axis               VARCHAR(64),
    attempt            INTEGER NOT NULL,
    model              VARCHAR(128) NOT NULL,
    prompt_version     VARCHAR(64) NOT NULL,
    input_hash         VARCHAR(64) NOT NULL,
    request            JSONB NOT NULL,
    response           JSONB,
    tokens_in          INTEGER,
    tokens_out         INTEGER,
    cached_tokens      INTEGER,
    latency_ms         INTEGER,
    cost_usd           DOUBLE PRECISION,
    verdict            VARCHAR(16) NOT NULL,
    error              TEXT,
    UNIQUE (run_id, idx)
)
"""

ALL_DDLS = [RUNS_DDL, RUNS_INDEX_DDL, STEPS_DDL]

revision = "a010_review_runs"
down_revision = "a009_player_profile"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    for stmt in ALL_DDLS:
        op.execute(stmt)


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS review_run_steps")
    op.execute("DROP TABLE IF EXISTS review_runs")

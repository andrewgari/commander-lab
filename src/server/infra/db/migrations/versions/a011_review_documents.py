"""Stored review_document v1 rows (deck reviewer card 11; DECISIONS.md D2).

``review_documents`` is both the job record and the stored document: a row is created ``queued`` when a
review is requested, moves ``running`` -> ``ok`` | ``failed`` as the pipeline runs in the background, and
holds the schema-validated ``review_document.v1`` JSON in ``doc`` once ``ok``. ``stage`` is the human-readable
progress label the page shows while the job runs. Rows are append-only history: a new review is a new row.

``review_runs.document_id`` (a010) is a plain integer; it now points at this table by convention (no FK, so a
trace outlives a deleted document). Idempotent DDL, same convention as a001-a010.
"""
from __future__ import annotations

DOCUMENTS_DDL = """
CREATE TABLE IF NOT EXISTS review_documents (
    id               SERIAL PRIMARY KEY,
    owner_id         VARCHAR(255),
    deck_id          VARCHAR(255) NOT NULL,
    status           VARCHAR(16) NOT NULL CHECK (status IN ('queued','running','ok','failed')),
    stage            VARCHAR(128) NOT NULL DEFAULT '',
    schema_version   VARCHAR(64),
    pipeline_version VARCHAR(64),
    fact_pack_hash   VARCHAR(128),
    run_id           INTEGER,
    doc              JSONB,
    error            TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    finished_at      TIMESTAMPTZ
)
"""

DOCUMENTS_INDEX_DDL = "CREATE INDEX IF NOT EXISTS ix_review_documents_deck ON review_documents (deck_id, id DESC)"

ALL_DDLS = [DOCUMENTS_DDL, DOCUMENTS_INDEX_DDL]

revision = "a011_review_documents"
down_revision = "a010_review_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    for stmt in ALL_DDLS:
        op.execute(stmt)


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS review_documents")

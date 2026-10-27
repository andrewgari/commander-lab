"""STA-11 work package B: per-deck owner-stated intent table.

`deck_intents` holds one sparse UserDeckIntent payload per deck (Postgres is
the only source of truth — AGENT_RULES rule 1). An absent row means the
deck's intent is unstated; stored payloads are stated-only (no default
backfill) so unstated fields stay visibly empty (discovery SRC-07).

Idempotent (CREATE TABLE IF NOT EXISTS), same convention as a001–a010.
"""
from __future__ import annotations

# NOTE: alembic is imported lazily inside upgrade()/downgrade() so this module
# doubles as a plain DDL source for tests in environments where alembic is not
# installed (CI unit-test job installs requirements.txt only).

DECK_INTENTS_DDL = """
CREATE TABLE IF NOT EXISTS deck_intents (
    deck_id     VARCHAR(255) PRIMARY KEY REFERENCES decks(id),
    payload     JSONB NOT NULL DEFAULT '{}',
    provenance  VARCHAR(16) NOT NULL DEFAULT 'stated'
                CHECK (provenance IN ('stated')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_id    VARCHAR(255)
)
"""

# revision identifiers, used by Alembic.
revision = "a011_deck_intents"
down_revision = "a010_review_runs"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    op.execute(DECK_INTENTS_DDL)


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS deck_intents")

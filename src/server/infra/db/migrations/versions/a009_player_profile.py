"""Player profile + feedback tables (deck reviewer card 04; DECISIONS.md D2).

- ``player_profile_versions`` — append-only history; current = max(version) per
  (owner, name). ``data`` is validated by player_profile.schema.json and
  carries per-leaf provenance.
- ``recommendation_feedback`` — the owner's accept/reject/maybe on each swap of
  a review, keyed by deck, review id and card names (D2 sketched an FK to
  ``review_documents``/oracle ids; that table does not exist yet, so names are
  stored directly and the document link can be added when it lands).
- ``player_profile_proposals`` — learned preferences proposed by the
  summarizer, awaiting confirmation. Nothing here touches the profile.

Idempotent DDL, same convention as a001–a008. alembic is imported lazily.
"""
from __future__ import annotations

PROFILE_VERSIONS_DDL = """
CREATE TABLE IF NOT EXISTS player_profile_versions (
    id            SERIAL PRIMARY KEY,
    owner_id      VARCHAR(255),
    name          VARCHAR(64) NOT NULL,
    version       INTEGER NOT NULL,
    kind          VARCHAR(16) NOT NULL CHECK (kind IN ('inferred','override','merged')),
    data          JSONB NOT NULL,
    provenance    JSONB NOT NULL DEFAULT '{}',
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    supersedes_id INTEGER REFERENCES player_profile_versions(id),
    UNIQUE (name, version)
)
"""

FEEDBACK_DDL = """
CREATE TABLE IF NOT EXISTS recommendation_feedback (
    id         SERIAL PRIMARY KEY,
    owner_id   VARCHAR(255),
    profile    VARCHAR(64) NOT NULL,
    deck_id    VARCHAR(255) NOT NULL,
    review_id  VARCHAR(255) NOT NULL,
    cut        VARCHAR(255) NOT NULL,
    "add"      VARCHAR(255) NOT NULL,
    decision   VARCHAR(16) NOT NULL CHECK (decision IN ('accepted','rejected','maybe')),
    reason     TEXT NOT NULL DEFAULT '',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

FEEDBACK_INDEX_DDL = """
CREATE INDEX IF NOT EXISTS ix_recommendation_feedback_deck
    ON recommendation_feedback (profile, deck_id)
"""

PROPOSALS_DDL = """
CREATE TABLE IF NOT EXISTS player_profile_proposals (
    id          SERIAL PRIMARY KEY,
    owner_id    VARCHAR(255),
    profile     VARCHAR(64) NOT NULL,
    path        VARCHAR(128) NOT NULL,
    op          VARCHAR(8) NOT NULL CHECK (op IN ('add','set')),
    value       JSONB NOT NULL,
    rationale   TEXT NOT NULL DEFAULT '',
    evidence    JSONB NOT NULL DEFAULT '[]',
    status      VARCHAR(16) NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','confirmed','dismissed')),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ
)
"""

ALL_DDLS = [PROFILE_VERSIONS_DDL, FEEDBACK_DDL, FEEDBACK_INDEX_DDL, PROPOSALS_DDL]

revision = "a009_player_profile"
down_revision = "a008_card_dossier"
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    for stmt in ALL_DDLS:
        op.execute(stmt)


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS player_profile_proposals")
    op.execute("DROP TABLE IF EXISTS recommendation_feedback")
    op.execute("DROP TABLE IF EXISTS player_profile_versions")

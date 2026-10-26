"""Create the games and play_style_profile tables (Forge auto-recorder).

Idempotent (CREATE TABLE IF NOT EXISTS), same convention as a001, so it can be
applied to a database that already carries tables created by
``Base.metadata.create_all`` and to a fresh PostgreSQL deploy alike.
"""
from __future__ import annotations

# NOTE: alembic is imported lazily inside upgrade()/downgrade() so this module
# doubles as a plain DDL source for tests in environments where alembic is not
# installed (CI unit-test job installs requirements.txt only).

# --- games ---------------------------------------------------------------
GAMES_DDL = """
CREATE TABLE IF NOT EXISTS games (
    id            SERIAL PRIMARY KEY,
    user_id       VARCHAR(255) NOT NULL REFERENCES users(id),
    game_uid      VARCHAR(64) NOT NULL UNIQUE,      -- content hash; idempotent ingest
    source        VARCHAR(32) NOT NULL DEFAULT 'forge',
    played_at     TIMESTAMPTZ,
    players       JSONB NOT NULL,
    winner        VARCHAR(255),
    turns         INTEGER NOT NULL DEFAULT 0,
    is_simulation BOOLEAN NOT NULL DEFAULT FALSE,
    summary       JSONB NOT NULL,
    events        JSONB NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# --- play_style_profile ---------------------------------------------------
PLAY_STYLE_PROFILE_DDL = """
CREATE TABLE IF NOT EXISTS play_style_profile (
    user_id         VARCHAR(255) NOT NULL REFERENCES users(id) PRIMARY KEY,
    games_analyzed  INTEGER NOT NULL DEFAULT 0,
    profile         JSONB NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

revision = "a002_create_games_play_style"
down_revision = "a001_create_users_deck_links"


def upgrade() -> None:
    from alembic import op

    op.execute(GAMES_DDL)
    op.execute(PLAY_STYLE_PROFILE_DDL)


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS play_style_profile")
    op.execute("DROP TABLE IF EXISTS games")

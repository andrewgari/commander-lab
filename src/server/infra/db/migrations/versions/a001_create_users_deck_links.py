"""Create the users and deck_links tables (Phase 1b).

This migration is idempotent (CREATE TABLE IF NOT EXISTS) so it can be applied
to a database that already carries tables created by ``Base.metadata.create_all``
during development, and to a fresh PostgreSQL deploy alike.
"""
from __future__ import annotations

# NOTE: alembic is imported lazily inside upgrade()/downgrade() so this module
# doubles as a plain DDL source (USERS_DDL / DECK_LINKS_DDL / ...) for the
# importer, the cutover verifier and tests in environments where alembic is
# not installed (e.g. CI unit-test job, which installs requirements.txt only).

# --- users ---------------------------------------------------------------
USERS_DDL = """
CREATE TABLE IF NOT EXISTS users (
    id                VARCHAR(255) NOT NULL PRIMARY KEY,
    name              VARCHAR(255) NOT NULL,
    archidekt_user_id VARCHAR(255) NOT NULL,
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

# --- deck_links ----------------------------------------------------------
DECK_LINKS_DDL = """
CREATE TABLE IF NOT EXISTS deck_links (
    id                    SERIAL PRIMARY KEY,
    user_id               VARCHAR(255) NOT NULL REFERENCES users(id),
    deck_id               VARCHAR(255),                       -- local registry id; nullable for orphan links
    provider              VARCHAR(32) NOT NULL,               -- archidekt | moxfield
    external_id           VARCHAR(255) NOT NULL,              -- provider-native deck id
    url                   VARCHAR(512) NOT NULL,
    folder_id             INTEGER,                            -- Archidekt parentFolderId
    name_at_source        VARCHAR(255) NOT NULL,
    commander_at_source   VARCHAR(255),
    match_method          VARCHAR(64) NOT NULL,
    last_synced_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    upstream_missing_at   TIMESTAMPTZ,
    raw                   JSONB NOT NULL,
    CONSTRAINT uq_deck_links_provider_external_id UNIQUE (provider, external_id)
)
"""

# --- existing tables kept for a complete, idempotent schema -------------
COMPOSITION_PRESETS_DDL = """
CREATE TABLE IF NOT EXISTS composition_presets (
    seq       SERIAL PRIMARY KEY,
    id        VARCHAR(255) NOT NULL UNIQUE,
    data      JSONB NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

DECK_COMPOSITIONS_DDL = """
CREATE TABLE IF NOT EXISTS deck_compositions (
    registry_id VARCHAR(255) NOT NULL PRIMARY KEY,
    data        JSONB NOT NULL,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""

revision = "a001_create_users_deck_links"
down_revision = None


def upgrade() -> None:
    from alembic import op

    op.execute(USERS_DDL)
    op.execute(DECK_LINKS_DDL)
    op.execute(COMPOSITION_PRESETS_DDL)
    op.execute(DECK_COMPOSITIONS_DDL)


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS deck_links")
    op.execute("DROP TABLE IF EXISTS users")


"""Create the Slice 2 profile_overrides table (ARCHITECTURE.md §4.2).

``profile_overrides(key PK, value jsonb, set_at)`` holds user steering over
the computed profile; every override write also appends a profile_snapshots
row (append-only history stays complete — that rule lives in the service
layer, the table itself is just the key/value store).

Idempotent (CREATE TABLE IF NOT EXISTS), same convention as a001-a003, so it
applies cleanly over ``Base.metadata.create_all`` databases and fresh
PostgreSQL deploys alike.
"""

PROFILE_OVERRIDES_DDL = """
CREATE TABLE IF NOT EXISTS profile_overrides (
    key         VARCHAR(64) PRIMARY KEY,        -- e.g. power_level_target
    value       JSONB NOT NULL,                 -- typed per key (domain/profile/overrides.py)
    set_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    owner_id    VARCHAR(255)
)
"""

revision = "a004_slice2_profile_overrides"
down_revision = "a003_slice1_core"


def upgrade() -> None:
    from alembic import op

    op.execute(PROFILE_OVERRIDES_DDL)


def downgrade() -> None:
    from alembic import op

    op.execute("DROP TABLE IF EXISTS profile_overrides")

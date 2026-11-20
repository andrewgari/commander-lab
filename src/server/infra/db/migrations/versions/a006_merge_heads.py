"""Merge heads: unify a005_draft_mode and a004_slice2_profile_overrides.

Slices 2 and 4 both forked off a003_slice1_core, leaving two alembic heads.
This merge revision is a no-op (empty upgrade/downgrade) whose only job is to
give the graph a single head so the documented cutover command
``DATABASE_URL=... alembic upgrade head`` (docs/redis-postgres-cutover.md §2)
works again.

Revision ID: a006_merge_heads
Revises: a005_draft_mode, a004_slice2_profile_overrides
Create Date: 2026-10-26
"""

# revision identifiers, used by Alembic.
revision = "a006_merge_heads"
down_revision = ("a005_draft_mode", "a004_slice2_profile_overrides")
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Merge point only — both parent migrations are idempotent and already
    # create their tables; nothing to do here.
    pass


def downgrade() -> None:
    # Nothing to undo for the merge point itself.
    pass

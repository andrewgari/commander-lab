"""Move app tables, sequences and enum types from ag_catalog to public.

The dev database's search_path leads with ``ag_catalog`` (docker/db/init/00-extensions.sql), so
earlier migrations created every app object there while CI (plain postgres) used ``public``.
This relocates whatever app object is in ag_catalog; it is a no-op where already in public (CI,
prod) or where ag_catalog does not exist. Objects owned by the AGE extension (pg_depend 'e') are
never touched. Table moves carry indexes, constraints and owned sequences with them; standalone
sequences and enums are moved explicitly. Idempotent.
"""
from __future__ import annotations

revision = "a012_app_tables_to_public"
down_revision = "a011_review_documents"
branch_labels = None
depends_on = None

def upgrade() -> None:
    from alembic import op

    from src.server.infra.db.app_schema import relocate_app_objects

    relocate_app_objects(op.get_bind())


def downgrade() -> None:
    """Intentionally a no-op: moving back into ag_catalog would re-create the bug."""

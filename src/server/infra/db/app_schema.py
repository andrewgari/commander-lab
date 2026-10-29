"""Relocate app objects from ag_catalog to public (decision-review-2026-10-29 section 5).

The dev DB's search_path leads with ag_catalog, so older migrations created app objects there.
``relocate_app_objects`` runs before Alembic starts (env.py, so unqualified names in old migrations
resolve under the pinned search_path) and again as migration a012. Objects owned by the AGE extension
are never moved; it is a no-op where nothing sits in ag_catalog. Idempotent.
"""
from __future__ import annotations

MOVE_SQL = r"""
DO $$
DECLARE r record;
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = 'ag_catalog') THEN
    RETURN;
  END IF;
  -- Tables (indexes, constraints, owned sequences follow).
  FOR r IN
    SELECT c.relname FROM pg_class c
    WHERE c.relnamespace = 'ag_catalog'::regnamespace AND c.relkind IN ('r', 'p')
      AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass
                      AND d.objid = c.oid AND d.deptype = 'e')
      AND NOT EXISTS (SELECT 1 FROM pg_class p WHERE p.relnamespace = 'public'::regnamespace
                      AND p.relname = c.relname)
  LOOP
    EXECUTE format('ALTER TABLE ag_catalog.%I SET SCHEMA public', r.relname);
  END LOOP;
  -- Standalone sequences (not owned by a table column).
  FOR r IN
    SELECT c.relname FROM pg_class c
    WHERE c.relnamespace = 'ag_catalog'::regnamespace AND c.relkind = 'S'
      AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_class'::regclass
                      AND d.objid = c.oid AND d.deptype IN ('e', 'a', 'i'))
      AND NOT EXISTS (SELECT 1 FROM pg_class p WHERE p.relnamespace = 'public'::regnamespace
                      AND p.relname = c.relname)
  LOOP
    EXECUTE format('ALTER SEQUENCE ag_catalog.%I SET SCHEMA public', r.relname);
  END LOOP;
  -- Enum types.
  FOR r IN
    SELECT t.typname FROM pg_type t
    WHERE t.typnamespace = 'ag_catalog'::regnamespace AND t.typtype = 'e'
      AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid = 'pg_type'::regclass
                      AND d.objid = t.oid AND d.deptype = 'e')
      AND NOT EXISTS (SELECT 1 FROM pg_type p WHERE p.typnamespace = 'public'::regnamespace
                      AND p.typname = t.typname)
  LOOP
    EXECUTE format('ALTER TYPE ag_catalog.%I SET SCHEMA public', r.typname);
  END LOOP;
END $$;
"""


def relocate_app_objects(connection) -> None:
    """Run MOVE_SQL on a SQLAlchemy connection (PostgreSQL only)."""
    from sqlalchemy import text

    if connection.dialect.name == "postgresql":
        connection.execute(text(MOVE_SQL))

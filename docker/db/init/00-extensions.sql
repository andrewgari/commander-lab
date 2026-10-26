-- Official postgres entrypoint runs this only for a NEW, EMPTY PGDATA.
-- Safe to reapply explicitly to an existing DB as the bootstrap superuser.
\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
CREATE EXTENSION IF NOT EXISTS age;

-- Database-scoped, not just this init session. No application tables/graph yet.
SELECT format('ALTER DATABASE %I SET search_path = ag_catalog, "$user", public',
              current_database())
\gexec

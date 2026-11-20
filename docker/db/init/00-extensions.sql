-- Official postgres entrypoint runs this only for a NEW, EMPTY PGDATA.
-- Safe to reapply explicitly to an existing DB as the bootstrap superuser.
\set ON_ERROR_STOP on
CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;

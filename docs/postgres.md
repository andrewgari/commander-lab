# PostgreSQL ORM layer

Additive SQLAlchemy 2.x layer (`db/`); Redis remains the default store and no app code is rewired.

- `db/config.py` – settings from env: `DATABASE_URL` (required to connect; `postgres://` and `postgresql://` are mapped to the psycopg v3 driver), `DB_POOL_SIZE` (5), `DB_MAX_OVERFLOW` (10), `DB_POOL_TIMEOUT` (30), `DB_POOL_RECYCLE` (1800), `DB_ECHO`.
- `db/engine.py` – lazy pooled engine singleton (`pool_pre_ping`), `dispose_engine()` for shutdown/tests. Nothing connects at import.
- `db/session.py` – `session_scope()`: commit on success, rollback on error, always close. Callers own the transaction, so several store calls can be atomic.
- `db/orm_models.py` – `composition_presets`, `deck_compositions` (JSONB on PostgreSQL, JSON elsewhere).
- `analytics/composition_pg_store.py` – same function names and dict shapes as `analytics/composition_store.py`, taking a `Session` instead of a Redis client.

Tables are created with `Base.metadata.create_all(engine)`; no Alembic migrations yet (follow-up).

Tests: `pytest tests/test_pg_store.py` (SQLite always; PostgreSQL too when `TEST_DATABASE_URL` is set — CI provides it).

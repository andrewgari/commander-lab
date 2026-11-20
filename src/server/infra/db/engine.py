"""Lazy engine singleton with connection pooling. Nothing connects at import time."""
from __future__ import annotations

import threading
from typing import Optional

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine

from src.server.infra.cache.datastore import WritesDisabledError, get_mode, is_readonly
from src.server.infra.db.config import PG_CONNECT_ARGS, DbSettings, get_settings

_lock = threading.Lock()
_engine: Optional[Engine] = None


def _guard_pg_transaction(connection) -> None:
    """Have PostgreSQL enforce readonly for every SQLAlchemy transaction.

    This catches ORM flushes, raw SQL, writable CTEs, and side-effecting SQL
    functions without attempting to classify SQL strings. The flag is sampled
    at transaction start; an already-running transaction cannot be retroactively
    made safe if the process environment is changed mid-transaction.
    """
    if is_readonly():
        # SET TRANSACTION is ineffective in DBAPI autocommit: it ends before
        # the next statement. Refuse that mode rather than silently allow writes.
        if connection.get_execution_options().get("isolation_level", "").upper() == "AUTOCOMMIT":
            raise WritesDisabledError("PostgreSQL autocommit")
        connection.exec_driver_sql("SET TRANSACTION READ ONLY")


def _translate_pg_readonly_error(context):
    # PostgreSQL SQLSTATE 25006 is read_only_sql_transaction. Keep the same
    # application-level exception as the Redis guard rather than exposing a DB
    # driver exception. Other DB failures must remain unchanged.
    if getattr(context.original_exception, "sqlstate", None) == "25006" and is_readonly():
        return WritesDisabledError("PostgreSQL write")
    return None


def make_engine(settings: DbSettings) -> Engine:
    kwargs = {"echo": settings.echo, "pool_pre_ping": True}
    if not settings.url.startswith("sqlite"):  # sqlite uses a different pool class
        kwargs.update(pool_size=settings.pool_size, max_overflow=settings.max_overflow,
                      pool_timeout=settings.pool_timeout, pool_recycle=settings.pool_recycle,
                      connect_args=dict(PG_CONNECT_ARGS))
    # Validate even when the engine is only constructed, before any work runs.
    get_mode()
    engine = create_engine(settings.url, **kwargs)
    if engine.dialect.name == "postgresql":
        event.listen(engine, "begin", _guard_pg_transaction)
        event.listen(engine, "handle_error", _translate_pg_readonly_error, retval=True)
    return engine


def get_engine() -> Engine:
    global _engine
    with _lock:
        if _engine is None:
            _engine = make_engine(get_settings())
        return _engine


def dispose_engine() -> None:
    """Close all pooled connections and drop the singleton (shutdown / tests)."""
    global _engine
    with _lock:
        if _engine is not None:
            _engine.dispose()
            _engine = None

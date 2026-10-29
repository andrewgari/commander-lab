"""Lazy engine singleton with connection pooling. Nothing connects at import time."""
from __future__ import annotations

import threading
from typing import Optional

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine

from src.server.infra.db.config import PG_CONNECT_ARGS, DbSettings, get_settings

_lock = threading.Lock()
_engine: Optional[Engine] = None


def make_engine(settings: DbSettings) -> Engine:
    kwargs = {"echo": settings.echo, "pool_pre_ping": True}
    if not settings.url.startswith("sqlite"):  # sqlite uses a different pool class
        kwargs.update(pool_size=settings.pool_size, max_overflow=settings.max_overflow,
                      pool_timeout=settings.pool_timeout, pool_recycle=settings.pool_recycle,
                      connect_args=dict(PG_CONNECT_ARGS))
    return create_engine(settings.url, **kwargs)


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

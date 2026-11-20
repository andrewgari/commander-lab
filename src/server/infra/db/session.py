"""Unit-of-work session helpers: commit on success, rollback on error, always close."""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator, Optional

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from src.server.infra.db.engine import get_engine


def get_session_factory(engine: Optional[Engine] = None) -> sessionmaker:
    return sessionmaker(bind=engine or get_engine(), expire_on_commit=False)


@contextmanager
def session_scope(factory: Optional[sessionmaker] = None) -> Iterator[Session]:
    session = (factory or get_session_factory())()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()

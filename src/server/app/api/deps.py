"""Shared API dependencies: DB session per request, 503 when unconfigured."""
from __future__ import annotations

import os
from typing import Iterator

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from src.server.infra.db.session import get_session_factory


def get_db() -> Iterator[Session]:
    if not os.environ.get("DATABASE_URL"):
        raise HTTPException(status_code=503, detail="Postgres not configured (DATABASE_URL unset)")
    factory = get_session_factory()
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


DbSession = Depends(get_db)

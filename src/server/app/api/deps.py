"""Dependency injection for the v2 API: DB sessions.

The engine connects lazily, so importing the app never requires a database;
a request without a reachable DB fails as 503 db_unavailable with a reason
(AGENT_RULES.md §4: missing data renders "unavailable" + reason).
"""
from __future__ import annotations

from typing import Iterator

from fastapi import HTTPException
from sqlalchemy.orm import Session

from src.server.infra.db.session import get_session_factory


def db_session() -> Iterator[Session]:
    try:
        factory = get_session_factory()
        session = factory()
    except Exception as e:  # DATABASE_URL unset/malformed, driver missing, ...
        raise HTTPException(
            status_code=503,
            detail={"error": "db_unavailable", "reason": str(e)},
        )
    try:
        yield session
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except Exception as e:  # connection lost mid-request, constraint errors, ...
        session.rollback()
        raise HTTPException(
            status_code=503,
            detail={"error": "db_unavailable", "reason": str(e)},
        )
    finally:
        session.close()

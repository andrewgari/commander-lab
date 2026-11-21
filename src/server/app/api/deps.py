"""Dependency injection for the v2 API: DB sessions.

The engine connects lazily, so importing the app never requires a database;
a request without a reachable DB fails as 503 db_unavailable with a reason
(AGENT_RULES.md §4: missing data renders "unavailable" + reason).
"""
from __future__ import annotations

import logging
from typing import Iterator

from fastapi import HTTPException
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from src.server.infra.cache.datastore import WritesDisabledError
from src.server.infra.db.session import get_session_factory

logger = logging.getLogger(__name__)


def _safe_db_reason(exc: Exception, *, phase: str) -> str:
    msg = str(exc)
    if "DATABASE_URL" in msg:
        return "Database is not configured."
    if isinstance(exc, OperationalError):
        return "Database connection is unavailable."
    if isinstance(exc, IntegrityError):
        return "Database constraint check failed."
    if phase == "init":
        return "Database session initialization failed."
    return "Database operation failed."


def db_session() -> Iterator[Session]:
    try:
        factory = get_session_factory()
        session = factory()
    except WritesDisabledError:
        raise
    except Exception as e:  # DATABASE_URL unset/malformed, driver missing, ...
        logger.exception("Failed to initialize database session")
        raise HTTPException(
            status_code=503,
            detail={"error": "db_unavailable", "reason": _safe_db_reason(e, phase="init")},
        )
    try:
        yield session
        session.commit()
    except HTTPException:
        session.rollback()
        raise
    except WritesDisabledError:
        session.rollback()
        raise
    except RequestValidationError:
        # FastAPI throws body-validation failures into the dependency
        # generator — a bad request body is not a DB outage; let it
        # surface as the standard 422 instead of 503 db_unavailable.
        session.rollback()
        raise
    except Exception as e:  # connection lost mid-request, constraint errors, ...
        session.rollback()
        logger.exception("Database session failed during request")
        raise HTTPException(
            status_code=503,
            detail={"error": "db_unavailable", "reason": _safe_db_reason(e, phase="request")},
        )
    finally:
        session.close()

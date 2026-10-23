"""Database settings read from the environment (no credentials in code)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.engine import make_url


def normalize_url(url: str) -> str:
    """Force the psycopg (v3) driver for bare postgres:// / postgresql:// URLs."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


@dataclass(frozen=True)
class DbSettings:
    url: str
    pool_size: int = 5
    max_overflow: int = 10
    pool_timeout: float = 30.0
    pool_recycle: int = 1800
    echo: bool = False

    def safe_url(self) -> str:
        """URL with the password masked, safe for logging."""
        return make_url(self.url).render_as_string(hide_password=True)


def get_settings(url: Optional[str] = None) -> DbSettings:
    raw = url or os.environ.get("DATABASE_URL")
    if not raw:
        raise RuntimeError("DATABASE_URL is not set")
    env = os.environ
    return DbSettings(
        url=normalize_url(raw),
        pool_size=int(env.get("DB_POOL_SIZE", 5)),
        max_overflow=int(env.get("DB_MAX_OVERFLOW", 10)),
        pool_timeout=float(env.get("DB_POOL_TIMEOUT", 30)),
        pool_recycle=int(env.get("DB_POOL_RECYCLE", 1800)),
        echo=env.get("DB_ECHO", "").lower() in ("1", "true", "yes"),
    )

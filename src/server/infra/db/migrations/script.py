"""Alembic script directory for Commander Lab's PostgreSQL ORM migrations."""
from __future__ import annotations

from alembic import script

__all__ = ["ScriptDirectory"]

script_dir = script.ScriptDirectory.from_config("db/migrations/alembic.ini")

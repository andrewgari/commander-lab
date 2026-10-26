"""Alembic environment for the Commander Lab PostgreSQL ORM layer.

Reads DATABASE_URL from the environment (db/config.get_settings) and
runs migrations against ``db.orm_models.Base.metadata``.
"""
from __future__ import annotations

import os
import sys

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make the repo root importable so `src.server.infra.db.orm_models` resolves.
# env.py lives at src/server/infra/db/migrations/ -> repo root is 5 levels up.
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))

from src.server.infra.db.config import get_settings, normalize_url  # noqa: E402
from src.server.infra.db.orm_models import Base  # noqa: E402

config = context.config

# Honor $DATABASE_URL (normalized to the psycopg driver) over the .ini placeholder.
database_url = os.environ.get("DATABASE_URL")
if database_url:
    config.set_main_option("sqlalchemy.url", normalize_url(database_url))

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_paramstyle="named",
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

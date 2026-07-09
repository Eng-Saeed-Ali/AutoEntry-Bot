"""Alembic async migration environment for AutoEntry Bot.

Reads ``DATABASE_URL`` dynamically from ``src.config.settings``
(unwrapping the pydantic-settings ``PostgresDsn``).  No credentials
are hardcoded — the .env file is the sole source of truth.

Uses a standalone ``MetaData`` object for migrations since the
project uses SQLAlchemy Core raw ``text()`` SQL (not ORM models).
Autogenerate will therefore produce empty migration scripts; all
table definitions are authored manually in the version files.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig

from sqlalchemy import MetaData, pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# ---------------------------------------------------------------------------
# Core Alembic objects
# ---------------------------------------------------------------------------
config = context.config

# Standard Python logger (not structlog — alembic uses stdlib logging)
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# ---------------------------------------------------------------------------
# Dynamically inject DATABASE_URL from pydantic-settings
# ---------------------------------------------------------------------------
from src.config.settings import settings  # noqa: E402

config.set_main_option("sqlalchemy.url", str(settings.database_url))

# ---------------------------------------------------------------------------
# MetaData for migration tracking
# ---------------------------------------------------------------------------
# The project uses raw SQL (not ORM), so we create a standalone MetaData.
# Autogenerate will produce empty migrations — this is expected and accepted.
target_metadata = MetaData()


# ============================================================================
# Migration runners
# ============================================================================


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (SQL script output)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    """Execute migrations within an active connection."""
    context.configure(connection=connection, target_metadata=target_metadata)

    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create async engine and run migrations over asyncpg."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (against live database)."""
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
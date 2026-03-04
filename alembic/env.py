"""
Alembic environment configuration.

This enables `alembic revision --autogenerate` and `alembic upgrade head`
using the application's SQLAlchemy metadata.

We use an async engine because the application uses async SQLAlchemy.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import context
from app.database import CONNECT_ARGS, DATABASE_URL, NORMALIZED_DATABASE_URL
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _set_sqlalchemy_url() -> None:
    """
    Ensure Alembic uses the same database URL as the application.

    Alembic requires this value even when we override engine creation below.
    """

    config.set_main_option("sqlalchemy.url", DATABASE_URL)


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL (no DBAPI connection).
    """

    _set_sqlalchemy_url()
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Any) -> None:
    """
    Configure the migration context and run migrations.

    `connection` is a synchronous SQLAlchemy Connection provided by
    `AsyncConnection.run_sync(...)`.
    """

    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations in 'online' mode using an async engine."""

    _set_sqlalchemy_url()

    connectable: AsyncEngine = create_async_engine(
        NORMALIZED_DATABASE_URL,
        poolclass=pool.NullPool,
        connect_args=CONNECT_ARGS,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

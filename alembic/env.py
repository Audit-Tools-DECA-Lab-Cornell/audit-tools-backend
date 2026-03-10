"""
Alembic environment configuration.

This environment uses the shared SQLAlchemy metadata defined by `app.models`
for both products. Each migration run still targets a single physical database
through `-x product=yee` or `-x product=playspace`.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
from typing import Any

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from alembic import context
from app.database import ProductKey, get_database_url, normalize_postgres_sqlalchemy_url
from app.models import Base

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_product_key() -> ProductKey:
    """
    Resolve the target product database for this migration run.

    Usage:
      alembic -x product=yee upgrade head
      alembic -x product=playspace upgrade head
    """

    x_args = context.get_x_argument(as_dictionary=True)
    raw_product = x_args.get("product", ProductKey.YEE.value)
    normalized = raw_product.strip().lower()
    try:
        return ProductKey(normalized)
    except ValueError as err:
        allowed = ", ".join([p.value for p in ProductKey])
        raise ValueError(f"Invalid product '{raw_product}'. Expected one of: {allowed}.") from err


def _set_sqlalchemy_url(product: ProductKey) -> str:
    """
    Ensure Alembic uses the same database URL as the application.

    Alembic requires this value even when we override engine creation below.
    """

    raw_url = get_database_url(product)
    config.set_main_option("sqlalchemy.url", raw_url)
    return raw_url


def run_migrations_offline() -> None:
    """
    Run migrations in 'offline' mode.

    This configures the context with just a URL (no DBAPI connection).
    """

    product = _resolve_product_key()
    _set_sqlalchemy_url(product)
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

    product = _resolve_product_key()
    raw_url = _set_sqlalchemy_url(product)
    normalized_url, connect_args = normalize_postgres_sqlalchemy_url(raw_url)

    connectable: AsyncEngine = create_async_engine(
        normalized_url,
        poolclass=pool.NullPool,
        connect_args=connect_args,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)

    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())

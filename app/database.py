"""
Database configuration and session management (async SQLAlchemy).

This module provides:
- A PostgreSQL async SQLAlchemy engine (asyncpg driver)
- An async session factory
- A `get_async_session()` dependency for FastAPI / Strawberry context creation
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator

from dotenv import find_dotenv, load_dotenv
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

# Load environment variables from a local `.env` file (when present).
load_dotenv(find_dotenv())


def _get_database_url() -> str:
    """
    Resolve the database URL.

    Preferred: set `DATABASE_URL` (example format):
      postgresql+asyncpg://user:password@localhost:5432/dbname
    """

    # NOTE: We intentionally do not read/print environment variables in terminal commands.
    # At runtime, your process environment can provide DATABASE_URL as needed.
    url = os.getenv("DATABASE_URL")
    if url and url.strip():
        return url.strip()

    # Practical local-development default. Change as appropriate for your setup.
    return "postgresql+asyncpg://postgres:postgres@localhost:5432/audit_tools"


DATABASE_URL: str = _get_database_url()


def _normalize_postgres_sqlalchemy_url(raw_url: str) -> tuple[URL, dict[str, object]]:
    """
    Normalize a Postgres URL for async SQLAlchemy (asyncpg).

    This primarily ensures Neon-provided libpq URLs work without manual edits.

    Neon often provides:
      postgresql://user:password@host/db?sslmode=require&channel_binding=require

    SQLAlchemy + asyncpg expects:
    - driver: `postgresql+asyncpg`
    - SSL: passed as `connect_args={"ssl": True}`
    - libpq-only query params removed (e.g. `sslmode`, `channel_binding`)
    """

    normalized = raw_url.strip()
    if normalized.startswith("postgres://"):
        normalized = normalized.replace("postgres://", "postgresql://", 1)

    url = make_url(normalized)
    if url.drivername == "postgresql":
        url = url.set(drivername="postgresql+asyncpg")

    query = dict(url.query)
    sslmode = query.pop("sslmode", None)
    query.pop("channel_binding", None)

    connect_args: dict[str, object] = {}
    if isinstance(sslmode, str) and sslmode.lower() in {"require", "verify-ca", "verify-full"}:
        connect_args["ssl"] = True

    url = url.set(query=query)
    return url, connect_args


NORMALIZED_DATABASE_URL, CONNECT_ARGS = _normalize_postgres_sqlalchemy_url(DATABASE_URL)

# Create the async engine once per process.
async_engine: AsyncEngine = create_async_engine(
    NORMALIZED_DATABASE_URL,
    echo=False,  # Set True for SQL debugging.
    pool_pre_ping=True,
    connect_args=CONNECT_ARGS,
)

# Create an async session factory.
AsyncSessionFactory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=async_engine,
    autoflush=False,
    expire_on_commit=False,
)


async def get_async_session() -> AsyncIterator[AsyncSession]:
    """
    FastAPI dependency / Strawberry context helper.

    Yields an `AsyncSession` and ensures it's closed after use.
    """

    async with AsyncSessionFactory() as session:
        yield session


async def dispose_engine() -> None:
    """Gracefully close all pooled connections on shutdown."""

    await async_engine.dispose()

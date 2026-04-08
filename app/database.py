"""
Database configuration and session management (async SQLAlchemy).

This module provides:
- A PostgreSQL async SQLAlchemy engine (asyncpg driver)
- An async session factory
- Product-scoped session dependencies so a single backend can serve multiple databases

Product databases:
- Youth Enabling Environment (YEE)
- Playspace Play Value and Usability (PLAYSPACE)
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from enum import Enum

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


class ProductKey(str, Enum):
    """
    Product selector for routing requests to the correct database.

    We intentionally keep product keys short and URL-friendly because we mount
    product-scoped endpoints (e.g. `/yee/graphql`, `/playspace/graphql`).
    """

    YEE = "yee"
    PLAYSPACE = "playspace"


def _get_database_url(product: ProductKey, development: bool = True) -> str:
    """
    Resolve the database URL for a given product.

    Preferred: set product-specific URLs:
    - `DATABASE_URL_YEE` || `DEV_DATABASE_URL_YEE`
    - `DATABASE_URL_PLAYSPACE` || `DEV_DATABASE_URL_PLAYSPACE`

    Example format:
      postgresql+asyncpg://user:password@localhost:5432/dbname
    """

    # Prefer explicit development URLs when present, but always fall back to the
    # documented product-specific DATABASE_URL_* variables before using defaults.
    base_env_var = "DATABASE_URL_YEE" if product is ProductKey.YEE else "DATABASE_URL_PLAYSPACE"
    candidate_env_vars: list[str] = []
    if development:
        candidate_env_vars.append(f"DEV_{base_env_var}")
    candidate_env_vars.append(base_env_var)

    for env_var in candidate_env_vars:
        url = os.getenv(env_var)
        if url and url.strip():
            return url.strip()

    # Backwards-compatible fallback: a single `DATABASE_URL` is treated as YEE.
    if product is ProductKey.YEE:
        legacy_url = os.getenv("DATABASE_URL")
        if legacy_url and legacy_url.strip():
            return legacy_url.strip()

    # Practical local-development defaults. Change as appropriate for your setup.
    default_dbname = "audit_tools_yee" if product is ProductKey.YEE else "audit_tools_playspace"
    return f"postgresql+asyncpg://postgres:postgres@localhost:5432/{default_dbname}"


def normalize_postgres_sqlalchemy_url(raw_url: str) -> tuple[URL, dict[str, object]]:
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


RAW_DATABASE_URL_BY_PRODUCT: dict[ProductKey, str] = {
    ProductKey.YEE: _get_database_url(ProductKey.YEE),
    ProductKey.PLAYSPACE: _get_database_url(ProductKey.PLAYSPACE),
}

NORMALIZED_DATABASE_URL_BY_PRODUCT: dict[ProductKey, URL] = {}
CONNECT_ARGS_BY_PRODUCT: dict[ProductKey, dict[str, object]] = {}
ASYNC_ENGINE_BY_PRODUCT: dict[ProductKey, AsyncEngine] = {}
ASYNC_SESSION_FACTORY_BY_PRODUCT: dict[ProductKey, async_sessionmaker[AsyncSession]] = {}

for _product_key, _raw_url in RAW_DATABASE_URL_BY_PRODUCT.items():
    _normalized_url, _connect_args = normalize_postgres_sqlalchemy_url(_raw_url)
    NORMALIZED_DATABASE_URL_BY_PRODUCT[_product_key] = _normalized_url
    CONNECT_ARGS_BY_PRODUCT[_product_key] = _connect_args

    engine: AsyncEngine = create_async_engine(
        _normalized_url,
        echo=False,  # Set True for SQL debugging.
        pool_pre_ping=True,
        connect_args=_connect_args,
    )
    ASYNC_ENGINE_BY_PRODUCT[_product_key] = engine
    ASYNC_SESSION_FACTORY_BY_PRODUCT[_product_key] = async_sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )


def get_database_url(product: ProductKey) -> str:
    """Return the raw database URL for a product (as provided by env/config)."""

    return RAW_DATABASE_URL_BY_PRODUCT[product]


async def get_async_session(product: ProductKey = ProductKey.YEE) -> AsyncIterator[AsyncSession]:
    """
    FastAPI dependency / Strawberry context helper.

    Yields an `AsyncSession` and ensures it's closed after use.
    """

    session_factory = ASYNC_SESSION_FACTORY_BY_PRODUCT[product]
    async with session_factory() as session:
        yield session


async def get_async_session_yee() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields a YEE database session."""

    async with ASYNC_SESSION_FACTORY_BY_PRODUCT[ProductKey.YEE]() as session:
        yield session


async def get_async_session_playspace() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields a Playspace database session."""

    async with ASYNC_SESSION_FACTORY_BY_PRODUCT[ProductKey.PLAYSPACE]() as session:
        yield session


async def dispose_engines() -> None:
    """Gracefully close all pooled connections on shutdown."""

    for engine in ASYNC_ENGINE_BY_PRODUCT.values():
        await engine.dispose()

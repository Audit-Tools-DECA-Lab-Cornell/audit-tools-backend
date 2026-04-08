"""
Database engines and async session dependencies for product databases.
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

load_dotenv(find_dotenv())

######################################################################################
################################ Database Products ###################################
######################################################################################


class ProductKey(str, Enum):
    """Selector used to route requests to YEE or Playspace databases."""

    YEE = "yee"
    PLAYSPACE = "playspace"


def _resolve_raw_database_url(product: ProductKey) -> str:
    """Resolve one product database URL from environment variables or defaults."""

    env_suffix = "YEE" if product is ProductKey.YEE else "PLAYSPACE"
    env_keys = [f"DEV_DATABASE_URL_{env_suffix}", f"DATABASE_URL_{env_suffix}"]

    for env_key in env_keys:
        raw_value = os.getenv(env_key)
        if raw_value is None:
            continue
        normalized = raw_value.strip()
        if normalized:
            return normalized

    if product is ProductKey.YEE:
        legacy_url = os.getenv("DATABASE_URL")
        if legacy_url and legacy_url.strip():
            return legacy_url.strip()

    default_dbname = "audit_tools_yee" if product is ProductKey.YEE else "audit_tools_playspace"
    return f"postgresql+asyncpg://postgres:postgres@localhost:5432/{default_dbname}"


def _normalize_postgres_sqlalchemy_url(raw_url: str) -> tuple[URL, dict[str, object]]:
    """Normalize a PostgreSQL URL for SQLAlchemy asyncpg usage."""

    normalized_url = raw_url.strip()
    if normalized_url.startswith("postgres://"):
        normalized_url = normalized_url.replace("postgres://", "postgresql://", 1)

    sqlalchemy_url = make_url(normalized_url)
    if sqlalchemy_url.drivername == "postgresql":
        sqlalchemy_url = sqlalchemy_url.set(drivername="postgresql+asyncpg")

    url_query = dict(sqlalchemy_url.query)
    sslmode = url_query.pop("sslmode", None)
    url_query.pop("channel_binding", None)

    connect_args: dict[str, object] = {}
    if isinstance(sslmode, str) and sslmode.lower() in {"require", "verify-ca", "verify-full"}:
        connect_args["ssl"] = True

    return sqlalchemy_url.set(query=url_query), connect_args


def get_database_url(product: ProductKey) -> str:
    """Return the resolved raw database URL for one product."""

    return RAW_DATABASE_URL_BY_PRODUCT[product]


def normalize_postgres_sqlalchemy_url(raw_url: str) -> tuple[URL, dict[str, object]]:
    """Public compatibility wrapper for URL normalization used by Alembic."""

    return _normalize_postgres_sqlalchemy_url(raw_url)


def _build_engine_and_factory(
    product: ProductKey,
) -> tuple[str, AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Create one product engine + session factory pair."""

    raw_database_url = _resolve_raw_database_url(product)
    normalized_url, connect_args = _normalize_postgres_sqlalchemy_url(raw_database_url)
    engine = create_async_engine(
        normalized_url,
        echo=False,
        pool_pre_ping=True,
        connect_args=connect_args,
    )
    session_factory = async_sessionmaker(
        bind=engine,
        autoflush=False,
        expire_on_commit=False,
    )
    return raw_database_url, engine, session_factory


######################################################################################
############################### Engines and Sessions #################################
######################################################################################

RAW_DATABASE_URL_BY_PRODUCT: dict[ProductKey, str] = {}
ASYNC_ENGINE_BY_PRODUCT: dict[ProductKey, AsyncEngine] = {}
ASYNC_SESSION_FACTORY_BY_PRODUCT: dict[ProductKey, async_sessionmaker[AsyncSession]] = {}

for product_key in ProductKey:
    raw_database_url, engine, session_factory = _build_engine_and_factory(product_key)
    RAW_DATABASE_URL_BY_PRODUCT[product_key] = raw_database_url
    ASYNC_ENGINE_BY_PRODUCT[product_key] = engine
    ASYNC_SESSION_FACTORY_BY_PRODUCT[product_key] = session_factory


async def get_async_session(product: ProductKey = ProductKey.YEE) -> AsyncIterator[AsyncSession]:
    """Yield one async session for a specific product."""

    async with ASYNC_SESSION_FACTORY_BY_PRODUCT[product]() as session:
        yield session


async def get_async_session_playspace() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency that yields a Playspace database session."""

    async with ASYNC_SESSION_FACTORY_BY_PRODUCT[ProductKey.PLAYSPACE]() as session:
        yield session


async def dispose_engines() -> None:
    """Gracefully close all pooled connections on shutdown."""

    for engine in ASYNC_ENGINE_BY_PRODUCT.values():
        await engine.dispose()

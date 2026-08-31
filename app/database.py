"""
Database engines and async session dependencies for product databases.
"""

from __future__ import annotations

import ssl
from collections.abc import AsyncIterator

import certifi
from dotenv import find_dotenv, load_dotenv
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import (
	AsyncEngine,
	AsyncSession,
	async_sessionmaker,
	create_async_engine,
)

from app.db_urls import (
	DatabaseEnvironment,
	ProductKey,
	describe_database_target,
	get_active_database_environment,
	resolve_raw_database_url,
)

load_dotenv(find_dotenv())

######################################################################################
################################ Database Products ###################################
######################################################################################

ACTIVE_DATABASE_ENVIRONMENT: DatabaseEnvironment = get_active_database_environment()


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
	if isinstance(sslmode, str) and sslmode.lower() in {
		"require",
		"verify-ca",
		"verify-full",
	}:
		connect_args["ssl"] = ssl.create_default_context(cafile=certifi.where())
		connect_args["statement_cache_size"] = 0

	return sqlalchemy_url.set(query=url_query), connect_args


def get_database_url(product: ProductKey) -> str:
	"""Return the resolved raw database URL for one product.

	Raises that product's recorded configuration error rather than a ``KeyError``
	when its engine could not be built.
	"""

	raw_url = RAW_DATABASE_URL_BY_PRODUCT.get(product)
	if raw_url is None:
		raise DATABASE_INIT_ERROR_BY_PRODUCT.get(
			product, RuntimeError(f"No database configured for product {product.value}.")
		)
	return raw_url


def normalize_postgres_sqlalchemy_url(raw_url: str) -> tuple[URL, dict[str, object]]:
	"""Public compatibility wrapper for URL normalization used by Alembic."""

	return _normalize_postgres_sqlalchemy_url(raw_url)


def describe_active_database_targets() -> dict[str, str]:
	"""Return safe host/database labels for startup diagnostics.

	A product whose engine could not be built is reported as unconfigured rather
	than omitted, so startup diagnostics stay complete and never leak a URL.
	"""

	return {
		product.value: (
			describe_database_target(RAW_DATABASE_URL_BY_PRODUCT[product])
			if product in RAW_DATABASE_URL_BY_PRODUCT
			else "unconfigured"
		)
		for product in ProductKey
	}


def _build_engine_and_factory(
	product: ProductKey,
	environment: DatabaseEnvironment,
) -> tuple[str, AsyncEngine, async_sessionmaker[AsyncSession]]:
	"""Create one product engine + session factory pair."""

	raw_database_url = resolve_raw_database_url(product, environment)
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
#: Why a product's engine could not be built, remembered so the failure surfaces
#: when THAT product is used rather than when this module is imported.
DATABASE_INIT_ERROR_BY_PRODUCT: dict[ProductKey, Exception] = {}

for product_key in ProductKey:
	# One unconfigured product must not stop the other from working. A
	# production-like run raises from `resolve_raw_database_url` when its URL
	# variable is absent, and building both engines at import time turned that
	# into "importing app.database crashes" for any single-product operator
	# task (for example the Phase 3 inventory CLI running against one database).
	# The error is kept and re-raised on first use of that product instead.
	try:
		raw_database_url, engine, session_factory = _build_engine_and_factory(product_key, ACTIVE_DATABASE_ENVIRONMENT)
	except Exception as error:  # noqa: BLE001 - deliberately deferred to first use
		DATABASE_INIT_ERROR_BY_PRODUCT[product_key] = error
		continue
	RAW_DATABASE_URL_BY_PRODUCT[product_key] = raw_database_url
	ASYNC_ENGINE_BY_PRODUCT[product_key] = engine
	ASYNC_SESSION_FACTORY_BY_PRODUCT[product_key] = session_factory


def get_session_factory(product: ProductKey) -> async_sessionmaker[AsyncSession]:
	"""Session factory for one product, raising that product's init error.

	Raises the original configuration error (never a ``KeyError``) so an
	operator sees which environment variable is missing.
	"""

	factory = ASYNC_SESSION_FACTORY_BY_PRODUCT.get(product)
	if factory is None:
		raise DATABASE_INIT_ERROR_BY_PRODUCT.get(
			product, RuntimeError(f"No database configured for product {product.value}.")
		)
	return factory


async def get_async_session(
	product: ProductKey = ProductKey.YEE,
) -> AsyncIterator[AsyncSession]:
	"""Yield one async session for a specific product."""

	async with get_session_factory(product)() as session:
		yield session


async def get_async_session_playspace() -> AsyncIterator[AsyncSession]:
	"""FastAPI dependency that yields a Playspace database session."""

	async with get_session_factory(ProductKey.PLAYSPACE)() as session:
		yield session


async def dispose_engines() -> None:
	"""Gracefully close all pooled connections on shutdown."""

	for engine in ASYNC_ENGINE_BY_PRODUCT.values():
		await engine.dispose()

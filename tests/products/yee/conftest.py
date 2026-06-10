"""Shared integration-test fixtures for YEE API endpoint coverage.

Mirrors the Playspace harness but builds the YEE database from the per-product
`yee` Alembic branch (`yee@head`) and seeds deterministic YEE entities. The
suite skips automatically unless `TEST_DATABASE_URL_YEE` is configured.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import (
	AsyncEngine,
	AsyncSession,
	async_sessionmaker,
	create_async_engine,
)
from sqlalchemy.pool import NullPool

from alembic import command
from app.database import (
	ASYNC_ENGINE_BY_PRODUCT,
	ASYNC_SESSION_FACTORY_BY_PRODUCT,
	RAW_DATABASE_URL_BY_PRODUCT,
	ProductKey,
	normalize_postgres_sqlalchemy_url,
)
from app.main import app
from app.seed import (
	_build_yee_entities,
	_clear_product_tables,
	_insert_seed_entities,
)

REPO_ROOT = Path(__file__).resolve().parents[3]


def _require_test_database_url() -> str:
	"""Return the dedicated YEE test DB URL or skip the suite."""

	raw_url = os.getenv("TEST_DATABASE_URL_YEE")
	if raw_url is None or raw_url.strip() == "":
		pytest.skip("TEST_DATABASE_URL_YEE is required for YEE endpoint tests.")
	return raw_url.strip()


async def _reseed_yee_database(session_factory: async_sessionmaker[AsyncSession]) -> None:
	"""Clear and reseed the dedicated YEE test database."""

	async with session_factory() as session:
		await _clear_product_tables(session, ProductKey.YEE)
		await _insert_seed_entities(session, _build_yee_entities())
		await session.commit()


async def _terminate_yee_test_database_connections(engine: AsyncEngine) -> None:
	"""Close stale sessions before the destructive schema reset."""

	for _ in range(5):
		async with engine.begin() as conn:
			remaining = (
				await conn.execute(
					text(
						"SELECT count(*) "
						"FROM pg_stat_activity "
						"WHERE datname = current_database() "
						"AND pid <> pg_backend_pid()"
					)
				)
			).scalar_one()
			if remaining == 0:
				return
			await conn.execute(
				text(
					"SELECT pg_terminate_backend(pid) "
					"FROM pg_stat_activity "
					"WHERE datname = current_database() "
					"AND pid <> pg_backend_pid()"
				)
			)
		await asyncio.sleep(0.25)


async def _reset_yee_test_database(engine: AsyncEngine) -> None:
	"""Drop and recreate the public schema so the YEE branch can apply cleanly.

	The test databases share one hosted compute, so a connection that reconnects after the
	pre-reset termination sweep can hold ``AccessShareLock`` on a table while the ``CASCADE``
	drop needs ``AccessExclusiveLock`` on it - a lock cycle Postgres reports as a deadlock.
	Bounding the lock wait lets the drop abort and release its own locks; re-terminating the
	stragglers and retrying then wins the schema reset cleanly.
	"""

	last_error: DBAPIError | None = None
	for _ in range(5):
		try:
			async with engine.begin() as conn:
				await conn.execute(text("SET lock_timeout = '10s'"))
				await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
				await conn.execute(text("CREATE SCHEMA public"))
			return
		except DBAPIError as error:
			last_error = error
			await _terminate_yee_test_database_connections(engine)
			await asyncio.sleep(0.5)
	if last_error is not None:
		raise last_error


def _upgrade_yee_test_database(engine: AsyncEngine) -> None:
	"""Run Alembic migrations against a freshly reset YEE test database."""

	asyncio.run(_reset_yee_test_database(engine))
	alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
	alembic_config.cmd_opts = argparse.Namespace(
		x=["product=yee", "environment=test"],
	)
	command.upgrade(alembic_config, "yee@head")


@pytest.fixture(scope="session")
def yee_test_session_factory() -> Iterator[async_sessionmaker[AsyncSession]]:
	"""Patch the YEE app DB bindings to use the dedicated test branch."""

	test_database_url = _require_test_database_url()
	normalized_url, connect_args = normalize_postgres_sqlalchemy_url(test_database_url)
	connect_args = {**connect_args, "statement_cache_size": 0}

	original_url = RAW_DATABASE_URL_BY_PRODUCT[ProductKey.YEE]
	original_engine = ASYNC_ENGINE_BY_PRODUCT[ProductKey.YEE]
	original_session_factory = ASYNC_SESSION_FACTORY_BY_PRODUCT[ProductKey.YEE]

	RAW_DATABASE_URL_BY_PRODUCT[ProductKey.YEE] = test_database_url
	# Release any pooled connections the imported app already opened against this
	# same test database before dropping/recreating ``public``.
	asyncio.run(original_engine.dispose())

	cleanup_engine: AsyncEngine = create_async_engine(
		normalized_url,
		echo=False,
		pool_pre_ping=True,
		poolclass=NullPool,
		connect_args=connect_args,
	)
	asyncio.run(_terminate_yee_test_database_connections(cleanup_engine))
	asyncio.run(cleanup_engine.dispose())

	migration_engine: AsyncEngine = create_async_engine(
		normalized_url,
		echo=False,
		pool_pre_ping=True,
		poolclass=NullPool,
		connect_args=connect_args,
	)
	_upgrade_yee_test_database(migration_engine)
	asyncio.run(migration_engine.dispose())

	# ``NullPool`` is required, not just preferred: this engine is seeded under one event loop
	# (``asyncio.run`` at setup) and then serves requests under the TestClient's separate loop.
	# Pooling would hand a connection bound to the setup loop to a request on the other loop,
	# which asyncpg rejects. A fresh connection per checkout keeps every operation loop-local.
	test_engine: AsyncEngine = create_async_engine(
		normalized_url,
		echo=False,
		pool_pre_ping=True,
		poolclass=NullPool,
		connect_args=connect_args,
	)
	test_session_factory = async_sessionmaker(
		bind=test_engine,
		autoflush=False,
		expire_on_commit=False,
	)

	ASYNC_ENGINE_BY_PRODUCT[ProductKey.YEE] = test_engine
	ASYNC_SESSION_FACTORY_BY_PRODUCT[ProductKey.YEE] = test_session_factory

	asyncio.run(_reseed_yee_database(test_session_factory))

	try:
		yield test_session_factory
	finally:
		RAW_DATABASE_URL_BY_PRODUCT[ProductKey.YEE] = original_url
		ASYNC_ENGINE_BY_PRODUCT[ProductKey.YEE] = original_engine
		ASYNC_SESSION_FACTORY_BY_PRODUCT[ProductKey.YEE] = original_session_factory
		asyncio.run(test_engine.dispose())


@pytest.fixture(scope="session")
def yee_client(
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> Iterator[TestClient]:
	"""Create a real FastAPI client bound to the dedicated YEE test DB."""

	_ = yee_test_session_factory
	with TestClient(app) as client:
		yield client

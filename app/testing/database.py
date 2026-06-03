"""Utilities for safe Playspace E2E database reset and seed commands."""

from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.database import ProductKey, normalize_postgres_sqlalchemy_url
from app.seed import _build_playspace_entities, _clear_product_tables, _insert_seed_entities

REPO_ROOT = Path(__file__).resolve().parents[2]


def get_playspace_test_database_url() -> str:
	"""Return the Playspace E2E database URL from the test environment."""

	for env_key in ("TEST_DATABASE_URL_PLAYSPACE", "DEV_DATABASE_URL_PLAYSPACE", "DATABASE_URL_PLAYSPACE"):
		raw_value = os.getenv(env_key)
		if raw_value is not None and raw_value.strip():
			return raw_value.strip()
	raise RuntimeError("TEST_DATABASE_URL_PLAYSPACE is required for Playspace E2E setup.")


def assert_test_database_url(raw_url: str) -> None:
	"""Refuse destructive operations unless the target is explicitly a test database."""

	normalized_url = raw_url.strip().lower()
	allow_reset = os.getenv("ALLOW_TEST_DB_RESET", "").strip().lower() == "true"
	is_test_environment = os.getenv("ENVIRONMENT", "").strip().lower() == "test"
	looks_like_test_database = "test" in normalized_url
	if is_test_environment and allow_reset and looks_like_test_database:
		return
	raise RuntimeError(
		"Refusing to reset a non-test Playspace database. "
		"Set ENVIRONMENT=test, ALLOW_TEST_DB_RESET=true, and use a TEST_DATABASE_URL_PLAYSPACE containing 'test'."
	)


def build_test_engine(raw_url: str) -> AsyncEngine:
	"""Create an async SQLAlchemy engine for one-shot E2E setup commands."""

	normalized_url, connect_args = normalize_postgres_sqlalchemy_url(raw_url)
	connect_args = {**connect_args, "statement_cache_size": 0}
	return create_async_engine(
		normalized_url,
		echo=False,
		pool_pre_ping=True,
		poolclass=NullPool,
		connect_args=connect_args,
	)


async def terminate_stale_connections(engine: AsyncEngine) -> None:
	"""Terminate idle sessions connected to the current test database before schema reset."""

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


async def reset_public_schema(engine: AsyncEngine) -> None:
	"""Drop and recreate the public schema in the guarded Playspace test database."""

	async with engine.begin() as conn:
		await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
		await conn.execute(text("CREATE SCHEMA public"))


def run_playspace_migrations() -> None:
	"""Run Alembic migrations for the Playspace product database."""

	alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
	alembic_config.cmd_opts = argparse.Namespace(
		x=[f"product={ProductKey.PLAYSPACE.value}", "environment=test"],
	)
	command.upgrade(alembic_config, f"{ProductKey.PLAYSPACE.value}@head")


async def seed_playspace_database(session_factory: async_sessionmaker[AsyncSession]) -> None:
	"""Clear Playspace-scoped tables and insert deterministic Playspace E2E seed entities."""

	async with session_factory() as session:
		await _clear_product_tables(session, ProductKey.PLAYSPACE)
		await _insert_seed_entities(session, _build_playspace_entities())
		await session.commit()


async def reset_playspace_test_database_schema() -> None:
	"""Reset only the public schema in the guarded Playspace test database."""

	raw_url = get_playspace_test_database_url()
	assert_test_database_url(raw_url)
	engine = build_test_engine(raw_url)
	try:
		await terminate_stale_connections(engine)
		await reset_public_schema(engine)
	finally:
		await engine.dispose()


def reset_and_migrate_playspace_test_database() -> None:
	"""Reset the guarded Playspace test database and apply all migrations."""

	asyncio.run(reset_playspace_test_database_schema())
	run_playspace_migrations()


async def seed_current_playspace_test_database() -> None:
	"""Seed deterministic Playspace E2E data into the current test database schema."""

	raw_url = get_playspace_test_database_url()
	assert_test_database_url(raw_url)
	engine = build_test_engine(raw_url)
	session_factory = async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
	try:
		await seed_playspace_database(session_factory)
	finally:
		await engine.dispose()

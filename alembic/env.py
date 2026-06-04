"""
Alembic environment configuration.

This environment uses the shared SQLAlchemy metadata defined by `app.models`
for both products. Each migration run still targets a single physical database
through `-x product=yee` or `-x product=playspace`.
"""

from __future__ import annotations

import asyncio
from enum import Enum
from logging.config import fileConfig
import os
from typing import Any

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dotenv import find_dotenv, load_dotenv

from alembic import context
from app.database import ProductKey, normalize_postgres_sqlalchemy_url
from app.models import Base

load_dotenv(find_dotenv())

config = context.config

if config.config_file_name is not None:
	fileConfig(config.config_file_name)

target_metadata = Base.metadata


class Environment(str, Enum):
	"""The target environment for this migration run."""

	TEST = "test"
	DEVELOPMENT = "development"
	PRODUCTION = "production"


def _is_production_like_environment(environment: Environment) -> bool:
	"""Detect hosted production-style environments where silent DB fallback is risky."""

	return (
		environment is Environment.PRODUCTION
		or os.getenv("RENDER") == "true"
		or bool(os.getenv("RENDER_SERVICE_ID"))
	)


def _resolve_raw_database_url(product: ProductKey, environment: Environment) -> str:
	"""Resolve one product database URL from environment variables or defaults."""

	env_suffix = "YEE" if product is ProductKey.YEE else "PLAYSPACE"
	match environment:
		case Environment.TEST:
			env_prefix = "TEST_"
		case Environment.DEVELOPMENT:
			env_prefix = "DEV_"
		case Environment.PRODUCTION:
			env_prefix = ""

	env_keys = [f"{env_prefix}DATABASE_URL_{env_suffix}", f"DATABASE_URL_{env_suffix}"]

	for env_key in env_keys:
		raw_value = os.getenv(env_key)
		if raw_value is None:
			continue
		normalized = raw_value.strip()
		if normalized:
			return normalized

	if product is ProductKey.YEE:
		legacy_url = os.getenv("DATABASE_URL")
		if legacy_url and legacy_url.strip() and not _is_production_like_environment(environment):
			return legacy_url.strip()

	if _is_production_like_environment(environment):
		expected_key = f"DATABASE_URL_{env_suffix}"
		raise RuntimeError(
			f"Missing required environment variable {expected_key}. "
			"Production migration runs must target explicit product database URLs."
		)

	default_dbname = "audit_tools_yee" if product is ProductKey.YEE else "audit_tools_playspace"
	return f"postgresql+asyncpg://postgres:postgres@localhost:5432/{default_dbname}"


def _resolve_environment() -> Environment:
	"""
	Resolve the target environment for this migration run.

	Resolution order: ``-x environment=...``, then ``ENVIRONMENT`` from ``.env``,
	then ``development``.

	Usage:
	  alembic -x product=playspace -x environment=test upgrade head
	  alembic -x product=yee -x environment=production upgrade head
	"""

	x_args = context.get_x_argument(as_dictionary=True)
	raw_environment = x_args.get("environment", Environment.DEVELOPMENT.value) or os.getenv(
		"ENVIRONMENT", Environment.DEVELOPMENT.value
	)
	environment = raw_environment.strip().lower()
	allowed = {item.value for item in Environment}
	if environment not in allowed:
		allowed_values = ", ".join(sorted(allowed))
		raise ValueError(f"Invalid environment '{raw_environment}'. Expected one of: {allowed_values}.")
	return Environment(environment)


def _resolve_product_key() -> ProductKey:
	"""
	Resolve the target product database for this migration run.

	Usage:
	  alembic -x product=yee environment=production upgrade head
	  alembic -x product=playspace environment=production upgrade head
	"""

	x_args = context.get_x_argument(as_dictionary=True)
	raw_product = x_args.get("product", ProductKey.PLAYSPACE.value)
	normalized = raw_product.strip().lower()
	try:
		return ProductKey(normalized)
	except ValueError as err:
		allowed = ", ".join([p.value for p in ProductKey])
		raise ValueError(f"Invalid product '{raw_product}'. Expected one of: {allowed}.") from err


def _set_sqlalchemy_url(product: ProductKey, environment: Environment) -> str:
	"""
	Ensure Alembic uses the same database URL as the application.

	Alembic requires this value even when we override engine creation below.
	"""

	raw_url = _resolve_raw_database_url(product, environment)
	config.set_main_option("sqlalchemy.url", raw_url)
	return raw_url


def run_migrations_offline() -> None:
	"""
	Run migrations in 'offline' mode.

	This configures the context with just a URL (no DBAPI connection).
	"""

	product = _resolve_product_key()
	environment = _resolve_environment()
	_set_sqlalchemy_url(product, environment)
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
	environment = _resolve_environment()
	raw_url = _set_sqlalchemy_url(product, environment)
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

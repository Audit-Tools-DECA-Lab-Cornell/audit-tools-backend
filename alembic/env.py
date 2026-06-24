"""
Alembic environment configuration.

This environment uses the shared SQLAlchemy metadata defined by `app.models`
for both products. Each migration run still targets a single physical database
through `-x product=yee` or `-x product=playspace`.
"""

from __future__ import annotations

import asyncio
from logging.config import fileConfig
import os
from typing import Any

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from dotenv import find_dotenv, load_dotenv

from alembic import context
from app.database import normalize_postgres_sqlalchemy_url
from app.db_urls import DatabaseEnvironment, ProductKey, parse_database_environment, resolve_raw_database_url
from app.models import Base, table_belongs_to_product

load_dotenv(find_dotenv())

config = context.config

if config.config_file_name is not None:
	fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _resolve_environment() -> DatabaseEnvironment:
	"""
	Resolve the target environment for this migration run.

	Resolution order: ``-x environment=...``, then ``ENVIRONMENT`` from ``.env``,
	then ``development``.

	Usage:
	  alembic -x product=playspace -x environment=test upgrade playspace@head
	  alembic -x product=yee -x environment=production upgrade yee@head
	"""

	x_args = context.get_x_argument(as_dictionary=True)
	raw_environment = x_args.get("environment") or os.getenv("ENVIRONMENT")
	return parse_database_environment(raw_environment)


def _resolve_product_key() -> ProductKey:
	"""
	Resolve the target product database for this migration run.

	Usage:
	  alembic -x product=yee -x environment=production upgrade yee@head
	  alembic -x product=playspace -x environment=production upgrade playspace@head
	"""

	x_args = context.get_x_argument(as_dictionary=True)
	raw_product = x_args.get("product", ProductKey.PLAYSPACE.value)
	normalized = raw_product.strip().lower()
	try:
		return ProductKey(normalized)
	except ValueError as err:
		allowed = ", ".join([p.value for p in ProductKey])
		raise ValueError(f"Invalid product '{raw_product}'. Expected one of: {allowed}.") from err


def _make_include_name(product: ProductKey):
	"""Build an Alembic ``include_name`` filter scoped to one product's tables.

	This governs objects discovered during **database reflection** — it keeps
	autogenerate from proposing to drop the *other* product's tables if they ever
	exist in the physical database. It does NOT suppress tables that exist only in
	``target_metadata`` (those never appear during reflection); ``include_object``
	below handles that ``create_table`` case.
	"""

	def include_name(name: str | None, type_: str, parent_names: dict[str, str | None]) -> bool:
		if type_ == "table" and name is not None:
			return table_belongs_to_product(name, product.value)
		return True

	return include_name


def _make_include_object(product: ProductKey):
	"""Build an Alembic ``include_object`` filter scoped to one product's tables.

	Unlike ``include_name`` (reflection-only), this hook is consulted for objects
	present in ``target_metadata`` as well, so it suppresses ``create_table`` ops
	for the *other* product's metadata-only tables (e.g. ``playspace_*`` when
	autogenerating against the YEE database). Physical table creation remains
	driven by the per-product migration branches.
	"""

	def include_object(object_: Any, name: str | None, type_: str, reflected: bool, compare_to: Any) -> bool:
		if type_ == "table":
			table_name = name if name is not None else getattr(object_, "name", None)
			if table_name is not None:
				return table_belongs_to_product(str(table_name), product.value)
		return True

	return include_object


def _set_sqlalchemy_url(product: ProductKey, environment: DatabaseEnvironment) -> str:
	"""
	Ensure Alembic uses the same database URL as the application.

	Alembic requires this value even when we override engine creation below.
	"""

	raw_url = resolve_raw_database_url(product, environment)
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
		include_name=_make_include_name(product),
		include_object=_make_include_object(product),
	)

	with context.begin_transaction():
		context.run_migrations()


def _do_run_migrations(connection: Any, product: ProductKey) -> None:
	"""
	Configure the migration context and run migrations.

	`connection` is a synchronous SQLAlchemy Connection provided by
	`AsyncConnection.run_sync(...)`.
	"""

	context.configure(
		connection=connection,
		target_metadata=target_metadata,
		compare_type=True,
		include_name=_make_include_name(product),
		include_object=_make_include_object(product),
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
		await connection.run_sync(_do_run_migrations, product)

	await connectable.dispose()


if context.is_offline_mode():
	run_migrations_offline()
else:
	asyncio.run(run_migrations_online())

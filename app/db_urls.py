"""
Shared database URL resolution for the API runtime and Alembic.

Set all product URLs in ``.env`` once (``DEV_*``, ``DATABASE_URL_*``, ``TEST_*``),
then select the target with ``ENVIRONMENT`` or Alembic ``-x environment=...``.
"""

from __future__ import annotations

import os
from enum import Enum


class ProductKey(str, Enum):
	"""Selector used to route requests to YEE or Playspace databases."""

	YEE = "yee"
	PLAYSPACE = "playspace"


class DatabaseEnvironment(str, Enum):
	"""Target database tier for local tooling and migrations."""

	TEST = "test"
	DEVELOPMENT = "development"
	PRODUCTION = "production"


_ENVIRONMENT_ALIASES: dict[str, DatabaseEnvironment] = {
	"test": DatabaseEnvironment.TEST,
	"testing": DatabaseEnvironment.TEST,
	"dev": DatabaseEnvironment.DEVELOPMENT,
	"development": DatabaseEnvironment.DEVELOPMENT,
	"local": DatabaseEnvironment.DEVELOPMENT,
	"prod": DatabaseEnvironment.PRODUCTION,
	"production": DatabaseEnvironment.PRODUCTION,
}


def parse_database_environment(
	raw_value: str | None,
	*,
	default: DatabaseEnvironment = DatabaseEnvironment.DEVELOPMENT,
) -> DatabaseEnvironment:
	"""Normalize an environment selector from CLI flags or ``ENVIRONMENT``."""

	if raw_value is None:
		return default

	normalized = raw_value.strip().lower()
	if not normalized:
		return default

	resolved = _ENVIRONMENT_ALIASES.get(normalized)
	if resolved is None:
		allowed = ", ".join(sorted(_ENVIRONMENT_ALIASES))
		raise ValueError(f"Invalid environment '{raw_value}'. Expected one of: {allowed}.")
	return resolved


def get_active_database_environment() -> DatabaseEnvironment:
	"""Return the environment selected for the current process."""

	return parse_database_environment(os.getenv("ENVIRONMENT"))


def _is_production_like_environment(environment: DatabaseEnvironment) -> bool:
	"""Detect production-style runs where silent DB fallback is risky."""

	return (
		environment is DatabaseEnvironment.PRODUCTION
		or os.getenv("RENDER") == "true"
		or bool(os.getenv("RENDER_SERVICE_ID"))
	)


def _environment_url_prefix(environment: DatabaseEnvironment) -> str:
	"""Return the env-var prefix for one database tier."""

	match environment:
		case DatabaseEnvironment.TEST:
			return "TEST_"
		case DatabaseEnvironment.DEVELOPMENT:
			return "DEV_"
		case DatabaseEnvironment.PRODUCTION:
			return ""


def resolve_raw_database_url(product: ProductKey, environment: DatabaseEnvironment) -> str:
	"""Resolve one product database URL from tier-prefixed env vars or defaults."""

	env_suffix = "YEE" if product is ProductKey.YEE else "PLAYSPACE"
	env_prefix = _environment_url_prefix(environment)
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
			"Production runs must target explicit product database URLs."
		)

	default_dbname = "audit_tools_yee" if product is ProductKey.YEE else "audit_tools_playspace"
	return f"postgresql+asyncpg://postgres:postgres@localhost:5432/{default_dbname}"


def database_url_env_keys(product: ProductKey, environment: DatabaseEnvironment) -> list[str]:
	"""Return env keys consulted for one product/environment pair (for diagnostics)."""

	env_suffix = "YEE" if product is ProductKey.YEE else "PLAYSPACE"
	env_prefix = _environment_url_prefix(environment)
	keys = [f"{env_prefix}DATABASE_URL_{env_suffix}", f"DATABASE_URL_{env_suffix}"]
	if product is ProductKey.YEE:
		keys.append("DATABASE_URL")
	return keys


def describe_database_target(raw_url: str) -> str:
	"""Return a safe host/database label for logs (no credentials)."""

	from sqlalchemy.engine import make_url

	try:
		parsed = make_url(raw_url.strip())
	except Exception:
		return "unknown"

	host = parsed.host or "localhost"
	database = parsed.database or "unknown"
	return f"{host}/{database}"

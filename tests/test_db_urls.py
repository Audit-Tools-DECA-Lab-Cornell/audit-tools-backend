"""Tests for environment-aware database URL resolution."""

from __future__ import annotations

import pytest

from app.db_urls import DatabaseEnvironment, ProductKey, parse_database_environment, resolve_raw_database_url


def test_parse_database_environment_aliases() -> None:
	assert parse_database_environment("dev") is DatabaseEnvironment.DEVELOPMENT
	assert parse_database_environment("prod") is DatabaseEnvironment.PRODUCTION
	assert parse_database_environment("test") is DatabaseEnvironment.TEST


def test_parse_database_environment_rejects_unknown() -> None:
	with pytest.raises(ValueError, match="Invalid environment"):
		parse_database_environment("staging")


def test_resolve_development_prefers_dev_url(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("DEV_DATABASE_URL_PLAYSPACE", "postgresql+asyncpg://dev-host/playspace_dev")
	monkeypatch.setenv("DATABASE_URL_PLAYSPACE", "postgresql+asyncpg://prod-host/playspace_prod")

	url = resolve_raw_database_url(ProductKey.PLAYSPACE, DatabaseEnvironment.DEVELOPMENT)
	assert url == "postgresql+asyncpg://dev-host/playspace_dev"


def test_resolve_production_uses_primary_url(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("DEV_DATABASE_URL_PLAYSPACE", "postgresql+asyncpg://dev-host/playspace_dev")
	monkeypatch.setenv("DATABASE_URL_PLAYSPACE", "postgresql+asyncpg://prod-host/playspace_prod")

	url = resolve_raw_database_url(ProductKey.PLAYSPACE, DatabaseEnvironment.PRODUCTION)
	assert url == "postgresql+asyncpg://prod-host/playspace_prod"


def test_resolve_test_prefers_test_url(monkeypatch: pytest.MonkeyPatch) -> None:
	monkeypatch.setenv("TEST_DATABASE_URL_PLAYSPACE", "postgresql+asyncpg://test-host/playspace_test")
	monkeypatch.setenv("DATABASE_URL_PLAYSPACE", "postgresql+asyncpg://prod-host/playspace_prod")

	url = resolve_raw_database_url(ProductKey.PLAYSPACE, DatabaseEnvironment.TEST)
	assert url == "postgresql+asyncpg://test-host/playspace_test"

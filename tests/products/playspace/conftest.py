"""Shared integration-test fixtures for Playspace API endpoint coverage."""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from collections.abc import Iterator

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    AsyncEngine,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import NullPool

from app.core.demo_data import (
    DEMO_ACCOUNT_ID,
    DEMO_AUDIT_RIVERSIDE_ID,
    DEMO_AUDITOR_AKL01_ID,
    DEMO_PLACE_RIVERSIDE_ID,
    DEMO_PROJECT_URBAN_ID,
)
from app.database import (
    ASYNC_ENGINE_BY_PRODUCT,
    ASYNC_SESSION_FACTORY_BY_PRODUCT,
    RAW_DATABASE_URL_BY_PRODUCT,
    ProductKey,
    normalize_postgres_sqlalchemy_url,
)
from app.main import app
from app.models import AuditorProfile
from app.seed import _build_playspace_entities, _clear_shared_tables, _insert_seed_entities

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class PlayspaceSeedSnapshot:
    """Stable seeded identifiers used across endpoint integration tests."""

    manager_account_id: str
    seeded_auditor_profile_id: str
    seeded_auditor_account_id: str
    seeded_auditor_code: str
    urban_project_id: str
    riverside_place_id: str
    riverside_submitted_audit_id: str


def _require_test_database_url() -> str:
    """Return the dedicated Playspace test DB URL or skip the suite."""

    raw_url = os.getenv("TEST_DATABASE_URL_PLAYSPACE")
    if raw_url is None or raw_url.strip() == "":
        pytest.skip("TEST_DATABASE_URL_PLAYSPACE is required for Playspace endpoint tests.")
    return raw_url.strip()


async def _reseed_playspace_database(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Clear and reseed the dedicated Playspace test database."""

    async with session_factory() as session:
        await _clear_shared_tables(session)
        await _insert_seed_entities(session, _build_playspace_entities())
        await session.commit()


def _upgrade_playspace_test_database() -> None:
    """Run Alembic migrations against the patched Playspace test database."""

    alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_config.cmd_opts = SimpleNamespace(x=["product=playspace"])
    command.upgrade(alembic_config, "head")


async def _load_seed_snapshot(
    session_factory: async_sessionmaker[AsyncSession],
) -> PlayspaceSeedSnapshot:
    """Load the specific seeded auditor identity required for endpoint auth headers."""

    async with session_factory() as session:
        result = await session.execute(
            select(AuditorProfile).where(AuditorProfile.id == DEMO_AUDITOR_AKL01_ID)
        )
        profile = result.scalar_one()
        return PlayspaceSeedSnapshot(
            manager_account_id=str(DEMO_ACCOUNT_ID),
            seeded_auditor_profile_id=str(profile.id),
            seeded_auditor_account_id=str(profile.account_id),
            seeded_auditor_code=profile.auditor_code,
            urban_project_id=str(DEMO_PROJECT_URBAN_ID),
            riverside_place_id=str(DEMO_PLACE_RIVERSIDE_ID),
            riverside_submitted_audit_id=str(DEMO_AUDIT_RIVERSIDE_ID),
        )


@pytest.fixture(scope="session")
def playspace_test_session_factory() -> Iterator[async_sessionmaker[AsyncSession]]:
    """Patch the Playspace app DB bindings to use the dedicated test branch."""

    test_database_url = _require_test_database_url()
    normalized_url, connect_args = normalize_postgres_sqlalchemy_url(test_database_url)
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

    original_url = RAW_DATABASE_URL_BY_PRODUCT[ProductKey.PLAYSPACE]
    original_engine = ASYNC_ENGINE_BY_PRODUCT[ProductKey.PLAYSPACE]
    original_session_factory = ASYNC_SESSION_FACTORY_BY_PRODUCT[ProductKey.PLAYSPACE]

    RAW_DATABASE_URL_BY_PRODUCT[ProductKey.PLAYSPACE] = test_database_url
    ASYNC_ENGINE_BY_PRODUCT[ProductKey.PLAYSPACE] = test_engine
    ASYNC_SESSION_FACTORY_BY_PRODUCT[ProductKey.PLAYSPACE] = test_session_factory

    _upgrade_playspace_test_database()
    asyncio.run(_reseed_playspace_database(test_session_factory))

    try:
        yield test_session_factory
    finally:
        RAW_DATABASE_URL_BY_PRODUCT[ProductKey.PLAYSPACE] = original_url
        ASYNC_ENGINE_BY_PRODUCT[ProductKey.PLAYSPACE] = original_engine
        ASYNC_SESSION_FACTORY_BY_PRODUCT[ProductKey.PLAYSPACE] = original_session_factory
        asyncio.run(test_engine.dispose())


@pytest.fixture(scope="session")
def playspace_seed_snapshot(
    playspace_test_session_factory: async_sessionmaker[AsyncSession],
) -> PlayspaceSeedSnapshot:
    """Expose a stable snapshot of seeded IDs for endpoint tests."""

    return asyncio.run(_load_seed_snapshot(playspace_test_session_factory))


@pytest.fixture(scope="session")
def playspace_client(
    playspace_test_session_factory: async_sessionmaker[AsyncSession],
) -> Iterator[TestClient]:
    """Create a real FastAPI client bound to the dedicated Playspace test DB."""

    _ = playspace_test_session_factory
    with TestClient(app) as client:
        yield client

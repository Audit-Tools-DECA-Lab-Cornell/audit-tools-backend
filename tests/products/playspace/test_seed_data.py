"""Tests for multi-account Playspace seed data coverage."""

from __future__ import annotations

from app.models import Account, AccountType, Project
from app.products.playspace.seed_data import build_playspace_seed_entities


def test_build_playspace_seed_entities_includes_admin_and_multiple_manager_accounts() -> None:
    """Seed data should include admin access plus more than one manager account."""

    entities = build_playspace_seed_entities()
    accounts = [entity for entity in entities if isinstance(entity, Account)]

    admin_accounts = [account for account in accounts if account.account_type is AccountType.ADMIN]
    manager_accounts = [account for account in accounts if account.account_type is AccountType.MANAGER]

    assert len(admin_accounts) >= 1
    assert len(manager_accounts) >= 2


def test_build_playspace_seed_entities_spreads_projects_across_manager_accounts() -> None:
    """Projects should no longer all belong to a single manager account."""

    entities = build_playspace_seed_entities()
    projects = [entity for entity in entities if isinstance(entity, Project)]

    project_account_ids = {project.account_id for project in projects}

    assert len(project_account_ids) >= 2

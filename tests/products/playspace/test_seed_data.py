"""Tests for multi-account Playspace seed data coverage."""

from __future__ import annotations

from app.models import (
	Account,
	AccountType,
	Audit,
	AuditorAssignment,
	Project,
	ProjectPlace,
	User,
)
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


def test_build_playspace_seed_entities_projects_have_created_by_user_id() -> None:
	"""Every seeded project must reference a creator user."""

	entities = build_playspace_seed_entities()
	projects = [entity for entity in entities if isinstance(entity, Project)]
	users = [entity for entity in entities if isinstance(entity, User)]
	user_ids = {user.id for user in users}

	assert len(projects) > 0
	for project in projects:
		assert project.created_by_user_id is not None
		assert project.created_by_user_id in user_ids


def test_build_playspace_seed_entities_include_project_place_links_and_pair_scoped_audits() -> None:
	"""Seed data should include join rows plus place-scoped assignments and audits."""

	entities = build_playspace_seed_entities()
	project_place_links = [entity for entity in entities if isinstance(entity, ProjectPlace)]
	assignments = [entity for entity in entities if isinstance(entity, AuditorAssignment)]
	audits = [entity for entity in entities if isinstance(entity, Audit)]

	assert len(project_place_links) > 0
	assert all(assignment.project_id is not None for assignment in assignments)
	assert all(audit.project_id is not None for audit in audits)

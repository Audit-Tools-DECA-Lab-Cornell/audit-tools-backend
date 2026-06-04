"""Tests for multi-account Playspace seed data coverage."""

from __future__ import annotations

from app.models import (
	Account,
	AccountType,
	Audit,
	AuditStatus,
	AuditorAssignment,
	AuditorProfile,
	ManagerProfile,
	PlayspaceChecklistAnswer,
	PlayspaceSubmission,
	Instrument,
	Project,
	ProjectPlace,
	User,
)
from app.products.playspace.seed_data import build_playspace_seed_entities


def test_build_playspace_seed_entities_instrument_is_active_root_version() -> None:
	"""The seeded Playspace instrument is the active root of version history."""

	entities = build_playspace_seed_entities()
	instruments = [entity for entity in entities if isinstance(entity, Instrument)]

	assert len(instruments) == 1
	seed_instrument = instruments[0]
	assert seed_instrument.is_active is True
	assert seed_instrument.parent_instrument_id is None


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


def test_build_playspace_seed_entities_every_profile_has_a_linked_user() -> None:
	"""Every ManagerProfile and AuditorProfile must have a unique user_id.

	Accounts are organisational workspaces only — no User is created directly
	for a MANAGER or AUDITOR account. Each profile carries its own login
	identity via its user_id FK, and no two profiles may share a User.
	"""

	entities = build_playspace_seed_entities()
	users = [entity for entity in entities if isinstance(entity, User)]
	manager_profiles = [entity for entity in entities if isinstance(entity, ManagerProfile)]
	auditor_profiles = [entity for entity in entities if isinstance(entity, AuditorProfile)]

	user_ids = {user.id for user in users}
	user_emails = {user.email for user in users}

	# Every manager profile must have a user_id pointing to a real User,
	# and the User's email must match the profile's email.
	for mgr_profile in manager_profiles:
		assert mgr_profile.user_id is not None, f"ManagerProfile {mgr_profile.email!r} has no user_id"
		assert mgr_profile.user_id in user_ids, f"ManagerProfile {mgr_profile.email!r} user_id has no matching User"
		assert mgr_profile.email in user_emails, f"No User found with email {mgr_profile.email!r}"

	# Every auditor profile must have a user_id pointing to a real User.
	for aud_profile in auditor_profiles:
		assert aud_profile.user_id is not None, f"AuditorProfile {aud_profile.email!r} has no user_id"
		assert aud_profile.user_id in user_ids, f"AuditorProfile {aud_profile.email!r} user_id has no matching User"

	# All user_ids across all profiles must be unique (no shared logins).
	all_profile_user_ids = [p.user_id for p in manager_profiles] + [p.user_id for p in auditor_profiles]
	assert len(all_profile_user_ids) == len(set(all_profile_user_ids)), "Two or more profiles share the same user_id"

	# No User belonging to a MANAGER or AUDITOR account should exist outside
	# of a profile link. (Admin accounts are exempt — they have no profile table.)
	accounts = [entity for entity in entities if isinstance(entity, Account)]
	manager_account_ids = {a.id for a in accounts if a.account_type is AccountType.MANAGER}
	auditor_account_ids = {a.id for a in accounts if a.account_type is AccountType.AUDITOR}
	profile_user_ids: set[object] = set(all_profile_user_ids)

	for user in users:
		if user.account_id in manager_account_ids or user.account_id in auditor_account_ids:
			assert user.id in profile_user_ids, f"User {user.email!r} belongs to a non-admin account but has no profile"


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


def test_build_playspace_seed_entities_attach_checklist_answers_for_drafts() -> None:
	"""Draft seed rows should attach checklist selections for cascade insert."""

	entities = build_playspace_seed_entities()
	draft_audits = [
		entity
		for entity in entities
		if isinstance(entity, PlayspaceSubmission) and entity.status in {AuditStatus.IN_PROGRESS, AuditStatus.PAUSED}
	]
	assert draft_audits

	found_checklist_answer = False
	for audit in draft_audits:
		for section in audit.submission_sections or []:
			for question_response in section.question_responses or []:
				checklist_answer = question_response.checklist_answer
				if not isinstance(checklist_answer, PlayspaceChecklistAnswer):
					continue
				found_checklist_answer = True
				assert question_response.scale_answers == []
				assert isinstance(checklist_answer.selected_option_keys, list)
				assert len(checklist_answer.selected_option_keys) > 0
				if "other" in checklist_answer.selected_option_keys:
					other_text = checklist_answer.other_details.get("text")
					assert isinstance(other_text, str)
					assert other_text.strip()

	assert found_checklist_answer

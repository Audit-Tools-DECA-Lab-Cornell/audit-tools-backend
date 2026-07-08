#!/usr/bin/env python3
"""Move the test auditors created by ``add_test_auditors.py`` to another manager
account and re-point their assignment (run locally).

``add_test_auditors.py`` created ``test-auditor-02@example.org`` ..
``test-auditor-30@example.org`` under the manager ``manager-demo@yee.local``,
assigned to a hardcoded project/place. This script:

  * finds those test auditors under the source manager's account
  * moves each auditor's ``users`` row and ``auditor_profiles`` row to the
    account whose *primary* manager email is ``TARGET_MANAGER_EMAIL``
  * drops any existing ``auditor_assignments`` rows for that auditor (they
    point at project/place rows owned by the *old* account, which would be a
    dangling cross-account reference once the auditor moves) and creates a
    single new assignment to ``TARGET_PROJECT_NAME`` / ``TARGET_PLACE_NAME``
    under the new account

The target project and place must already exist under the target account
(this script does not create them). It is idempotent: an auditor already
moved to the target account, or already assigned to the target project/place,
is left as-is / skipped where appropriate.

The script targets the production YEE database via ``DATABASE_URL_YEE`` in
your local ``.env`` file.

Usage (from the repo root, inside your virtualenv)::

    python -m scripts.reassign_test_auditors            # prompts before writing
    python -m scripts.reassign_test_auditors --yes      # skip the confirmation
    python -m scripts.reassign_test_auditors --dry-run  # show what would happen
"""

from __future__ import annotations

import argparse
import asyncio
import os
import ssl
import sys
import uuid
from datetime import datetime, timezone

import certifi

from dotenv import find_dotenv, load_dotenv
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Import only modules that do NOT build the app's database engines on import,
# so this script can run against DATABASE_URL_YEE without needing the Playspace
# URL to be configured.
from app.models import (
	AuditorAssignment,
	AuditorProfile,
	ManagerProfile,
	Place,
	Project,
	ProjectPlace,
	User,
)

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

SOURCE_MANAGER_EMAIL = "manager-demo@yee.local"
TARGET_MANAGER_EMAIL = "jel357@cornell.edu"
TARGET_PROJECT_NAME = "Youth Friendly Communities Project"
TARGET_PLACE_NAME = "Henry St. Courtyard"

# The test auditors this script moves are the ones ``add_test_auditors.py``
# creates: ``test-auditor-<NN>@example.org``.
TEST_AUDITOR_EMAIL_PREFIX = "test-auditor-"
TEST_AUDITOR_EMAIL_DOMAIN = "example.org"


def _normalize_async_url(raw_url: str):
	"""Mirror app.database URL normalization for asyncpg without importing it.

	Returns ``(sqlalchemy_url, connect_args)``.
	"""

	normalized = raw_url.strip()
	if normalized.startswith("postgres://"):
		normalized = normalized.replace("postgres://", "postgresql://", 1)

	url = make_url(normalized)
	if url.drivername == "postgresql":
		url = url.set(drivername="postgresql+asyncpg")

	query = dict(url.query)
	sslmode = query.pop("sslmode", None)
	query.pop("channel_binding", None)

	connect_args: dict[str, object] = {}
	if isinstance(sslmode, str) and sslmode.lower() in {"require", "verify-ca", "verify-full"}:
		connect_args["ssl"] = ssl.create_default_context(cafile=certifi.where())
		connect_args["statement_cache_size"] = 0

	return url.set(query=query), connect_args


def _is_test_auditor_email(email: str | None) -> bool:
	if email is None:
		return False
	lowered = email.strip().lower()
	return lowered.startswith(TEST_AUDITOR_EMAIL_PREFIX) and lowered.endswith(f"@{TEST_AUDITOR_EMAIL_DOMAIN}")


async def _resolve_source_account(session: AsyncSession) -> tuple[User, uuid.UUID]:
	manager = (
		await session.execute(select(User).where(User.email == SOURCE_MANAGER_EMAIL))
	).scalar_one_or_none()
	if manager is None:
		raise SystemExit(f"Source manager '{SOURCE_MANAGER_EMAIL}' was not found in this database.")
	if manager.account_id is None:
		raise SystemExit(f"Source manager '{SOURCE_MANAGER_EMAIL}' has no account configured.")
	return manager, manager.account_id


async def _resolve_target_account(session: AsyncSession) -> tuple[ManagerProfile, uuid.UUID]:
	profile = (
		await session.execute(select(ManagerProfile).where(ManagerProfile.email == TARGET_MANAGER_EMAIL))
	).scalar_one_or_none()
	if profile is None:
		raise SystemExit(f"No manager_profiles row found for '{TARGET_MANAGER_EMAIL}'.")
	if not profile.is_primary:
		print(
			f"WARNING: manager_profiles row for '{TARGET_MANAGER_EMAIL}' is not marked "
			"is_primary=true; proceeding anyway since the account_id still resolves."
		)
	return profile, profile.account_id


async def _resolve_target_project(session: AsyncSession, *, account_id) -> Project:
	projects = (
		await session.execute(
			select(Project).where(Project.account_id == account_id, Project.name == TARGET_PROJECT_NAME)
		)
	).scalars().all()
	if not projects:
		raise SystemExit(
			f"No project named '{TARGET_PROJECT_NAME}' was found under account {account_id}."
		)
	if len(projects) > 1:
		raise SystemExit(
			f"Multiple projects named '{TARGET_PROJECT_NAME}' were found under account {account_id}; "
			"resolve the ambiguity before running this script."
		)
	return projects[0]


async def _resolve_target_place(session: AsyncSession, *, project: Project) -> Place:
	places = (
		await session.execute(
			select(Place)
			.join(ProjectPlace, ProjectPlace.place_id == Place.id)
			.where(ProjectPlace.project_id == project.id, Place.name == TARGET_PLACE_NAME)
		)
	).scalars().all()
	if not places:
		known = (
			await session.execute(
				select(Place.name).join(ProjectPlace, ProjectPlace.place_id == Place.id).where(
					ProjectPlace.project_id == project.id
				)
			)
		).scalars().all()
		raise SystemExit(
			f"No place named '{TARGET_PLACE_NAME}' is linked to project '{project.name}' ({project.id}). "
			f"Places currently linked: {sorted(known) or '(none)'}"
		)
	if len(places) > 1:
		raise SystemExit(
			f"Multiple places named '{TARGET_PLACE_NAME}' are linked to project '{project.name}'; "
			"resolve the ambiguity before running this script."
		)
	return places[0]


async def _reassign(session: AsyncSession, *, dry_run: bool) -> None:
	_source_manager, source_account_id = await _resolve_source_account(session)
	target_manager_profile, target_account_id = await _resolve_target_account(session)
	target_project = await _resolve_target_project(session, account_id=target_account_id)
	target_place = await _resolve_target_place(session, project=target_project)

	print(f"Source account: {source_account_id} (manager {SOURCE_MANAGER_EMAIL})")
	print(
		f"Target account: {target_account_id} "
		f"(primary manager {target_manager_profile.email}, is_primary={target_manager_profile.is_primary})"
	)
	print(f"Target project: {target_project.name} ({target_project.id})")
	print(f"Target place:   {target_place.name} ({target_place.id})")
	print()

	if source_account_id == target_account_id:
		raise SystemExit("Source and target account are the same; nothing to move.")

	profiles = (
		await session.execute(select(AuditorProfile).where(AuditorProfile.account_id == source_account_id))
	).scalars().all()
	test_profiles = [profile for profile in profiles if _is_test_auditor_email(profile.email)]

	if not test_profiles:
		print(f"No test auditors (test-auditor-*@{TEST_AUDITOR_EMAIL_DOMAIN}) found under the source account.")
		return

	now = datetime.now(timezone.utc)
	moved = 0
	assigned = 0
	skipped = 0

	for profile in sorted(test_profiles, key=lambda p: p.email or ""):
		if profile.user_id is None:
			print(f"SKIP   {profile.email} -> auditor profile has no linked user row")
			skipped += 1
			continue
		user = await session.get(User, profile.user_id)
		if user is None:
			print(f"SKIP   {profile.email} -> linked user row {profile.user_id} not found")
			skipped += 1
			continue

		existing_assignments = (
			await session.execute(
				select(AuditorAssignment).where(AuditorAssignment.auditor_profile_id == profile.id)
			)
		).scalars().all()
		stale_assignments = [
			assignment
			for assignment in existing_assignments
			if not (assignment.project_id == target_project.id and assignment.place_id == target_place.id)
		]
		already_assigned = any(
			assignment.project_id == target_project.id and assignment.place_id == target_place.id
			for assignment in existing_assignments
		)

		if dry_run:
			action = "MOVE" if user.account_id != target_account_id else "KEEP"
			print(
				f"DRYRUN {action:4s} {profile.email} -> account={target_account_id}, "
				f"drop {len(stale_assignments)} stale assignment(s), "
				f"{'already assigned' if already_assigned else 'add new assignment'} to "
				f"{target_project.name}/{target_place.name}"
			)
			moved += 1
			if not already_assigned:
				assigned += 1
			continue

		user.account_id = target_account_id
		profile.account_id = target_account_id
		session.add(user)
		session.add(profile)

		for assignment in stale_assignments:
			await session.delete(assignment)
		await session.flush()

		if not already_assigned:
			session.add(
				AuditorAssignment(
					auditor_profile_id=profile.id,
					project_id=target_project.id,
					place_id=target_place.id,
					assigned_at=now,
				)
			)
			await session.flush()
			assigned += 1

		print(
			f"MOVED  {profile.email} -> account={target_account_id}, "
			f"dropped {len(stale_assignments)} stale assignment(s), "
			f"assigned to {target_project.name}/{target_place.name}"
		)
		moved += 1

	if dry_run:
		await session.rollback()
		print(
			f"\nDry run complete. Would move {moved}, add {assigned} new assignment(s), "
			f"skip {skipped}. No changes written."
		)
		return

	await session.commit()
	print(f"\nDone. Moved {moved} auditor(s), added {assigned} new assignment(s), skipped {skipped}.")


def _resolve_target_url():
	"""Load .env and resolve the YEE database URL + asyncpg connect args."""

	load_dotenv(find_dotenv())
	raw_url = os.getenv("DATABASE_URL_YEE", "").strip()
	if not raw_url:
		raise SystemExit("DATABASE_URL_YEE is not set in your environment / .env file.")
	return _normalize_async_url(raw_url)


async def _main_async(url, connect_args, *, dry_run: bool) -> None:
	engine = create_async_engine(url, connect_args=connect_args, pool_pre_ping=True)
	session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
	try:
		async with session_factory() as session:
			await _reassign(session, dry_run=dry_run)
	finally:
		await engine.dispose()


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Move test auditors from the demo manager account to another manager's account."
	)
	parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
	parser.add_argument("--dry-run", action="store_true", help="Show what would happen without writing.")
	args = parser.parse_args()

	# Resolve and show the actual target database BEFORE prompting, so the
	# operator can see which host/database a (possibly stale) .env points at
	# instead of confirming a production write blindly.
	url, connect_args = _resolve_target_url()

	print("About to move test auditors between manager accounts:")
	print(f"  From manager: {SOURCE_MANAGER_EMAIL}")
	print(f"  To manager:   {TARGET_MANAGER_EMAIL}")
	print(f"  Project:      {TARGET_PROJECT_NAME}")
	print(f"  Place:        {TARGET_PLACE_NAME}")
	print(f"  Target DB:    {url.host}/{url.database}")
	print()

	if not args.dry_run and not args.yes:
		answer = input("This writes to the database shown above. Continue? [y/N] ").strip().lower()
		if answer not in {"y", "yes"}:
			print("Aborted.")
			sys.exit(1)

	asyncio.run(_main_async(url, connect_args, dry_run=args.dry_run))


if __name__ == "__main__":
	main()

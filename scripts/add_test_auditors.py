#!/usr/bin/env python3
"""Add fully-onboarded test auditors under a manager account (run locally).

This reproduces the end state of a manager manually inviting an auditor, the
auditor accepting the invite, and the auditor completing their profile -- the
same rows those flows create in the YEE database:

  * an ``auditor_invites`` row (accepted) attributed to the manager
  * a ``users`` row (AUDITOR, email-verified, approved, profile-completed)
  * an ``auditor_profiles`` row linked to the manager's account, with the
    required onboarding fields filled in

By default it creates ``test-auditor-03@example.org`` ... ``test-auditor-20@example.org``
(18 auditors) named "Test Auditor 3" ... "Test Auditor 20" under the manager
``manager-demo@yee.local`` with the password ``DemoPass123!``.

The script targets the production YEE database via ``DATABASE_URL_YEE`` in your
local ``.env`` file. It is idempotent: auditors whose email already exists are
skipped.

Usage (from the repo root, inside your virtualenv)::

    python -m scripts.add_test_auditors            # prompts before writing
    python -m scripts.add_test_auditors --yes      # skip the confirmation
    python -m scripts.add_test_auditors --dry-run  # show what would happen

Notes on the required onboarding fields:
  * name        -> users.name and auditor_profiles.full_name
  * password    -> users.password_hash (PBKDF2, the app's own hasher)
  * role title  -> auditor_profiles.role (filled with random data)
  * industry    -> the YEE schema has no dedicated industry column, so the
                   randomly chosen industry is folded into the role title
                   (e.g. "Healthcare Field Auditor"). Age range, gender and
                   country are also filled so the profile looks fully onboarded,
                   matching the seeded demo auditors.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import secrets
import ssl
import sys
import uuid
from datetime import datetime, timedelta, timezone

import certifi

from dotenv import find_dotenv, load_dotenv
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Import only modules that do NOT build the app's database engines on import,
# so this script can run against DATABASE_URL_YEE without needing the Playspace
# URL to be configured.
from app.auth_security import hash_password, hash_verification_token
from app.models import (
	Account,
	AccountType,
	AuditorAssignment,
	AuditorInvite,
	AuditorProfile,
	Project,
	ProjectPlace,
	User,
)

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

MANAGER_EMAIL = "manager-demo@yee.local"
AUDITOR_PASSWORD = "DemoPass123!"
EMAIL_DOMAIN = "example.org"
START_INDEX = 2
END_INDEX = 30  # inclusive -> 02..30 == 29 auditors

# Optional place assignment for every created (or existing) auditor.
ASSIGNMENT_ACCOUNT_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
ASSIGNMENT_PROJECT_ID = uuid.UUID("fa77ef3d-29de-4982-a4e1-08adff99f242")
ASSIGNMENT_PLACE_ID = uuid.UUID("411ab922-7432-49f0-b607-3c5fa87dc6ad")

# Random profile data used to "complete" each auditor's onboarding fields.
INDUSTRIES = [
	"Healthcare",
	"Education",
	"Urban Planning",
	"Public Health",
	"Recreation",
	"Community Development",
	"Environmental Design",
	"Social Services",
	"Nonprofit",
	"Government",
]
ROLE_TITLES = [
	"Field Auditor",
	"Research Assistant",
	"Program Coordinator",
	"Community Facilitator",
	"Evaluation Specialist",
	"Site Inspector",
	"Outreach Associate",
	"Data Analyst",
]
AGE_RANGES = ["18-24", "25-34", "35-44", "45-54", "55-64", "65+"]
GENDERS = ["Woman", "Man", "Non-binary", "Prefer not to say"]
COUNTRIES = ["United States", "Canada", "United Kingdom"]


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


def _next_auditor_code_counter(existing_codes: list[str]) -> int:
	"""Return the next numeric suffix for AUD### codes (matches app/auth.py)."""

	max_suffix = 0
	for code in existing_codes:
		match = re.search(r"(\d+)$", code or "")
		if match is not None:
			max_suffix = max(max_suffix, int(match.group(1)))
	return max_suffix + 1


def _planned_auditors() -> list[tuple[str, str, int]]:
	"""Return (email, name, index) tuples for every auditor to create."""

	planned = []
	for index in range(START_INDEX, END_INDEX + 1):
		email = f"test-auditor-{index:02d}@{EMAIL_DOMAIN}"
		name = f"Test Auditor {index}"
		planned.append((email, name, index))
	return planned


async def _ensure_assignment_target(
	session: AsyncSession,
	*,
	account_id: uuid.UUID,
	project_id: uuid.UUID,
	place_id: uuid.UUID,
) -> None:
	"""Verify the project/place pair exists under the account before assigning."""

	project = await session.get(Project, project_id)
	if project is None or project.account_id != account_id:
		raise SystemExit(f"Project {project_id} was not found under account {account_id}.")

	link = (
		await session.execute(
			select(ProjectPlace).where(
				ProjectPlace.project_id == project_id,
				ProjectPlace.place_id == place_id,
			)
		)
	).scalar_one_or_none()
	if link is None:
		raise SystemExit(f"project_places row missing for project={project_id}, place={place_id}.")


async def _ensure_auditor_assignment(
	session: AsyncSession,
	*,
	profile: AuditorProfile,
	project_id: uuid.UUID,
	place_id: uuid.UUID,
	now: datetime,
	dry_run: bool,
) -> bool:
	"""Create the assignment row when missing; return True if one was added."""

	existing_assignment = (
		await session.execute(
			select(AuditorAssignment).where(
				AuditorAssignment.auditor_profile_id == profile.id,
				AuditorAssignment.project_id == project_id,
				AuditorAssignment.place_id == place_id,
			)
		)
	).scalar_one_or_none()
	if existing_assignment is not None:
		return False

	if dry_run:
		return True

	session.add(
		AuditorAssignment(
			auditor_profile_id=profile.id,
			project_id=project_id,
			place_id=place_id,
			assigned_at=now,
		)
	)
	await session.flush()
	return True


async def _create_auditors(session: AsyncSession, *, dry_run: bool) -> None:
	# 1. Resolve the manager and their account.
	manager = (await session.execute(select(User).where(User.email == MANAGER_EMAIL))).scalar_one_or_none()
	if manager is None:
		raise SystemExit(f"Manager user '{MANAGER_EMAIL}' was not found in this database.")
	if manager.account_type not in {AccountType.MANAGER, AccountType.ADMIN}:
		raise SystemExit(f"User '{MANAGER_EMAIL}' is a {manager.account_type.value}, not a manager/admin.")
	account_id = manager.account_id
	if account_id is None:
		raise SystemExit(f"Manager '{MANAGER_EMAIL}' has no account/organization configured.")

	account = await session.get(Account, account_id)
	org_name = account.name if account is not None else "(unknown organization)"
	print(f"Manager:      {MANAGER_EMAIL} (user_id={manager.id})")
	print(f"Organization: {org_name} (account_id={account_id})")
	print(f"Assignment:   project={ASSIGNMENT_PROJECT_ID}, place={ASSIGNMENT_PLACE_ID}")
	print()

	if account_id != ASSIGNMENT_ACCOUNT_ID:
		raise SystemExit(
			f"Manager account {account_id} does not match configured assignment account {ASSIGNMENT_ACCOUNT_ID}."
		)

	await _ensure_assignment_target(
		session,
		account_id=ASSIGNMENT_ACCOUNT_ID,
		project_id=ASSIGNMENT_PROJECT_ID,
		place_id=ASSIGNMENT_PLACE_ID,
	)

	# 2. Seed the auditor_code counter from existing codes.
	existing_codes = list((await session.execute(select(AuditorProfile.auditor_code))).scalars().all())
	code_counter = _next_auditor_code_counter(existing_codes)

	now = datetime.now(timezone.utc)
	created = 0
	skipped = 0
	assigned = 0

	for email, name, index in _planned_auditors():
		# Idempotency: skip if a user OR an auditor profile with this email
		# already exists. Checking both tables avoids an integrity error when a
		# partial cleanup left an orphaned auditor_profiles row (its email and
		# auditor_code are unique) behind a deleted users row.
		existing_user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
		if existing_user is not None:
			profile = (
				await session.execute(select(AuditorProfile).where(AuditorProfile.user_id == existing_user.id))
			).scalar_one_or_none()
			if profile is None:
				print(f"SKIP   {email} -> user exists but has no auditor profile")
				skipped += 1
				continue

			added_assignment = await _ensure_auditor_assignment(
				session,
				profile=profile,
				project_id=ASSIGNMENT_PROJECT_ID,
				place_id=ASSIGNMENT_PLACE_ID,
				now=now,
				dry_run=dry_run,
			)
			if added_assignment:
				assigned += 1
				print(f"ASSIGN {email} -> {profile.auditor_code}")
			else:
				print(f"SKIP   {email} -> user already exists")
			skipped += 1
			continue
		existing_profile = (
			await session.execute(select(AuditorProfile).where(AuditorProfile.email == email))
		).scalar_one_or_none()
		if existing_profile is not None:
			print(f"SKIP   {email} -> auditor profile already exists (orphaned; no user row)")
			skipped += 1
			continue

		auditor_code = f"AUD{code_counter:03d}"
		code_counter += 1

		# Deterministic-ish random data per auditor.
		industry = INDUSTRIES[index % len(INDUSTRIES)]
		title = ROLE_TITLES[index % len(ROLE_TITLES)]
		role = f"{industry} {title}"
		age_range = AGE_RANGES[index % len(AGE_RANGES)]
		gender = GENDERS[index % len(GENDERS)]
		country = COUNTRIES[index % len(COUNTRIES)]

		if dry_run:
			print(f"DRYRUN {email} -> name='{name}', code={auditor_code}, role='{role}', assign place")
			created += 1
			assigned += 1
			continue

		# --- users row (accept invite + complete profile end state) ---------- #
		user = User(
			email=email,
			password_hash=hash_password(AUDITOR_PASSWORD),
			account_id=account_id,
			account_type=AccountType.AUDITOR,
			name=name,
			email_verified=True,
			email_verified_at=now,
			failed_login_attempts=0,
			approved=True,
			approved_at=now,
			profile_completed=True,
			profile_completed_at=now,
			last_login_at=now,
		)
		session.add(user)
		await session.flush()

		# --- auditor_profiles row (fully completed onboarding fields) -------- #
		profile = AuditorProfile(
			account_id=account_id,
			user_id=user.id,
			auditor_code=auditor_code,
			email=email,
			full_name=name,
			age_range=age_range,
			gender=gender,
			country=country,
			role=role,
			terms_accepted_at=now,
		)
		session.add(profile)
		await session.flush()

		# --- auditor_invites row (accepted invite from the manager) --------- #
		invite = AuditorInvite(
			account_id=account_id,
			invited_by_user_id=manager.id,
			auditor_id=profile.id,
			email=email,
			token_hash=hash_verification_token(secrets.token_urlsafe(32)),
			expires_at=now + timedelta(days=7),
			accepted_at=now,
		)
		session.add(invite)
		await session.flush()

		added_assignment = await _ensure_auditor_assignment(
			session,
			profile=profile,
			project_id=ASSIGNMENT_PROJECT_ID,
			place_id=ASSIGNMENT_PLACE_ID,
			now=now,
			dry_run=False,
		)
		if added_assignment:
			assigned += 1

		print(f"CREATE {email} -> name='{name}', code={auditor_code}, role='{role}', assigned")
		created += 1

	if dry_run:
		await session.rollback()
		print(f"\nDry run complete. Would create {created}, assign {assigned}, skip {skipped}. No changes written.")
		return

	await session.commit()
	print(f"\nDone. Created {created} auditor(s), assigned {assigned}, skipped {skipped} existing.")


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
			await _create_auditors(session, dry_run=dry_run)
	finally:
		await engine.dispose()


def main() -> None:
	parser = argparse.ArgumentParser(description="Add fully-onboarded test auditors to the YEE database.")
	parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
	parser.add_argument("--dry-run", action="store_true", help="Show what would happen without writing.")
	args = parser.parse_args()

	# Resolve and show the actual target database BEFORE prompting, so the
	# operator can see which host/database a (possibly stale) .env points at
	# instead of confirming a production write blindly.
	url, connect_args = _resolve_target_url()

	planned = _planned_auditors()
	print("About to add the following test auditors (idempotent; existing emails are skipped):")
	print(f"  {planned[0][0]} ('{planned[0][1]}') ... {planned[-1][0]} ('{planned[-1][1]}')")
	print(f"  {len(planned)} auditors, manager '{MANAGER_EMAIL}', password '{AUDITOR_PASSWORD}'")
	print(f"  Target DB: {url.host}/{url.database}")
	print()

	if not args.dry_run and not args.yes:
		answer = input("This writes to the database shown above. Continue? [y/N] ").strip().lower()
		if answer not in {"y", "yes"}:
			print("Aborted.")
			sys.exit(1)

	asyncio.run(_main_async(url, connect_args, dry_run=args.dry_run))


if __name__ == "__main__":
	main()

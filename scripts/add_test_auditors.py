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
import sys
from datetime import datetime, timedelta, timezone

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
	AuditorInvite,
	AuditorProfile,
	User,
)

# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #

MANAGER_EMAIL = "manager-demo@yee.local"
AUDITOR_PASSWORD = "DemoPass123!"
EMAIL_DOMAIN = "example.org"
START_INDEX = 3
END_INDEX = 20  # inclusive -> 03..20 == 18 auditors

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
		connect_args["ssl"] = True
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
	print()

	# 2. Seed the auditor_code counter from existing codes.
	existing_codes = list((await session.execute(select(AuditorProfile.auditor_code))).scalars().all())
	code_counter = _next_auditor_code_counter(existing_codes)

	now = datetime.now(timezone.utc)
	created = 0
	skipped = 0

	for email, name, index in _planned_auditors():
		# Idempotency: skip if a user with this email already exists.
		existing_user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
		if existing_user is not None:
			print(f"SKIP   {email} -> user already exists")
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
			print(f"DRYRUN {email} -> name='{name}', code={auditor_code}, role='{role}'")
			created += 1
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

		print(f"CREATE {email} -> name='{name}', code={auditor_code}, role='{role}'")
		created += 1

	if dry_run:
		await session.rollback()
		print(f"\nDry run complete. Would create {created}, skip {skipped}. No changes written.")
		return

	await session.commit()
	print(f"\nDone. Created {created} auditor(s), skipped {skipped} existing.")


async def _main_async(dry_run: bool) -> None:
	load_dotenv(find_dotenv())
	raw_url = os.getenv("DATABASE_URL_YEE", "").strip()
	if not raw_url:
		raise SystemExit("DATABASE_URL_YEE is not set in your environment / .env file.")

	url, connect_args = _normalize_async_url(raw_url)
	print(f"Target DB:    {url.host}/{url.database}")
	print()

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

	planned = _planned_auditors()
	print("About to add the following test auditors (idempotent; existing emails are skipped):")
	print(f"  {planned[0][0]} ('{planned[0][1]}') ... {planned[-1][0]} ('{planned[-1][1]}')")
	print(f"  {len(planned)} auditors, manager '{MANAGER_EMAIL}', password '{AUDITOR_PASSWORD}'")
	print()

	if not args.dry_run and not args.yes:
		answer = input("This writes to the database in DATABASE_URL_YEE. Continue? [y/N] ").strip().lower()
		if answer not in {"y", "yes"}:
			print("Aborted.")
			sys.exit(1)

	asyncio.run(_main_async(dry_run=args.dry_run))


if __name__ == "__main__":
	main()

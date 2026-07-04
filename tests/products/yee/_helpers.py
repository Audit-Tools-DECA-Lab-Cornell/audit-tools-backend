"""Shared module-level constants and helpers for YEE endpoint tests.

These back the focused YEE integration suites (audit lifecycle, manager
workflows, auth flows, and submit durability). They provide the deterministic
seed identifiers, bearer-auth helpers, login shortcuts, and the async database
utilities each suite reuses against the per-product YEE schema.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import TypedDict

from fastapi.testclient import TestClient
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.auth as auth_module
from app.auth_security import hash_verification_token
from app.demo_account_reconciler import reconcile_protected_yee_demo_accounts
from app.models import Account, Auditor, ManagerInvite, ManagerProfile, Project, User

# Matches the deterministic YEE seed (see app/seed.py).
SEED_AUDITOR_EMAIL = "auditor-demo-1@yee.local"
SEED_MANAGER_EMAIL = "manager-demo@yee.local"
SEED_AUDITOR_THREE_EMAIL = "auditor-demo-3@yee.local"
SEED_PASSWORD = "DemoPass123!"


def _bearer_headers(access_token: str) -> dict[str, str]:
	"""Build bearer auth headers for session-backed authorization."""

	return {"Authorization": f"bearer {access_token}"}


def _login_auditor(client: TestClient, email: str = SEED_AUDITOR_EMAIL, password: str = SEED_PASSWORD) -> str:
	"""Login a seeded YEE auditor account and return a bearer token."""

	response = client.post("/yee/auth/login", json={"email": email, "password": password})
	assert response.status_code == 200, response.text
	return response.json()["access_token"]


def _unique_suffix() -> str:
	"""Return a short unique token so each test owns an isolated org and emails."""

	return uuid.uuid4().hex[:10]


async def _verify_user_email(
	session_factory: async_sessionmaker[AsyncSession],
	email: str,
) -> None:
	"""Mark a freshly signed-up user as email-verified so it can authenticate."""

	async with session_factory() as session:
		user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
		assert user is not None
		user.email_verified = True
		user.email_verified_at = datetime.now(timezone.utc)
		await session.commit()


class PrimaryManagerSignup(TypedDict):
	"""Result of signing up a fresh primary manager for an isolated org."""

	email: str
	organization: str
	headers: dict[str, str]


def _signup_primary_manager(
	client: TestClient,
	session_factory: async_sessionmaker[AsyncSession],
) -> PrimaryManagerSignup:
	"""Sign up a fresh manager and return its email, org name, and bearer headers.

	Each stateful manager test gets its own organization where it is the sole
	primary manager with zero secondaries, so invite limits, listings, and
	removals stay deterministic and never accumulate across the session-scoped
	client (unlike reusing the shared seeded demo manager).
	"""

	suffix = _unique_suffix()
	email = f"primary-{suffix}@example.org"
	organization = f"Isolated Org {suffix}"
	signup = client.post(
		"/yee/auth/signup",
		json={
			"email": email,
			"password": SEED_PASSWORD,
			"name": f"Primary Manager {suffix}",
			"organization": organization,
			"account_type": "MANAGER",
			"website": "",
		},
	)
	assert signup.status_code == 201, signup.text
	asyncio.run(_verify_user_email(session_factory, email))
	login = client.post("/yee/auth/login", json={"email": email, "password": SEED_PASSWORD})
	assert login.status_code == 200, login.text
	assert login.json()["user"]["is_primary_manager"] is True
	return {
		"email": email,
		"organization": organization,
		"headers": _bearer_headers(login.json()["access_token"]),
	}


async def _load_manager_signup_snapshot(
	session_factory: async_sessionmaker[AsyncSession],
	email: str,
) -> tuple[User | None, int, str | None]:
	"""Return the signed-up user, current account count, and linked account name."""

	async with session_factory() as session:
		user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
		account_count = int((await session.execute(select(func.count(Account.id)))).scalar_one() or 0)
		account_name = None
		if user is not None and user.account_id is not None:
			account = await session.get(Account, user.account_id)
			account_name = account.name if account is not None else None
	return user, account_count, account_name


async def _create_verified_manager_user(
	session_factory: async_sessionmaker[AsyncSession],
	*,
	email: str,
	password: str,
	name: str,
) -> None:
	"""Create a disposable verified manager account for auth-flow assertions."""

	async with session_factory() as session:
		account = Account(
			id=uuid.uuid4(),
			name=f"{name} Organization",
			email=email,
			account_type=auth_module.AccountType.MANAGER,
		)
		user = User(
			id=uuid.uuid4(),
			email=email,
			password_hash=auth_module.hash_password(password),
			account_id=account.id,
			account_type=auth_module.AccountType.MANAGER,
			name=name,
			email_verified=True,
			email_verified_at=datetime.now(timezone.utc),
			failed_login_attempts=0,
			approved=True,
			approved_at=datetime.now(timezone.utc),
			profile_completed=False,
		)
		profile = ManagerProfile(
			id=uuid.uuid4(),
			account_id=account.id,
			user_id=user.id,
			full_name=name,
			email=email,
			organization=account.name,
			is_primary=True,
		)
		session.add_all([account, user, profile])
		await session.commit()


async def _create_legacy_manager_invite_for_existing_manager(
	session_factory: async_sessionmaker[AsyncSession],
	*,
	email: str,
	account_id: str,
	invited_by_user_id: str,
	token: str,
) -> None:
	"""Create a pending invite to simulate a legacy/stray manager invite row."""

	async with session_factory() as session:
		invite = ManagerInvite(
			account_id=account_id,
			invited_by_user_id=invited_by_user_id,
			email=email,
			token_hash=hash_verification_token(token),
			expires_at=datetime.now(timezone.utc) + timedelta(days=7),
		)
		session.add(invite)
		await session.commit()


async def _delete_user_by_email(
	session_factory: async_sessionmaker[AsyncSession],
	email: str,
) -> None:
	"""Delete one user row to simulate auth drift in the live database."""

	async with session_factory() as session:
		user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
		if user is None:
			return
		if user.account_id is not None:
			replacement_id = (
				await session.execute(
					select(User.id)
					.where(
						User.account_id == user.account_id,
						User.id != user.id,
					)
					.order_by(User.created_at.asc())
					.limit(1)
				)
			).scalar_one_or_none()
			if replacement_id is not None:
				await session.execute(
					update(Project)
					.where(Project.created_by_user_id == user.id)
					.values(created_by_user_id=replacement_id)
				)
		await session.execute(delete(User).where(User.id == user.id))
		await session.commit()


async def _reconcile_demo_accounts(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, int]:
	"""Run protected demo reconciliation through a real async session."""

	async with session_factory() as session:
		return await reconcile_protected_yee_demo_accounts(session)


async def _load_manager_profile_by_email(
	session_factory: async_sessionmaker[AsyncSession],
	email: str,
) -> ManagerProfile | None:
	"""Fetch one manager profile row by email for assertions."""

	async with session_factory() as session:
		return (await session.execute(select(ManagerProfile).where(ManagerProfile.email == email))).scalar_one_or_none()


async def _load_auditor_profile_by_user_email(
	session_factory: async_sessionmaker[AsyncSession],
	email: str,
) -> Auditor | None:
	"""Fetch one self-created auditor profile via the linked user email."""

	async with session_factory() as session:
		user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
		if user is None:
			return None
		return (await session.execute(select(Auditor).where(Auditor.user_id == user.id))).scalar_one_or_none()

"""Non-destructive reconciliation for protected YEE demo accounts."""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.demo_data import DEMO_ACCOUNT_ID
from app.demo_accounts import get_protected_yee_demo_emails
from app.models import Account, User
from app.seed import _build_yee_entities

logger = logging.getLogger(__name__)


def _seeded_demo_account_and_users() -> tuple[Account | None, list[User]]:
	"""Load the canonical seeded YEE account/user rows for protected demos."""

	protected = set(get_protected_yee_demo_emails())
	account: Account | None = None
	users: list[User] = []
	for entity in _build_yee_entities():
		if isinstance(entity, Account) and entity.id == DEMO_ACCOUNT_ID:
			account = entity
		elif isinstance(entity, User) and entity.email.lower() in protected:
			users.append(entity)
	return account, users


async def reconcile_protected_yee_demo_accounts(session: AsyncSession) -> dict[str, int]:
	"""Restore canonical protected YEE demo auth rows without destructive reseeding."""

	seed_account, seed_users = _seeded_demo_account_and_users()
	if seed_account is None:
		logger.warning("Protected YEE demo reconciliation skipped because the seeded account definition is missing.")
		return {"accounts_created": 0, "accounts_updated": 0, "users_created": 0, "users_updated": 0}

	summary = {
		"accounts_created": 0,
		"accounts_updated": 0,
		"users_created": 0,
		"users_updated": 0,
	}

	account = await session.get(Account, seed_account.id)
	if account is None:
		account = Account(
			id=seed_account.id,
			name=seed_account.name,
			email=seed_account.email,
			account_type=seed_account.account_type,
			created_at=seed_account.created_at,
		)
		session.add(account)
		await session.flush()
		summary["accounts_created"] += 1
	else:
		account.name = seed_account.name
		account.email = seed_account.email
		account.account_type = seed_account.account_type
		summary["accounts_updated"] += 1

	existing_users = {
		user.email.lower(): user
		for user in (
			await session.execute(
				select(User).where(User.email.in_([seed_user.email for seed_user in seed_users]))
			)
		).scalars()
	}

	for seed_user in seed_users:
		existing = existing_users.get(seed_user.email.lower())
		if existing is None:
			session.add(
				User(
					id=seed_user.id,
					email=seed_user.email,
					password_hash=seed_user.password_hash,
					account_id=seed_user.account_id,
					account_type=seed_user.account_type,
					name=seed_user.name,
					email_verified=seed_user.email_verified,
					email_verified_at=seed_user.email_verified_at,
					failed_login_attempts=0,
					approved=seed_user.approved,
					approved_at=seed_user.approved_at,
					profile_completed=seed_user.profile_completed,
					profile_completed_at=seed_user.profile_completed_at,
					last_login_at=seed_user.last_login_at,
					created_at=seed_user.created_at,
				)
			)
			summary["users_created"] += 1
			continue

		existing.password_hash = seed_user.password_hash
		existing.account_id = seed_user.account_id
		existing.account_type = seed_user.account_type
		existing.name = seed_user.name
		existing.email_verified = seed_user.email_verified
		existing.email_verified_at = seed_user.email_verified_at
		existing.failed_login_attempts = 0
		existing.approved = seed_user.approved
		existing.approved_at = seed_user.approved_at
		existing.profile_completed = seed_user.profile_completed
		existing.profile_completed_at = seed_user.profile_completed_at
		summary["users_updated"] += 1

	await session.commit()
	return summary

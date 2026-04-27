"""
Self-service read operations for the current Playspace user.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Account, AccountType, AuditorProfile, ManagerProfile, User


class PlayspaceMeService:
	"""Read-only current-user queries for profile and account details."""

	def __init__(self, *, session: AsyncSession) -> None:
		self._session = session

	async def get_my_identity(
		self,
		*,
		user_id: uuid.UUID,
	) -> tuple[str, str, str, str | None, uuid.UUID]:
		"""Resolve the logged-in user's display name, email, account type, org name, and account ID.

		Returns profile-level data — the person's own name and email — not the
		organisational Account record.

		Returns a tuple of ``(name, email, account_type_value, organization, account_id)``.
		"""

		user_result = await self._session.execute(select(User).where(User.id == user_id))
		user = user_result.scalar_one_or_none()
		if user is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="User not found.",
			)

		account_id = user.account_id
		if account_id is None:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Account identity is required for self-service operations.",
			)

		# Resolve from the profile table matching the user's account type.
		if user.account_type == AccountType.MANAGER:
			profile_result = await self._session.execute(
				select(ManagerProfile).where(ManagerProfile.user_id == user.id)
			)
			profile = profile_result.scalar_one_or_none()
			if profile is not None:
				return (
					profile.full_name,
					profile.email,
					user.account_type.value,
					profile.organization,
					account_id,
				)

		elif user.account_type == AccountType.AUDITOR:
			profile_result = await self._session.execute(
				select(AuditorProfile).where(AuditorProfile.user_id == user.id)
			)
			profile = profile_result.scalar_one_or_none()
			if profile is not None:
				return (
					profile.full_name,
					profile.email or user.email,
					user.account_type.value,
					None,
					account_id,
				)

		# Admin or profile-not-yet-created fallback: use the User/Account record.
		account_result = await self._session.execute(select(Account).where(Account.id == account_id))
		account = account_result.scalar_one_or_none()
		return (
			user.name or (account.name if account is not None else "Unknown"),
			user.email,
			user.account_type.value,
			account.name if account is not None else None,
			account_id,
		)

	async def get_auditor_profile(self, *, account_id: uuid.UUID) -> AuditorProfile:
		"""Fetch an auditor profile for the given account or raise 404."""

		result = await self._session.execute(select(AuditorProfile).where(AuditorProfile.account_id == account_id))
		profile = result.scalar_one_or_none()
		if profile is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Auditor profile not found for this account.",
			)
		return profile

"""
Self-service read/write operations for the current Playspace user.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth_security import hash_password, verify_password
from app.models import Account, AccountType, AuditorProfile, ManagerProfile, User
from app.products.playspace.schemas.me import AuditorProfileSelfUpdateRequest


class PlayspaceMeService:
	"""Read and write operations scoped to the currently authenticated user."""

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
				# Resolve the organisation name from the manager's Account.
				account_result = await self._session.execute(select(Account).where(Account.id == account_id))
				account = account_result.scalar_one_or_none()
				org_name = account.name if account is not None else None
				return (
					profile.full_name,
					profile.email or user.email,
					user.account_type.value,
					org_name,
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

	async def get_auditor_profile(self, *, user_id: uuid.UUID) -> AuditorProfile:
		"""Fetch an auditor profile for the given user or raise 404.

		Lookup is keyed by ``user_id`` because multiple auditors now share the
		same ``account_id`` (the manager's organisation account).
		"""

		result = await self._session.execute(select(AuditorProfile).where(AuditorProfile.user_id == user_id))
		profile = result.scalar_one_or_none()
		if profile is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Auditor profile not found for this user.",
			)
		return profile

	async def change_password(
		self,
		*,
		user_id: uuid.UUID,
		current_password: str,
		new_password: str,
	) -> User:
		"""Verify the current password and replace it with the new one."""

		user_result = await self._session.execute(select(User).where(User.id == user_id))
		user = user_result.scalar_one_or_none()
		if user is None:
			raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found.")

		if not verify_password(current_password, user.password_hash):
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="Current password is incorrect.",
			)

		user.password_hash = hash_password(new_password)
		await self._session.commit()
		return user

	async def update_auditor_profile(
		self,
		*,
		user_id: uuid.UUID,
		payload: AuditorProfileSelfUpdateRequest,
	) -> AuditorProfile:
		"""Apply non-null fields from the payload to the auditor profile."""

		profile = await self.get_auditor_profile(user_id=user_id)

		if payload.full_name is not None:
			profile.full_name = payload.full_name
		if payload.email is not None:
			profile.email = payload.email
		if payload.phone is not None:
			profile.phone = payload.phone
		if payload.gender is not None:
			profile.gender = payload.gender
		if payload.age_range is not None:
			profile.age_range = payload.age_range
		if payload.city is not None:
			profile.city = payload.city
		if payload.province is not None:
			profile.province = payload.province
		if payload.country is not None:
			profile.country = payload.country
		if payload.role is not None:
			profile.role = payload.role

		await self._session.commit()
		await self._session.refresh(profile)
		return profile

	async def complete_onboarding(self, *, user_id: uuid.UUID) -> AuditorProfile:
		"""Mark terms as accepted and set profile_completed on the User record."""

		now = datetime.now(timezone.utc)

		profile_result = await self._session.execute(select(AuditorProfile).where(AuditorProfile.user_id == user_id))
		profile = profile_result.scalar_one_or_none()
		if profile is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Auditor profile not found.",
			)
		profile.terms_accepted_at = now

		user_result = await self._session.execute(select(User).where(User.id == user_id))
		user = user_result.scalar_one_or_none()
		if user is not None:
			user.profile_completed = True
			user.profile_completed_at = now

		await self._session.commit()
		await self._session.refresh(profile)
		return profile

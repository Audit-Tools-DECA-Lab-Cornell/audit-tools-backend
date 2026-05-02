"""
Self-service current-user endpoints for Playspace.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.core.actors import CurrentUserContext
from app.products.playspace.routes.dependencies import (
	CURRENT_USER_DEPENDENCY,
	SESSION_DEPENDENCY,
)
from app.products.playspace.schemas.me import (
	AuditorProfileSelfUpdateRequest,
	ChangePasswordRequest,
	ManagerProfileSelfUpdateRequest,
	MyAccountResponse,
	MyAuditorProfileResponse,
	MyManagerProfileResponse,
)
from app.products.playspace.services.me import PlayspaceMeService

router: APIRouter = APIRouter(tags=["playspace-me"])


def _require_user_id(current_user: CurrentUserContext) -> uuid.UUID:
	"""Extract user_id from the current user context or raise 403."""

	if current_user.user_id is None:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="Authenticated user identity is required for self-service operations.",
		)
	return current_user.user_id


def _require_account_id(current_user: CurrentUserContext) -> uuid.UUID:
	"""Extract account_id from the current user context or raise 403."""

	if current_user.account_id is None:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="Account identity is required for self-service operations.",
		)
	return current_user.account_id


@router.get("/me")
async def get_my_account(
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	session=SESSION_DEPENDENCY,
) -> MyAccountResponse:
	"""Return the current user's profile identity for settings and display."""

	user_id = _require_user_id(current_user)
	service = PlayspaceMeService(session=session)
	name, email, account_type, organization, account_id = await service.get_my_identity(user_id=user_id)

	return MyAccountResponse(
		account_id=account_id,
		name=name,
		email=email,
		account_type=account_type,
		organization=organization,
	)


@router.get("/me/auditor-profile")
async def get_my_auditor_profile(
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	session=SESSION_DEPENDENCY,
) -> MyAuditorProfileResponse:
	"""Return the current user's auditor profile."""

	user_id = _require_user_id(current_user)
	service = PlayspaceMeService(session=session)
	profile = await service.get_auditor_profile(user_id=user_id)

	return MyAuditorProfileResponse(
		profile_id=profile.id,
		auditor_code=profile.auditor_code,
		full_name=profile.full_name,
		email=profile.email,
		phone=profile.phone,
		age_range=profile.age_range,
		gender=profile.gender,
		city=profile.city,
		province=profile.province,
		country=profile.country,
		role=profile.role,
	)


@router.patch("/me/auditor-profile")
async def update_my_auditor_profile(
	payload: AuditorProfileSelfUpdateRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	session=SESSION_DEPENDENCY,
) -> MyAuditorProfileResponse:
	"""Update mutable fields on the current auditor's profile."""

	user_id = _require_user_id(current_user)
	service = PlayspaceMeService(session=session)
	profile = await service.update_auditor_profile(user_id=user_id, payload=payload)

	return MyAuditorProfileResponse(
		profile_id=profile.id,
		auditor_code=profile.auditor_code,
		full_name=profile.full_name,
		email=profile.email,
		phone=profile.phone,
		age_range=profile.age_range,
		gender=profile.gender,
		city=profile.city,
		province=profile.province,
		country=profile.country,
		role=profile.role,
	)


@router.post("/me/change-password", status_code=204, response_model=None)
async def change_password(
	payload: ChangePasswordRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	session=SESSION_DEPENDENCY,
) -> None:
	"""Change the authenticated user's password after verifying the current one."""

	user_id = _require_user_id(current_user)
	service = PlayspaceMeService(session=session)
	await service.change_password(
		user_id=user_id,
		current_password=payload.current_password,
		new_password=payload.new_password,
	)


@router.get("/me/manager-profile")
async def get_my_manager_profile(
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	session=SESSION_DEPENDENCY,
) -> MyManagerProfileResponse:
	"""Return the current manager user's own profile."""

	user_id = _require_user_id(current_user)
	service = PlayspaceMeService(session=session)
	profile = await service.get_manager_profile(user_id=user_id)

	return MyManagerProfileResponse(
		profile_id=profile.id,
		full_name=profile.full_name,
		email=profile.email,
		phone=profile.phone,
		position=profile.position,
		organization=profile.organization,
		is_primary=profile.is_primary,
	)


@router.patch("/me/manager-profile")
async def update_my_manager_profile(
	payload: ManagerProfileSelfUpdateRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	session=SESSION_DEPENDENCY,
) -> MyManagerProfileResponse:
	"""Update mutable fields on the current manager user's own profile."""

	user_id = _require_user_id(current_user)
	service = PlayspaceMeService(session=session)
	profile = await service.update_manager_profile(user_id=user_id, payload=payload)

	return MyManagerProfileResponse(
		profile_id=profile.id,
		full_name=profile.full_name,
		email=profile.email,
		phone=profile.phone,
		position=profile.position,
		organization=profile.organization,
		is_primary=profile.is_primary,
	)


@router.post("/me/complete-manager-onboarding")
async def complete_manager_onboarding(
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	session=SESSION_DEPENDENCY,
) -> MyManagerProfileResponse:
	"""Mark a manager's onboarding as complete and return their updated profile."""

	user_id = _require_user_id(current_user)
	service = PlayspaceMeService(session=session)
	profile = await service.complete_manager_onboarding(user_id=user_id)

	return MyManagerProfileResponse(
		profile_id=profile.id,
		full_name=profile.full_name,
		email=profile.email,
		phone=profile.phone,
		position=profile.position,
		organization=profile.organization,
		is_primary=profile.is_primary,
	)


@router.post("/me/complete-onboarding")
async def complete_onboarding(
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	session=SESSION_DEPENDENCY,
) -> MyAuditorProfileResponse:
	"""Accept terms and mark the auditor's profile as complete."""

	user_id = _require_user_id(current_user)
	service = PlayspaceMeService(session=session)
	profile = await service.complete_onboarding(user_id=user_id)
	return MyAuditorProfileResponse(
		profile_id=profile.id,
		auditor_code=profile.auditor_code,
		full_name=profile.full_name,
		email=profile.email,
		phone=profile.phone,
		age_range=profile.age_range,
		gender=profile.gender,
		city=profile.city,
		province=profile.province,
		country=profile.country,
		role=profile.role,
	)

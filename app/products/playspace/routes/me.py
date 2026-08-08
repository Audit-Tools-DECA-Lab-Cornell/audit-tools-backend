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
	AccountDeletionPreviewResponse,
	AccountDeletionRequest,
	AuditorProfileSelfUpdateRequest,
	ChangePasswordRequest,
	ManagerProfileSelfUpdateRequest,
	MyAccountResponse,
	MyAuditorProfileResponse,
	MyManagerProfileResponse,
	PrimaryManagerTransferRequest,
)
from app.products.playspace.services.account_deletion import PlayspaceAccountDeletionService
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


@router.get("/me/account-deletion")
async def preview_account_deletion(
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	session=SESSION_DEPENDENCY,
) -> AccountDeletionPreviewResponse:
	"""Report what deleting this account would preserve, remove, or block."""

	user_id = _require_user_id(current_user)
	service = PlayspaceAccountDeletionService(session=session)
	preview = await service.preview(user_id=user_id)

	return AccountDeletionPreviewResponse(
		role=preview.role,  # type: ignore[arg-type]
		submitted_audits_preserved=preview.submitted_audits_preserved,
		draft_audits_to_delete=preview.draft_audits_to_delete,
		active_assignments_to_delete=preview.active_assignments_to_delete,
		pending_submissions=preview.pending_submissions,
		is_primary_manager=preview.is_primary_manager,
		can_delete=preview.can_delete,
		blocker=preview.blocker,
	)


@router.post("/me/account-deletion", status_code=204, response_model=None)
async def delete_my_account(
	payload: AccountDeletionRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	session=SESSION_DEPENDENCY,
) -> None:
	"""Delete the authenticated user's account, preserving submitted audits."""

	user_id = _require_user_id(current_user)
	service = PlayspaceAccountDeletionService(session=session)
	await service.delete_account(user_id=user_id, current_password=payload.current_password)


@router.post("/me/manager-profile/primary-transfer", status_code=204, response_model=None)
async def transfer_primary_manager(
	payload: PrimaryManagerTransferRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	session=SESSION_DEPENDENCY,
) -> None:
	"""Hand the organisation's primary-manager role to another manager."""

	user_id = _require_user_id(current_user)
	service = PlayspaceAccountDeletionService(session=session)
	await service.transfer_primary_manager(
		user_id=user_id,
		successor_manager_profile_id=payload.successor_manager_profile_id,
	)

"""
Manager/admin write endpoints for Playspace entities.
"""

from __future__ import annotations

import os
import uuid

from fastapi import APIRouter, Response

from app.core.actors import CurrentUserContext
from app.products.playspace.routes.dependencies import (
	CURRENT_USER_DEPENDENCY,
	MANAGEMENT_SERVICE_DEPENDENCY,
)
from app.products.playspace.schemas import (
	AccountManagementResponse,
	AccountUpdateRequest,
	AuditorProfileCreateRequest,
	AuditorProfileDetailResponse,
	AuditorProfileUpdateRequest,
	ManagerInviteCreateRequest,
	ManagerInviteCreatedResponse,
	ManagerInviteListItemResponse,
	PlaceCreateRequest,
	PlaceDetailResponse,
	PlaceUpdateRequest,
	ProjectCreateRequest,
	ProjectDetailResponse,
	ProjectUpdateRequest,
	SavePlaceReportRequest,
)
from app.products.playspace.services import PlayspaceManagementService


def _build_manager_invite_url_template() -> str:
	"""Resolve the invite URL format string.

	Uses ``AUTH_MANAGER_INVITE_URL_TEMPLATE`` when set (production), otherwise
	falls back to ``http://localhost:3000/manager-invite/{token}`` for local dev
	(the Next.js frontend default).
	"""

	template = os.getenv("AUTH_MANAGER_INVITE_URL_TEMPLATE", "").strip()
	return template or "http://localhost:3000/manager-invite/{token}"


router = APIRouter(tags=["playspace-management"])


@router.patch("/accounts/{account_id}")
async def update_account(
	account_id: uuid.UUID,
	payload: AccountUpdateRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> AccountManagementResponse:
	"""Update an account."""

	return await service.update_account(
		actor=current_user,
		account_id=account_id,
		payload=payload,
	)


@router.post("/projects", status_code=201)
async def create_project(
	payload: ProjectCreateRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> ProjectDetailResponse:
	"""Create a project."""

	return await service.create_project(actor=current_user, payload=payload)


@router.patch("/projects/{project_id}")
async def update_project(
	project_id: uuid.UUID,
	payload: ProjectUpdateRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> ProjectDetailResponse:
	"""Update a project."""

	return await service.update_project(
		actor=current_user,
		project_id=project_id,
		payload=payload,
	)


@router.delete("/projects/{project_id}", status_code=204, response_model=None, response_class=Response)
async def delete_project(
	project_id: uuid.UUID,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> None:
	"""Delete a project."""

	await service.delete_project(actor=current_user, project_id=project_id)


@router.post("/places", status_code=201)
async def create_place(
	payload: PlaceCreateRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> PlaceDetailResponse:
	"""Create a place."""

	return await service.create_place(actor=current_user, payload=payload)


@router.get("/places/{place_id}")
async def get_place(
	place_id: uuid.UUID,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> PlaceDetailResponse:
	"""Get place details."""

	return await service.get_place(actor=current_user, place_id=place_id)


@router.patch("/places/{place_id}")
async def update_place(
	place_id: uuid.UUID,
	payload: PlaceUpdateRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> PlaceDetailResponse:
	"""Update a place."""

	return await service.update_place(actor=current_user, place_id=place_id, payload=payload)


@router.delete("/places/{place_id}", status_code=204, response_model=None, response_class=Response)
async def delete_place(
	place_id: uuid.UUID,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> None:
	"""Delete a place."""

	await service.delete_place(actor=current_user, place_id=place_id)


@router.post("/places/{place_id}/place-reports", status_code=201)
async def save_place_report(
	place_id: uuid.UUID,
	payload: SavePlaceReportRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> PlaceDetailResponse:
	"""Save a place report combination to a place."""

	return await service.save_place_report(actor=current_user, place_id=place_id, payload=payload)


@router.delete("/places/{place_id}/place-reports/{report_index}")
async def delete_place_report(
	place_id: uuid.UUID,
	report_index: int,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> PlaceDetailResponse:
	"""Remove a saved place report by its list index."""

	return await service.delete_place_report(actor=current_user, place_id=place_id, report_index=report_index)


@router.post("/auditor-profiles", status_code=201)
async def create_auditor_profile(
	payload: AuditorProfileCreateRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> AuditorProfileDetailResponse:
	"""Create an auditor profile."""

	return await service.create_auditor_profile(actor=current_user, payload=payload)


@router.patch("/auditor-profiles/{auditor_profile_id}")
async def update_auditor_profile(
	auditor_profile_id: uuid.UUID,
	payload: AuditorProfileUpdateRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> AuditorProfileDetailResponse:
	"""Update an auditor profile."""

	return await service.update_auditor_profile(
		actor=current_user,
		auditor_profile_id=auditor_profile_id,
		payload=payload,
	)


@router.delete(
	"/auditor-profiles/{auditor_profile_id}",
	status_code=204,
	response_model=None,
	response_class=Response,
)
async def delete_auditor_profile(
	auditor_profile_id: uuid.UUID,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> None:
	"""Delete an auditor profile."""

	await service.delete_auditor_profile(actor=current_user, auditor_profile_id=auditor_profile_id)


@router.post("/manager-invites", status_code=201)
async def create_manager_invite(
	payload: ManagerInviteCreateRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> ManagerInviteCreatedResponse:
	"""Create a manager invite and send the invitation email."""

	return await service.create_manager_invite(
		actor=current_user,
		payload=payload,
		invite_url_template=_build_manager_invite_url_template(),
	)


@router.get("/manager-invites")
async def list_manager_invites(
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> list[ManagerInviteListItemResponse]:
	"""Return all manager invites for the current primary manager's account."""

	return await service.list_manager_invites(actor=current_user)


@router.delete(
	"/manager-invites/{invite_id}",
	status_code=204,
	response_model=None,
	response_class=Response,
)
async def revoke_manager_invite(
	invite_id: uuid.UUID,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> None:
	"""Delete a pending manager invite, preventing acceptance."""

	await service.revoke_manager_invite(actor=current_user, invite_id=invite_id)


@router.post("/manager-invites/{invite_id}/resend")
async def resend_manager_invite(
	invite_id: uuid.UUID,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> ManagerInviteListItemResponse:
	"""Regenerate the invite token and re-send the invitation email."""

	return await service.resend_manager_invite(
		actor=current_user,
		invite_id=invite_id,
		invite_url_template=_build_manager_invite_url_template(),
	)

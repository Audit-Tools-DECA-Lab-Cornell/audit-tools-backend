"""
Auditor assignment endpoints for Playspace.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.core.actors import CurrentUserContext
from app.products.playspace.routes.dependencies import (
	AUDIT_SERVICE_DEPENDENCY,
	CURRENT_USER_DEPENDENCY,
)
from app.products.playspace.schemas import (
	AssignmentResponse,
	AssignmentWriteRequest,
	BulkAssignmentResponse,
	BulkAssignmentWriteRequest,
)
from app.products.playspace.services import PlayspaceAuditService

######################################################################################
############################### Assignment Endpoints #################################
######################################################################################

router = APIRouter(tags=["playspace"])


@router.get("/auditor-profiles/{auditor_profile_id}/assignments")
async def list_auditor_assignments(
	auditor_profile_id: uuid.UUID,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceAuditService = AUDIT_SERVICE_DEPENDENCY,
) -> list[AssignmentResponse]:
	"""List assignments and place-scoped playspace roles for an auditor profile."""

	return await service.list_assignments(actor=current_user, auditor_profile_id=auditor_profile_id)


@router.post(
	"/auditor-profiles/{auditor_profile_id}/assignments",
	status_code=201,
)
async def create_auditor_assignment(
	auditor_profile_id: uuid.UUID,
	payload: AssignmentWriteRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceAuditService = AUDIT_SERVICE_DEPENDENCY,
) -> AssignmentResponse:
	"""Create a manager-authored playspace assignment with audit capabilities."""

	return await service.create_assignment(
		actor=current_user,
		auditor_profile_id=auditor_profile_id,
		payload=payload,
	)


@router.patch(
	"/auditor-profiles/{auditor_profile_id}/assignments/{assignment_id}",
)
async def update_auditor_assignment(
	auditor_profile_id: uuid.UUID,
	assignment_id: uuid.UUID,
	payload: AssignmentWriteRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceAuditService = AUDIT_SERVICE_DEPENDENCY,
) -> AssignmentResponse:
	"""Update a playspace assignment scope or its place-level capabilities."""

	return await service.update_assignment(
		actor=current_user,
		auditor_profile_id=auditor_profile_id,
		assignment_id=assignment_id,
		payload=payload,
	)


@router.delete(
	"/auditor-profiles/{auditor_profile_id}/assignments/{assignment_id}",
	status_code=204,
)
async def delete_auditor_assignment(
	auditor_profile_id: uuid.UUID,
	assignment_id: uuid.UUID,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceAuditService = AUDIT_SERVICE_DEPENDENCY,
) -> None:
	"""Delete a playspace assignment."""

	await service.delete_assignment(
		actor=current_user,
		auditor_profile_id=auditor_profile_id,
		assignment_id=assignment_id,
	)


@router.post(
	"/bulk-assignments",
	status_code=201,
)
async def create_bulk_auditor_assignments(
	payload: BulkAssignmentWriteRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceAuditService = AUDIT_SERVICE_DEPENDENCY,
) -> BulkAssignmentResponse:
	"""Bulk create playspace assignments for multiple auditors and places."""

	created_count = await service.create_bulk_assignments(
		actor=current_user,
		payload=payload,
	)
	return BulkAssignmentResponse(created_count=created_count)

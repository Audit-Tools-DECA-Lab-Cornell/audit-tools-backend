"""
Manager/admin write endpoints for Playspace entities.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

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
    PlaceCreateRequest,
    PlaceDetailResponse,
    PlaceUpdateRequest,
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectUpdateRequest,
)
from app.products.playspace.services import PlayspaceManagementService

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


@router.delete("/projects/{project_id}", status_code=204)
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


@router.patch("/places/{place_id}")
async def update_place(
    place_id: uuid.UUID,
    payload: PlaceUpdateRequest,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> PlaceDetailResponse:
    """Update a place."""

    return await service.update_place(actor=current_user, place_id=place_id, payload=payload)


@router.delete("/places/{place_id}", status_code=204)
async def delete_place(
    place_id: uuid.UUID,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> None:
    """Delete a place."""

    await service.delete_place(actor=current_user, place_id=place_id)


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


@router.delete("/auditor-profiles/{auditor_profile_id}", status_code=204)
async def delete_auditor_profile(
    auditor_profile_id: uuid.UUID,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceManagementService = MANAGEMENT_SERVICE_DEPENDENCY,
) -> None:
    """Delete an auditor profile."""

    await service.delete_auditor_profile(actor=current_user, auditor_profile_id=auditor_profile_id)

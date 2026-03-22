"""
Administrator dashboard endpoints for global Playspace oversight.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.actors import CurrentUserContext
from app.products.playspace.routes.dependencies import (
    ADMIN_SERVICE_DEPENDENCY,
    CURRENT_USER_DEPENDENCY,
)
from app.products.playspace.schemas.admin import (
    AdminAccountRowResponse,
    AdminAuditRowResponse,
    AdminAuditorRowResponse,
    AdminOverviewResponse,
    AdminPlaceRowResponse,
    AdminProjectRowResponse,
    AdminSystemResponse,
)
from app.products.playspace.services.admin import PlayspaceAdminService

router = APIRouter(tags=["playspace-admin-dashboard"], prefix="/admin")


@router.get("/overview")
async def get_admin_overview(
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceAdminService = ADMIN_SERVICE_DEPENDENCY,
) -> AdminOverviewResponse:
    """Return global overview metrics."""

    return await service.get_overview(actor=current_user)


@router.get("/accounts")
async def list_admin_accounts(
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceAdminService = ADMIN_SERVICE_DEPENDENCY,
) -> list[AdminAccountRowResponse]:
    """Return global account rows."""

    return await service.list_accounts(actor=current_user)


@router.get("/projects")
async def list_admin_projects(
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceAdminService = ADMIN_SERVICE_DEPENDENCY,
) -> list[AdminProjectRowResponse]:
    """Return global project rows."""

    return await service.list_projects(actor=current_user)


@router.get("/places")
async def list_admin_places(
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceAdminService = ADMIN_SERVICE_DEPENDENCY,
) -> list[AdminPlaceRowResponse]:
    """Return global place rows."""

    return await service.list_places(actor=current_user)


@router.get("/auditors")
async def list_admin_auditors(
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceAdminService = ADMIN_SERVICE_DEPENDENCY,
) -> list[AdminAuditorRowResponse]:
    """Return global auditor rows."""

    return await service.list_auditors(actor=current_user)


@router.get("/audits")
async def list_admin_audits(
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceAdminService = ADMIN_SERVICE_DEPENDENCY,
) -> list[AdminAuditRowResponse]:
    """Return global audit rows."""

    return await service.list_audits(actor=current_user)


@router.get("/system")
def get_admin_system(
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceAdminService = ADMIN_SERVICE_DEPENDENCY,
) -> AdminSystemResponse:
    """Return system metadata for admin dashboards."""

    return service.get_system(actor=current_user)

"""
Manager dashboard endpoints for Playspace.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter

from app.core.actors import CurrentUserContext
from app.products.playspace.routes.dependencies import (
    CURRENT_USER_DEPENDENCY,
    DASHBOARD_SERVICE_DEPENDENCY,
)
from app.products.playspace.schemas import (
    AccountDetailResponse,
    AuditorSummaryResponse,
    ManagerProfileResponse,
    PlaceAuditHistoryItemResponse,
    PlaceHistoryResponse,
    PlaceSummaryResponse,
    ProjectDetailResponse,
    ProjectStatsResponse,
    ProjectSummaryResponse,
)
from app.products.playspace.services import PlayspaceDashboardService

router = APIRouter(tags=["playspace-manager-dashboard"])


@router.get("/accounts/{account_id}")
async def get_account_detail(
    account_id: uuid.UUID,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceDashboardService = DASHBOARD_SERVICE_DEPENDENCY,
) -> AccountDetailResponse:
    """Return the manager dashboard payload for a Playspace account."""

    return await service.get_account_detail(actor=current_user, account_id=account_id)


@router.get("/accounts/{account_id}/manager-profiles")
async def list_manager_profiles(
    account_id: uuid.UUID,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceDashboardService = DASHBOARD_SERVICE_DEPENDENCY,
) -> list[ManagerProfileResponse]:
    """Return manager profiles for a Playspace account."""

    return await service.list_manager_profiles(actor=current_user, account_id=account_id)


@router.get("/accounts/{account_id}/projects")
async def list_account_projects(
    account_id: uuid.UUID,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceDashboardService = DASHBOARD_SERVICE_DEPENDENCY,
) -> list[ProjectSummaryResponse]:
    """Return project summaries for a Playspace account."""

    return await service.list_account_projects(actor=current_user, account_id=account_id)


@router.get("/accounts/{account_id}/auditors")
async def list_account_auditors(
    account_id: uuid.UUID,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceDashboardService = DASHBOARD_SERVICE_DEPENDENCY,
) -> list[AuditorSummaryResponse]:
    """Return manager-facing auditor summaries for a Playspace account."""

    return await service.list_account_auditors(actor=current_user, account_id=account_id)


@router.get("/projects/{project_id}")
async def get_project_detail(
    project_id: uuid.UUID,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceDashboardService = DASHBOARD_SERVICE_DEPENDENCY,
) -> ProjectDetailResponse:
    """Return Playspace project details."""

    return await service.get_project_detail(actor=current_user, project_id=project_id)


@router.get("/projects/{project_id}/stats")
async def get_project_stats(
    project_id: uuid.UUID,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceDashboardService = DASHBOARD_SERVICE_DEPENDENCY,
) -> ProjectStatsResponse:
    """Return Playspace project stats."""

    return await service.get_project_stats(actor=current_user, project_id=project_id)


@router.get("/projects/{project_id}/places")
async def list_project_places(
    project_id: uuid.UUID,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceDashboardService = DASHBOARD_SERVICE_DEPENDENCY,
) -> list[PlaceSummaryResponse]:
    """Return Playspace place summaries for a project."""

    return await service.list_project_places(actor=current_user, project_id=project_id)


@router.get("/places/{place_id}/audits")
async def list_place_audit_history(
    place_id: uuid.UUID,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceDashboardService = DASHBOARD_SERVICE_DEPENDENCY,
) -> list[PlaceAuditHistoryItemResponse]:
    """Return all audit rows for a place."""

    return await service.list_place_audits(actor=current_user, place_id=place_id)


@router.get("/places/{place_id}/history")
async def get_place_history(
    place_id: uuid.UUID,
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceDashboardService = DASHBOARD_SERVICE_DEPENDENCY,
) -> PlaceHistoryResponse:
    """Return aggregate history metrics for one place."""

    return await service.get_place_history(actor=current_user, place_id=place_id)

"""
Auditor dashboard endpoints for Playspace.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.actors import CurrentUserContext
from app.products.playspace.routes.dependencies import (
    AUDIT_SERVICE_DEPENDENCY,
    CURRENT_USER_DEPENDENCY,
)
from app.products.playspace.schemas import (
    AuditorAuditSummaryResponse,
    AuditorDashboardSummaryResponse,
    AuditorPlaceResponse,
)
from app.products.playspace.services import PlayspaceAuditService

router = APIRouter(tags=["playspace-auditor-dashboard"], prefix="/auditor/me")


@router.get("/places")
async def list_my_assigned_places(
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceAuditService = AUDIT_SERVICE_DEPENDENCY,
) -> list[AuditorPlaceResponse]:
    """Return places assigned to the current auditor with latest audit status."""

    return await service.list_auditor_places(actor=current_user)


@router.get("/audits")
async def list_my_audits(
    status_filter: str | None = Query(default=None, alias="status"),
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceAuditService = AUDIT_SERVICE_DEPENDENCY,
) -> list[AuditorAuditSummaryResponse]:
    """Return audits for the current auditor with optional status filtering."""

    return await service.list_auditor_audits(actor=current_user, status_filter=status_filter)


@router.get("/dashboard-summary")
async def get_my_dashboard_summary(
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    service: PlayspaceAuditService = AUDIT_SERVICE_DEPENDENCY,
) -> AuditorDashboardSummaryResponse:
    """Return top-level dashboard metrics for the current auditor."""

    return await service.get_auditor_dashboard_summary(actor=current_user)

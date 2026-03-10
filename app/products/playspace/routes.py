"""
Playspace route wrappers over the shared dashboard service layer.

These endpoints keep the Playspace namespace explicit while reusing the common
account/project/place/auditor logic shared across both products.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actors import ActorContext, resolve_dummy_actor
from app.core.dashboard_service import SharedDashboardService
from app.core.schemas import (
    AccountDetailResponse,
    AuditorSummaryResponse,
    ManagerProfileResponse,
    PlaceSummaryResponse,
    ProjectDetailResponse,
    ProjectStatsResponse,
    ProjectSummaryResponse,
)
from app.database import get_async_session_playspace

router = APIRouter(tags=["playspace-shared"])
ACTOR_DEPENDENCY = Depends(resolve_dummy_actor)
SESSION_DEPENDENCY = Depends(get_async_session_playspace)


def _get_service(session: AsyncSession) -> SharedDashboardService:
    """Build a shared dashboard service for the current request."""

    return SharedDashboardService(session=session)


@router.get("/accounts/{account_id}", response_model=AccountDetailResponse)
async def get_account_detail(
    account_id: uuid.UUID,
    actor: ActorContext = ACTOR_DEPENDENCY,
    session: AsyncSession = SESSION_DEPENDENCY,
):
    """Return the manager dashboard payload for a Playspace account."""

    service = _get_service(session=session)
    return await service.get_account_detail(actor=actor, account_id=account_id)


@router.get(
    "/accounts/{account_id}/manager-profiles",
    response_model=list[ManagerProfileResponse],
)
async def list_manager_profiles(
    account_id: uuid.UUID,
    actor: ActorContext = ACTOR_DEPENDENCY,
    session: AsyncSession = SESSION_DEPENDENCY,
):
    """Return manager profiles for a Playspace account."""

    service = _get_service(session=session)
    return await service.list_manager_profiles(actor=actor, account_id=account_id)


@router.get("/accounts/{account_id}/projects", response_model=list[ProjectSummaryResponse])
async def list_account_projects(
    account_id: uuid.UUID,
    actor: ActorContext = ACTOR_DEPENDENCY,
    session: AsyncSession = SESSION_DEPENDENCY,
):
    """Return project summaries for a Playspace account."""

    service = _get_service(session=session)
    return await service.list_account_projects(actor=actor, account_id=account_id)


@router.get("/accounts/{account_id}/auditors", response_model=list[AuditorSummaryResponse])
async def list_account_auditors(
    account_id: uuid.UUID,
    actor: ActorContext = ACTOR_DEPENDENCY,
    session: AsyncSession = SESSION_DEPENDENCY,
):
    """Return manager-facing auditor summaries for a Playspace account."""

    service = _get_service(session=session)
    return await service.list_account_auditors(actor=actor, account_id=account_id)


@router.get("/projects/{project_id}", response_model=ProjectDetailResponse)
async def get_project_detail(
    project_id: uuid.UUID,
    actor: ActorContext = ACTOR_DEPENDENCY,
    session: AsyncSession = SESSION_DEPENDENCY,
):
    """Return Playspace project details."""

    service = _get_service(session=session)
    return await service.get_project_detail(actor=actor, project_id=project_id)


@router.get("/projects/{project_id}/stats", response_model=ProjectStatsResponse)
async def get_project_stats(
    project_id: uuid.UUID,
    actor: ActorContext = ACTOR_DEPENDENCY,
    session: AsyncSession = SESSION_DEPENDENCY,
):
    """Return Playspace project stats."""

    service = _get_service(session=session)
    return await service.get_project_stats(actor=actor, project_id=project_id)


@router.get("/projects/{project_id}/places", response_model=list[PlaceSummaryResponse])
async def list_project_places(
    project_id: uuid.UUID,
    actor: ActorContext = ACTOR_DEPENDENCY,
    session: AsyncSession = SESSION_DEPENDENCY,
):
    """Return Playspace place summaries for a project."""

    service = _get_service(session=session)
    return await service.list_project_places(actor=actor, project_id=project_id)

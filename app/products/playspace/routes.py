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
from app.core.auditor_signup_request_service import AuditorSignupRequestService
from app.core.dashboard_service import SharedDashboardService
from app.core.schemas import (
    AccountDetailResponse,
    ApproveAuditorSignupRequestPayload,
    AuditorCodeLoginResponse,
    AuditorSignupApprovalResponse,
    AuditorSignupRequestResponse,
    AuditorSummaryResponse,
    CreateAuditorSignupRequestPayload,
    ManagerProfileResponse,
    PlaceSummaryResponse,
    ProjectDetailResponse,
    ProjectStatsResponse,
    ProjectSummaryResponse,
    ValidateAuditorCodePayload,
)
from app.database import get_async_session_playspace

router = APIRouter(tags=["playspace-shared"])
ACTOR_DEPENDENCY = Depends(resolve_dummy_actor)
SESSION_DEPENDENCY = Depends(get_async_session_playspace)


def _get_service(session: AsyncSession) -> SharedDashboardService:
    """Build a shared dashboard service for the current request."""

    return SharedDashboardService(session=session)


def _get_request_service(session: AsyncSession) -> AuditorSignupRequestService:
    """Build the shared signup-request workflow service for the current request."""

    return AuditorSignupRequestService(session=session)


@router.post("/auditor-code-login", response_model=AuditorCodeLoginResponse)
async def validate_auditor_code(
    payload: ValidateAuditorCodePayload,
    session: AsyncSession = SESSION_DEPENDENCY,
):
    """Validate an auditor code before the web app creates its dummy session."""

    service = _get_request_service(session=session)
    return await service.validate_auditor_code(auditor_code=payload.auditor_code)


@router.post(
    "/auditor-signup-requests",
    response_model=AuditorSignupRequestResponse,
    status_code=201,
)
async def create_auditor_signup_request(
    payload: CreateAuditorSignupRequestPayload,
    session: AsyncSession = SESSION_DEPENDENCY,
):
    """Create a new auditor access request for manager review."""

    service = _get_request_service(session=session)
    return await service.create_request(payload=payload)


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


@router.get(
    "/accounts/{account_id}/auditor-signup-requests",
    response_model=list[AuditorSignupRequestResponse],
)
async def list_auditor_signup_requests(
    account_id: uuid.UUID,
    actor: ActorContext = ACTOR_DEPENDENCY,
    session: AsyncSession = SESSION_DEPENDENCY,
):
    """Return pending auditor access requests for the manager dashboard."""

    service = _get_request_service(session=session)
    return await service.list_pending_requests(actor=actor, account_id=account_id)


@router.post(
    "/accounts/{account_id}/auditor-signup-requests/{request_id}/approve",
    response_model=AuditorSignupApprovalResponse,
)
async def approve_auditor_signup_request(
    account_id: uuid.UUID,
    request_id: uuid.UUID,
    payload: ApproveAuditorSignupRequestPayload,
    actor: ActorContext = ACTOR_DEPENDENCY,
    session: AsyncSession = SESSION_DEPENDENCY,
):
    """Approve an auditor request once a project or place assignment is chosen."""

    service = _get_request_service(session=session)
    return await service.approve_request(
        actor=actor,
        account_id=account_id,
        request_id=request_id,
        payload=payload,
    )


@router.post(
    "/accounts/{account_id}/auditor-signup-requests/{request_id}/decline",
    response_model=AuditorSignupRequestResponse,
)
async def decline_auditor_signup_request(
    account_id: uuid.UUID,
    request_id: uuid.UUID,
    actor: ActorContext = ACTOR_DEPENDENCY,
    session: AsyncSession = SESSION_DEPENDENCY,
):
    """Decline a pending auditor request."""

    service = _get_request_service(session=session)
    return await service.decline_request(
        actor=actor,
        account_id=account_id,
        request_id=request_id,
    )


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

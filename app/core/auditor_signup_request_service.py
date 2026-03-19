"""
Shared write service for auditor access requests and approvals.

This keeps the product route wrappers thin while the real authentication flow is
still under development.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actors import ActorContext, ensure_manager_actor
from app.core.schemas import (
    ApproveAuditorSignupRequestPayload,
    ApprovedAuditorResponse,
    AuditorCodeLoginResponse,
    AuditorSignupApprovalResponse,
    AuditorSignupRequestResponse,
    CreateAuditorSignupRequestPayload,
)
from app.models import (
    Account,
    AccountType,
    AuditorAssignment,
    AuditorProfile,
    AuditorSignupRequest,
    AuditorSignupRequestStatus,
    ManagerProfile,
    Place,
    Project,
)

AUDITOR_CODE_PREFIX = "AUD"
MAX_AUDITOR_CODE_SEQUENCE = 9999


def _normalize_email(value: str) -> str:
    """Normalize a user-provided email-like identifier for consistent matching."""

    return value.strip().lower()


def _normalize_optional_note(value: str | None) -> str | None:
    """Trim optional freeform notes while preserving empty-as-null semantics."""

    if value is None:
        return None

    trimmed_value = value.strip()
    return trimmed_value if trimmed_value else None


def _serialize_request(request_model: AuditorSignupRequest) -> AuditorSignupRequestResponse:
    """Convert an ORM request row into the API shape expected by the web app."""

    return AuditorSignupRequestResponse(
        id=request_model.id,
        account_id=request_model.account_id,
        manager_email=request_model.manager_email,
        full_name=request_model.full_name,
        email=request_model.email,
        note=request_model.note,
        status=request_model.status.value.lower(),
        requested_at=request_model.requested_at,
        reviewed_at=request_model.reviewed_at,
        assigned_project_id=request_model.assigned_project_id,
        assigned_place_id=request_model.assigned_place_id,
    )


class AuditorSignupRequestService:
    """Own the shared request-access flow used by both product route layers."""

    def __init__(self, session: AsyncSession):
        self._session = session

    def _ensure_manager_scope(self, actor: ActorContext, account_id: uuid.UUID) -> None:
        """Limit manager actions to the active account scope."""

        ensure_manager_actor(actor)
        if actor.account_id is not None and actor.account_id != account_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This actor cannot access the requested account.",
            )

    def _ensure_request_target_matches_actor(
        self,
        *,
        actor: ActorContext,
        request_model: AuditorSignupRequest,
    ) -> None:
        """Restrict review actions to the targeted manager when known."""

        if actor.manager_email is None:
            return

        if actor.manager_email != request_model.manager_email:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This request is addressed to another manager.",
            )

    async def _get_manager_account(self, account_id: uuid.UUID) -> Account:
        """Load a manager account or fail with a clear 404/400 message."""

        result = await self._session.execute(select(Account).where(Account.id == account_id))
        account = result.scalar_one_or_none()
        if account is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Account not found.",
            )
        if account.account_type != AccountType.MANAGER:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Auditor requests can only target a manager account.",
            )
        return account

    async def _resolve_request_target(self, manager_email: str) -> tuple[uuid.UUID, str]:
        """Resolve a manager email into the target account for a public request."""

        normalized_manager_email = _normalize_email(manager_email)
        if not normalized_manager_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Manager email is required.",
            )

        result = await self._session.execute(
            select(ManagerProfile.account_id, ManagerProfile.email).where(
                func.lower(ManagerProfile.email) == normalized_manager_email,
            )
        )
        target_row = result.one_or_none()
        if target_row is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Manager email not found.",
            )

        account_id, stored_manager_email = target_row
        return account_id, _normalize_email(stored_manager_email)

    async def _get_request(
        self,
        *,
        account_id: uuid.UUID,
        request_id: uuid.UUID,
    ) -> AuditorSignupRequest:
        """Load a specific access request scoped to the given manager account."""

        result = await self._session.execute(
            select(AuditorSignupRequest).where(
                AuditorSignupRequest.id == request_id,
                AuditorSignupRequest.account_id == account_id,
            )
        )
        request_model = result.scalar_one_or_none()
        if request_model is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Auditor access request not found.",
            )
        return request_model

    async def _ensure_email_available(self, email: str) -> None:
        """Prevent duplicate accounts or duplicate pending requests for the same email."""

        existing_account_result = await self._session.execute(
            select(Account.id).where(func.lower(Account.email) == email),
        )
        if existing_account_result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="An account with this email already exists.",
            )

    async def _ensure_no_pending_request(self, account_id: uuid.UUID, email: str) -> None:
        """Avoid stacking multiple pending requests for the same person."""

        pending_request_result = await self._session.execute(
            select(AuditorSignupRequest.id).where(
                AuditorSignupRequest.account_id == account_id,
                func.lower(AuditorSignupRequest.email) == email,
                AuditorSignupRequest.status == AuditorSignupRequestStatus.PENDING,
            )
        )
        if pending_request_result.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A pending request already exists for this email.",
            )

    async def _resolve_project(
        self,
        *,
        account_id: uuid.UUID,
        project_id: uuid.UUID,
    ) -> Project:
        """Load a project that belongs to the active manager account."""

        result = await self._session.execute(
            select(Project).where(
                Project.id == project_id,
                Project.account_id == account_id,
            )
        )
        project = result.scalar_one_or_none()
        if project is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found for this account.",
            )
        return project

    async def _resolve_place(
        self,
        *,
        account_id: uuid.UUID,
        place_id: uuid.UUID,
    ) -> Place:
        """Load a place that belongs to the active manager account."""

        result = await self._session.execute(
            select(Place)
            .join(Project, Place.project_id == Project.id)
            .where(
                Place.id == place_id,
                Project.account_id == account_id,
            )
        )
        place = result.scalar_one_or_none()
        if place is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Place not found for this account.",
            )
        return place

    async def _generate_auditor_code(self) -> str:
        """Create the next available alphanumeric auditor code."""

        for sequence in range(1, MAX_AUDITOR_CODE_SEQUENCE + 1):
            candidate_code = f"{AUDITOR_CODE_PREFIX}{sequence:04d}"
            existing_result = await self._session.execute(
                select(AuditorProfile.id).where(AuditorProfile.auditor_code == candidate_code),
            )
            if existing_result.scalar_one_or_none() is None:
                return candidate_code

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No auditor codes are currently available.",
        )

    async def create_request(
        self,
        payload: CreateAuditorSignupRequestPayload,
    ) -> AuditorSignupRequestResponse:
        """Persist a new auditor access request for later manager review."""

        full_name = payload.full_name.strip()
        if not full_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Full name is required.",
            )

        normalized_email = _normalize_email(payload.email)
        if not normalized_email:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Email is required.",
            )

        target_account_id, target_manager_email = await self._resolve_request_target(
            payload.manager_email,
        )
        await self._get_manager_account(target_account_id)
        await self._ensure_email_available(normalized_email)
        await self._ensure_no_pending_request(target_account_id, normalized_email)

        request_model = AuditorSignupRequest(
            id=uuid.uuid4(),
            account_id=target_account_id,
            manager_email=target_manager_email,
            email=normalized_email,
            full_name=full_name,
            note=_normalize_optional_note(payload.note),
            status=AuditorSignupRequestStatus.PENDING,
        )

        self._session.add(request_model)
        await self._session.commit()
        await self._session.refresh(request_model)
        return _serialize_request(request_model)

    async def list_pending_requests(
        self,
        *,
        actor: ActorContext,
        account_id: uuid.UUID,
    ) -> list[AuditorSignupRequestResponse]:
        """Return newest-first pending requests for the manager dashboard."""

        self._ensure_manager_scope(actor, account_id)
        await self._get_manager_account(account_id)

        filters = [
            AuditorSignupRequest.account_id == account_id,
            AuditorSignupRequest.status == AuditorSignupRequestStatus.PENDING,
        ]
        if actor.manager_email is not None:
            filters.append(AuditorSignupRequest.manager_email == actor.manager_email)

        result = await self._session.execute(
            select(AuditorSignupRequest)
            .where(*filters)
            .order_by(AuditorSignupRequest.requested_at.desc())
        )
        requests = result.scalars().all()
        return [_serialize_request(request_model) for request_model in requests]

    async def approve_request(
        self,
        *,
        actor: ActorContext,
        account_id: uuid.UUID,
        request_id: uuid.UUID,
        payload: ApproveAuditorSignupRequestPayload,
    ) -> AuditorSignupApprovalResponse:
        """Approve a request and create the assigned auditor records."""

        self._ensure_manager_scope(actor, account_id)
        await self._get_manager_account(account_id)

        if (payload.project_id is None) == (payload.place_id is None):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Approving a request requires either a project or a place assignment.",
            )

        request_model = await self._get_request(account_id=account_id, request_id=request_id)
        self._ensure_request_target_matches_actor(actor=actor, request_model=request_model)
        if request_model.status != AuditorSignupRequestStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only pending requests can be approved.",
            )

        await self._ensure_email_available(request_model.email)

        project: Project | None = None
        place: Place | None = None
        assigned_project_id: uuid.UUID | None = None
        assigned_place_id: uuid.UUID | None = None

        if payload.project_id is not None:
            project = await self._resolve_project(
                account_id=account_id, project_id=payload.project_id
            )
            assigned_project_id = project.id

        if payload.place_id is not None:
            place = await self._resolve_place(account_id=account_id, place_id=payload.place_id)
            assigned_place_id = place.id

        auditor_account_id = uuid.uuid4()
        auditor_profile_id = uuid.uuid4()
        auditor_assignment_id = uuid.uuid4()
        auditor_code = await self._generate_auditor_code()
        reviewed_at = datetime.now(timezone.utc)

        auditor_account = Account(
            id=auditor_account_id,
            name=request_model.full_name,
            email=request_model.email,
            password_hash=None,
            account_type=AccountType.AUDITOR,
        )
        auditor_profile = AuditorProfile(
            id=auditor_profile_id,
            account_id=auditor_account_id,
            auditor_code=auditor_code,
            email=request_model.email,
            full_name=request_model.full_name,
            age_range=None,
            gender=None,
            country=None,
            role=None,
        )
        auditor_assignment = AuditorAssignment(
            id=auditor_assignment_id,
            auditor_profile_id=auditor_profile_id,
            project_id=assigned_project_id,
            place_id=assigned_place_id,
        )

        self._session.add(auditor_account)
        self._session.add(auditor_profile)
        self._session.add(auditor_assignment)
        # Flush inserts first so the request update can safely reference the new auditor profile.
        await self._session.flush()

        request_model.status = AuditorSignupRequestStatus.APPROVED
        request_model.reviewed_at = reviewed_at
        request_model.approved_auditor_profile_id = auditor_profile_id
        request_model.assigned_project_id = assigned_project_id
        request_model.assigned_place_id = assigned_place_id
        await self._session.commit()

        return AuditorSignupApprovalResponse(
            request=_serialize_request(request_model),
            approved_auditor=ApprovedAuditorResponse(
                auditor_account_id=auditor_account_id,
                auditor_profile_id=auditor_profile_id,
                auditor_code=auditor_code,
                full_name=request_model.full_name,
                assigned_project_id=assigned_project_id,
                assigned_project_name=project.name if project is not None else None,
                assigned_place_id=assigned_place_id,
                assigned_place_name=place.name if place is not None else None,
            ),
        )

    async def decline_request(
        self,
        *,
        actor: ActorContext,
        account_id: uuid.UUID,
        request_id: uuid.UUID,
    ) -> AuditorSignupRequestResponse:
        """Decline a pending request so it disappears from the dashboard queue."""

        self._ensure_manager_scope(actor, account_id)
        await self._get_manager_account(account_id)

        request_model = await self._get_request(account_id=account_id, request_id=request_id)
        self._ensure_request_target_matches_actor(actor=actor, request_model=request_model)
        if request_model.status != AuditorSignupRequestStatus.PENDING:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Only pending requests can be declined.",
            )

        request_model.status = AuditorSignupRequestStatus.DECLINED
        request_model.reviewed_at = datetime.now(timezone.utc)
        await self._session.commit()
        return _serialize_request(request_model)

    async def validate_auditor_code(self, auditor_code: str) -> AuditorCodeLoginResponse:
        """Confirm that an auditor code belongs to an approved auditor profile."""

        cleaned_code = auditor_code.strip()
        if not cleaned_code:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Auditor code is required.",
            )

        result = await self._session.execute(
            select(AuditorProfile).where(
                func.lower(AuditorProfile.auditor_code) == cleaned_code.lower(),
            )
        )
        auditor_profile = result.scalar_one_or_none()
        if auditor_profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Auditor code not found.",
            )

        return AuditorCodeLoginResponse(
            account_id=auditor_profile.account_id,
            auditor_profile_id=auditor_profile.id,
            auditor_code=auditor_profile.auditor_code,
        )

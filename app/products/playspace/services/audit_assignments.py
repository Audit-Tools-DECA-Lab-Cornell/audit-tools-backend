"""
Assignment-focused methods for the Playspace audit service.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.orm import selectinload

from app.core.actors import CurrentUserContext, CurrentUserRole, require_manager_or_admin_user
from app.models import AuditorAssignment, AuditorProfile, Place, Project
from app.products.playspace.schemas import AssignmentResponse, AssignmentWriteRequest

######################################################################################
############################# Assignment Service Mixin ###############################
######################################################################################


class PlayspaceAuditAssignmentsMixin:
    """Mixin containing auditor assignment operations for Playspace."""

    async def list_assignments(
        self,
        *,
        actor: CurrentUserContext,
        auditor_profile_id: uuid.UUID,
    ) -> list[AssignmentResponse]:
        """List assignments for a profile, allowing managers and the owning auditor."""

        profile = await self._get_auditor_profile(auditor_profile_id=auditor_profile_id)
        self._ensure_profile_access(actor=actor, profile=profile)

        query = (
            select(AuditorAssignment)
            .where(AuditorAssignment.auditor_profile_id == auditor_profile_id)
            .order_by(AuditorAssignment.assigned_at.desc())
            .options(
                selectinload(AuditorAssignment.project),
                selectinload(AuditorAssignment.place).selectinload(Place.project),
            )
        )
        if actor.role is CurrentUserRole.MANAGER:
            if actor.account_id is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Manager account context is required for assignment access.",
                )
            query = query.where(
                or_(
                    AuditorAssignment.project.has(Project.account_id == actor.account_id),
                    AuditorAssignment.place.has(
                        Place.project.has(Project.account_id == actor.account_id)
                    ),
                )
            )

        result = await self._session.execute(query)
        assignments = result.scalars().all()
        return [self._serialize_assignment(assignment) for assignment in assignments]

    async def create_assignment(
        self,
        *,
        actor: CurrentUserContext,
        auditor_profile_id: uuid.UUID,
        payload: AssignmentWriteRequest,
    ) -> AssignmentResponse:
        """Create a project- or place-scoped assignment with role-array capabilities."""

        require_manager_or_admin_user(actor)
        await self._get_auditor_profile(auditor_profile_id=auditor_profile_id)
        await self._validate_assignment_scope(payload=payload)
        await self._ensure_actor_can_manage_scope(
            actor=actor,
            project_id=payload.project_id,
            place_id=payload.place_id,
        )

        assignment = AuditorAssignment(
            auditor_profile_id=auditor_profile_id,
            project_id=payload.project_id,
            place_id=payload.place_id,
            audit_roles=self._assignment_roles_to_db_values(roles=payload.audit_roles),
        )
        self._session.add(assignment)
        await self._commit_and_refresh(assignment)
        hydrated_assignment = await self._get_assignment_with_scope(
            assignment_id=assignment.id,
            auditor_profile_id=auditor_profile_id,
        )
        return self._serialize_assignment(hydrated_assignment)

    async def update_assignment(
        self,
        *,
        actor: CurrentUserContext,
        auditor_profile_id: uuid.UUID,
        assignment_id: uuid.UUID,
        payload: AssignmentWriteRequest,
    ) -> AssignmentResponse:
        """Update an existing assignment scope and role-array capabilities."""

        require_manager_or_admin_user(actor)
        await self._get_auditor_profile(auditor_profile_id=auditor_profile_id)
        await self._validate_assignment_scope(payload=payload)
        await self._ensure_actor_can_manage_scope(
            actor=actor,
            project_id=payload.project_id,
            place_id=payload.place_id,
        )

        assignment = await self._get_assignment(
            assignment_id=assignment_id,
            auditor_profile_id=auditor_profile_id,
        )
        assignment.project_id = payload.project_id
        assignment.place_id = payload.place_id
        assignment.audit_roles = self._assignment_roles_to_db_values(roles=payload.audit_roles)
        await self._commit_and_refresh(assignment)
        hydrated_assignment = await self._get_assignment_with_scope(
            assignment_id=assignment.id,
            auditor_profile_id=auditor_profile_id,
        )
        return self._serialize_assignment(hydrated_assignment)

    async def delete_assignment(
        self,
        *,
        actor: CurrentUserContext,
        auditor_profile_id: uuid.UUID,
        assignment_id: uuid.UUID,
    ) -> None:
        """Delete one assignment row under an auditor profile."""

        require_manager_or_admin_user(actor)
        await self._get_auditor_profile(auditor_profile_id=auditor_profile_id)
        assignment = await self._get_assignment(
            assignment_id=assignment_id,
            auditor_profile_id=auditor_profile_id,
        )
        await self._ensure_actor_can_manage_scope(
            actor=actor,
            project_id=assignment.project_id,
            place_id=assignment.place_id,
        )
        await self._session.delete(assignment)
        await self._session.commit()

    async def _get_auditor_profile(self, *, auditor_profile_id: uuid.UUID) -> AuditorProfile:
        """Load an auditor profile and fail with 404 when it does not exist."""

        result = await self._session.execute(
            select(AuditorProfile).where(AuditorProfile.id == auditor_profile_id)
        )
        profile = result.scalar_one_or_none()
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Auditor profile not found.",
            )
        return profile

    async def _get_assignment(
        self,
        *,
        assignment_id: uuid.UUID,
        auditor_profile_id: uuid.UUID,
    ) -> AuditorAssignment:
        """Load an assignment under one profile and fail with 404 when missing."""

        result = await self._session.execute(
            select(AuditorAssignment).where(
                AuditorAssignment.id == assignment_id,
                AuditorAssignment.auditor_profile_id == auditor_profile_id,
            )
        )
        assignment = result.scalar_one_or_none()
        if assignment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assignment not found.",
            )
        return assignment

    async def _get_assignment_with_scope(
        self,
        *,
        assignment_id: uuid.UUID,
        auditor_profile_id: uuid.UUID,
    ) -> AuditorAssignment:
        """Load an assignment together with its project/place display relationships."""

        result = await self._session.execute(
            select(AuditorAssignment)
            .where(
                AuditorAssignment.id == assignment_id,
                AuditorAssignment.auditor_profile_id == auditor_profile_id,
            )
            .options(
                selectinload(AuditorAssignment.project),
                selectinload(AuditorAssignment.place).selectinload(Place.project),
            )
        )
        assignment = result.scalar_one_or_none()
        if assignment is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Assignment not found.",
            )
        return assignment

    async def _validate_assignment_scope(self, *, payload: AssignmentWriteRequest) -> None:
        """Ensure the request targets exactly one real project or place."""

        if (payload.project_id is None) == (payload.place_id is None):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Exactly one of project_id or place_id must be provided.",
            )

        if payload.place_id is not None:
            place_result = await self._session.execute(
                select(Place.id).where(Place.id == payload.place_id)
            )
            if place_result.scalar_one_or_none() is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Place not found.",
                )
            return

        project_result = await self._session.execute(
            select(Project.id).where(Project.id == payload.project_id)
        )
        if project_result.scalar_one_or_none() is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found.",
            )

    def _ensure_profile_access(self, *, actor: CurrentUserContext, profile: AuditorProfile) -> None:
        """Allow managers or the owning auditor account to read assignment rows."""

        if actor.role is CurrentUserRole.ADMIN:
            return
        if actor.role is CurrentUserRole.MANAGER:
            if actor.account_id is None:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Manager account context is required for assignment access.",
                )
            return
        if actor.role is CurrentUserRole.AUDITOR and actor.account_id == profile.account_id:
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this auditor profile.",
        )

    async def _ensure_actor_can_manage_scope(
        self,
        *,
        actor: CurrentUserContext,
        project_id: uuid.UUID | None,
        place_id: uuid.UUID | None,
    ) -> None:
        """Ensure manager actors can only manage assignments inside their own account scope."""

        if actor.role is CurrentUserRole.ADMIN:
            return
        if actor.role is not CurrentUserRole.MANAGER or actor.account_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Manager account context is required for assignment management.",
            )

        scope_account_id = await self._resolve_scope_account_id(
            project_id=project_id,
            place_id=place_id,
        )
        if scope_account_id != actor.account_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Managers can only manage assignments inside their own account.",
            )

    async def _resolve_scope_account_id(
        self,
        *,
        project_id: uuid.UUID | None,
        place_id: uuid.UUID | None,
    ) -> uuid.UUID:
        """Resolve the owning account id for one assignment scope."""

        if place_id is not None:
            place_account_result = await self._session.execute(
                select(Project.account_id)
                .join(Place, Place.project_id == Project.id)
                .where(Place.id == place_id)
            )
            place_account_id = place_account_result.scalar_one_or_none()
            if place_account_id is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Place not found.",
                )
            return place_account_id

        if project_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Assignment scope is required.",
            )

        project_account_result = await self._session.execute(
            select(Project.account_id).where(Project.id == project_id)
        )
        project_account_id = project_account_result.scalar_one_or_none()
        if project_account_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Project not found.",
            )
        return project_account_id

    def _serialize_assignment(self, assignment: AuditorAssignment) -> AssignmentResponse:
        """Convert an ORM assignment row into the API response model."""

        if assignment.place is not None:
            project_name = (
                assignment.place.project.name
                if assignment.place.project is not None
                else "Unknown project"
            )
            return AssignmentResponse(
                id=assignment.id,
                auditor_profile_id=assignment.auditor_profile_id,
                project_id=assignment.place.project_id,
                place_id=assignment.place_id,
                scope_type="place",
                scope_id=assignment.place.id,
                scope_name=assignment.place.name,
                project_name=project_name,
                place_name=assignment.place.name,
                audit_roles=self._assignment_roles_from_db_values(db_values=assignment.audit_roles),
                assigned_at=assignment.assigned_at,
            )

        project_name = assignment.project.name if assignment.project is not None else "Unknown project"
        return AssignmentResponse(
            id=assignment.id,
            auditor_profile_id=assignment.auditor_profile_id,
            project_id=assignment.project_id,
            place_id=assignment.place_id,
            scope_type="project",
            scope_id=assignment.project_id if assignment.project_id is not None else assignment.id,
            scope_name=project_name,
            project_name=project_name,
            place_name=None,
            audit_roles=self._assignment_roles_from_db_values(db_values=assignment.audit_roles),
            assigned_at=assignment.assigned_at,
        )

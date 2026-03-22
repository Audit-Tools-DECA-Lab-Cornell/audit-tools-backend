"""
Facade service for Playspace assignment and audit-session operations.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audit, AuditorAssignment, AuditStatus
from app.products.playspace.audit_state import set_execution_mode_value
from app.products.playspace.schemas import AssignmentRole, ExecutionMode
from app.products.playspace.services.audit_assignments import PlayspaceAuditAssignmentsMixin
from app.products.playspace.services.audit_sessions import PlayspaceAuditSessionsMixin

######################################################################################
################################## Service Facade ####################################
######################################################################################


class PlayspaceAuditService(
    PlayspaceAuditAssignmentsMixin,
    PlayspaceAuditSessionsMixin,
):
    """Facade service that composes assignment and audit-session behaviors."""

    def __init__(self, session: AsyncSession):
        self._session = session

    async def _commit_and_refresh(self, instance: Audit | AuditorAssignment) -> None:
        """Persist and re-hydrate one ORM instance."""

        await self._session.commit()
        await self._session.refresh(
            instance, ["playspace_context", "playspace_pre_audit_answers", "playspace_sections"]
        )

    @staticmethod
    def _normalize_assignment_roles(roles: list[AssignmentRole]) -> list[AssignmentRole]:
        """Return ordered unique assignment roles with stable ordering."""

        ordered_roles = [AssignmentRole.AUDITOR, AssignmentRole.PLACE_ADMIN]
        role_set = set(roles)
        return [role for role in ordered_roles if role in role_set]

    def _assignment_roles_to_db_values(
        self,
        *,
        roles: list[AssignmentRole],
    ) -> list[str]:
        """Convert API assignment role arrays into DB string-array storage."""

        normalized_roles = self._normalize_assignment_roles(roles)
        if not normalized_roles:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="audit_roles must include at least one role.",
            )

        return [role.value for role in normalized_roles]

    def _assignment_roles_from_db_values(
        self,
        *,
        db_values: list[str],
    ) -> list[AssignmentRole]:
        """Convert DB string-array storage into normalized API assignment roles."""

        parsed_roles: list[AssignmentRole] = []
        for raw_value in db_values:
            try:
                parsed_roles.append(AssignmentRole(raw_value))
            except ValueError:
                continue

        normalized_roles = self._normalize_assignment_roles(parsed_roles)
        if normalized_roles:
            return normalized_roles
        return [AssignmentRole.AUDITOR]

    @staticmethod
    def _ensure_mode_allowed(
        *,
        requested_mode: ExecutionMode | None,
        allowed_modes: list[ExecutionMode],
        detail: str,
    ) -> None:
        """Validate that an optional execution mode belongs to the allowed set."""

        if requested_mode is not None and requested_mode not in allowed_modes:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=detail,
            )

    @staticmethod
    def _ensure_not_submitted(*, audit: Audit, detail: str) -> None:
        """Reject writes to already-submitted audits."""

        if audit.status is AuditStatus.SUBMITTED:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=detail,
            )

    @staticmethod
    def _resolve_initial_execution_mode_value(
        *,
        requested_mode: ExecutionMode | None,
        allowed_modes: list[ExecutionMode],
    ) -> str | None:
        """Pick the initial execution mode value for new audit drafts."""

        if requested_mode is not None:
            return requested_mode.value
        if len(allowed_modes) == 1:
            return allowed_modes[0].value
        return None

    def _set_execution_mode(
        self,
        *,
        audit: Audit,
        execution_mode: ExecutionMode,
    ) -> None:
        """Write the selected execution mode into normalized Playspace storage."""

        set_execution_mode_value(audit=audit, execution_mode=execution_mode.value)

"""
Facade service for Playspace assignment and audit-session operations.
"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audit, AuditorAssignment, AuditStatus
from app.products.playspace.audit_state import set_execution_mode_value
from app.products.playspace.schemas import ExecutionMode
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
        if isinstance(instance, Audit):
            await self._session.refresh(
                instance,
                [
                    "updated_at",
                    "playspace_context",
                    "playspace_pre_audit_answers",
                    "playspace_sections",
                ],
            )
            return

        await self._session.refresh(instance)

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

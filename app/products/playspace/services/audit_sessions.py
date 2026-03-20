"""
Audit session-focused methods for the Playspace audit service.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import selectinload

from app.core.actors import CurrentUserContext, CurrentUserRole
from app.models import Audit, AuditorAssignment, AuditorProfile, AuditStatus, Place, Project
from app.products.playspace.instrument import (
    INSTRUMENT_KEY,
    INSTRUMENT_VERSION,
)
from app.products.playspace.schemas import (
    AssignmentRole,
    AuditDraftPatchRequest,
    AuditorPlaceResponse,
    AuditProgressResponse,
    AuditSessionResponse,
    PlaceAuditAccessRequest,
)
from app.products.playspace.scoring import (
    build_audit_progress,
    get_allowed_execution_modes,
    merge_draft_patch,
    resolve_execution_mode,
    score_audit,
)

######################################################################################
############################## Audit Session Service Mixin ###########################
######################################################################################


class PlayspaceAuditSessionsMixin:
    """Mixin containing audit-session operations. Inherits from PlayspaceAuditService."""

    async def list_auditor_places(
        self,
        *,
        actor: CurrentUserContext,
    ) -> list[AuditorPlaceResponse]:
        """Return assigned places for the current auditor with latest audit status."""

        auditor_profile = await self._require_auditor_profile(actor=actor)

        assignments_result = await self._session.execute(
            select(AuditorAssignment)
            .where(AuditorAssignment.auditor_profile_id == auditor_profile.id)
            .options(
                selectinload(AuditorAssignment.place),
                selectinload(AuditorAssignment.project).selectinload(Project.places),
            )
        )
        assignments = assignments_result.scalars().all()

        place_roles: dict[uuid.UUID, set[str]] = {}
        places_by_id: dict[uuid.UUID, Place] = {}
        project_by_place: dict[uuid.UUID, Project] = {}

        for assignment in assignments:
            if assignment.place is not None:
                place = assignment.place
                places_by_id[place.id] = place
                place_roles.setdefault(place.id, set()).update(assignment.audit_roles)
            elif assignment.project is not None:
                for place in assignment.project.places:
                    places_by_id[place.id] = place
                    place_roles.setdefault(place.id, set()).update(assignment.audit_roles)
                    project_by_place[place.id] = assignment.project

        for assignment in assignments:
            if assignment.place is not None and assignment.place.project_id is not None:
                project_result = await self._session.execute(
                    select(Project).where(Project.id == assignment.place.project_id)
                )
                project = project_result.scalar_one_or_none()
                if project is not None:
                    project_by_place[assignment.place.id] = project

        audits_result = await self._session.execute(
            select(Audit)
            .where(
                Audit.auditor_profile_id == auditor_profile.id,
                Audit.place_id.in_(list(places_by_id.keys())),
            )
            .order_by(Audit.started_at.desc())
        )
        all_audits = audits_result.scalars().all()
        latest_audit_by_place: dict[uuid.UUID, Audit] = {}
        for audit in all_audits:
            if audit.place_id not in latest_audit_by_place:
                latest_audit_by_place[audit.place_id] = audit

        responses: list[AuditorPlaceResponse] = []
        for place_id, place in sorted(places_by_id.items(), key=lambda p: p[1].name.lower()):
            project = project_by_place.get(place_id)
            roles = self._assignment_roles_from_db_values(
                db_values=list(place_roles.get(place_id, {"auditor"})),
            )
            latest_audit = latest_audit_by_place.get(place_id)

            progress_percent: float | None = None
            if latest_audit is not None and isinstance(latest_audit.scores_json, dict):
                raw_progress = latest_audit.scores_json.get("draft_progress_percent")
                if isinstance(raw_progress, int | float):
                    progress_percent = float(raw_progress)

            responses.append(
                AuditorPlaceResponse(
                    place_id=place.id,
                    place_name=place.name,
                    place_type=place.place_type,
                    project_id=place.project_id,
                    project_name=project.name if project is not None else "Unknown project",
                    city=place.city,
                    province=place.province,
                    country=place.country,
                    assignment_roles=roles,
                    audit_status=latest_audit.status if latest_audit is not None else None,
                    audit_id=latest_audit.id if latest_audit is not None else None,
                    started_at=latest_audit.started_at if latest_audit is not None else None,
                    submitted_at=latest_audit.submitted_at if latest_audit is not None else None,
                    summary_score=latest_audit.summary_score if latest_audit is not None else None,
                    progress_percent=progress_percent,
                )
            )

        return responses

    async def create_or_resume_audit(
        self,
        *,
        actor: CurrentUserContext,
        place_id: uuid.UUID,
        payload: PlaceAuditAccessRequest,
    ) -> AuditSessionResponse:
        """Create a new in-progress audit or return the active draft for this place."""

        auditor_profile = await self._require_auditor_profile(actor=actor)
        place = await self._get_place(place_id=place_id)
        assignment_roles = await self._resolve_assignment_roles(
            auditor_profile_id=auditor_profile.id,
            place=place,
        )
        allowed_modes = get_allowed_execution_modes(assignment_roles)
        self._ensure_mode_allowed(
            requested_mode=payload.execution_mode,
            allowed_modes=allowed_modes,
            detail="The requested execution mode is not allowed for this place assignment.",
        )

        audit = await self._get_active_audit(
            place_id=place.id,
            auditor_profile_id=auditor_profile.id,
        )
        now = datetime.now(timezone.utc)

        if audit is None:
            initial_meta: dict[str, object] = {}
            initial_execution_mode = self._resolve_initial_execution_mode_value(
                requested_mode=payload.execution_mode,
                allowed_modes=allowed_modes,
            )
            if initial_execution_mode is not None:
                initial_meta["execution_mode"] = initial_execution_mode

            audit = Audit(
                place_id=place.id,
                auditor_profile_id=auditor_profile.id,
                audit_code=self._build_audit_code(
                    place_name=place.name,
                    auditor_code=auditor_profile.auditor_code,
                    created_at=now,
                ),
                instrument_key=INSTRUMENT_KEY,
                instrument_version=INSTRUMENT_VERSION,
                status=AuditStatus.IN_PROGRESS,
                started_at=now,
                responses_json={
                    "meta": initial_meta,
                    "pre_audit": {},
                    "sections": {},
                },
                scores_json={},
            )
            self._session.add(audit)
            await self._commit_and_refresh(audit)
        elif payload.execution_mode is not None:
            self._set_execution_mode(audit=audit, execution_mode=payload.execution_mode)
            await self._commit_and_refresh(audit)

        return self._build_audit_session_response(
            audit=audit,
            place=place,
            assignment_roles=assignment_roles,
        )

    async def get_audit_session(
        self,
        *,
        actor: CurrentUserContext,
        audit_id: uuid.UUID,
    ) -> AuditSessionResponse:
        """Return the current audit state for the owning auditor or a manager."""

        audit, assignment_roles = await self._load_accessible_audit_with_roles(
            actor=actor,
            audit_id=audit_id,
        )
        return self._build_audit_session_response(
            audit=audit,
            place=audit.place,
            assignment_roles=assignment_roles,
        )

    async def patch_audit_draft(
        self,
        *,
        actor: CurrentUserContext,
        audit_id: uuid.UUID,
        payload: AuditDraftPatchRequest,
    ) -> AuditSessionResponse:
        """Merge a draft patch into an in-progress audit and return the updated state."""

        audit, assignment_roles = await self._load_accessible_audit_with_roles(
            actor=actor,
            audit_id=audit_id,
        )
        self._ensure_not_submitted(
            audit=audit,
            detail="Submitted audits cannot be edited.",
        )

        requested_mode = payload.meta.execution_mode if payload.meta is not None else None
        allowed_modes = get_allowed_execution_modes(assignment_roles)
        self._ensure_mode_allowed(
            requested_mode=requested_mode,
            allowed_modes=allowed_modes,
            detail="The requested execution mode is not allowed for this assignment.",
        )

        audit.responses_json = merge_draft_patch(
            current_responses_json=dict(audit.responses_json),
            patch=payload,
        )
        progress = build_audit_progress(
            assignment_roles=assignment_roles,
            responses_json=audit.responses_json,
        )
        audit.scores_json = {
            **dict(audit.scores_json),
            "draft_progress_percent": self._progress_percent(progress),
            "progress": progress.model_dump(),
        }
        await self._commit_and_refresh(audit)

        return self._build_audit_session_response(
            audit=audit,
            place=audit.place,
            assignment_roles=assignment_roles,
        )

    async def patch_place_draft(
        self,
        *,
        actor: CurrentUserContext,
        place_id: uuid.UUID,
        payload: AuditDraftPatchRequest,
    ) -> AuditSessionResponse:
        """Compatibility helper for place-scoped draft saves used by the web scaffold."""

        session = await self.create_or_resume_audit(
            actor=actor,
            place_id=place_id,
            payload=PlaceAuditAccessRequest(
                execution_mode=payload.meta.execution_mode if payload.meta is not None else None,
            ),
        )
        return await self.patch_audit_draft(
            actor=actor,
            audit_id=session.audit_id,
            payload=payload,
        )

    async def submit_audit(
        self,
        *,
        actor: CurrentUserContext,
        audit_id: uuid.UUID,
    ) -> AuditSessionResponse:
        """Validate completion, calculate scores, and submit an in-progress audit."""

        audit, assignment_roles = await self._load_accessible_audit_with_roles(
            actor=actor,
            audit_id=audit_id,
        )
        self._ensure_not_submitted(
            audit=audit,
            detail="This audit has already been submitted.",
        )

        progress = build_audit_progress(
            assignment_roles=assignment_roles,
            responses_json=audit.responses_json,
        )
        if not progress.ready_to_submit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Complete the pre-audit fields and all visible sections before submitting.",
            )

        calculated_scores = score_audit(
            assignment_roles=assignment_roles,
            responses_json=audit.responses_json,
        )
        submitted_at = datetime.now(timezone.utc)
        elapsed_minutes = int((submitted_at - audit.started_at).total_seconds() // 60)

        audit.status = AuditStatus.SUBMITTED
        audit.submitted_at = submitted_at
        audit.total_minutes = max(elapsed_minutes, 0)
        audit.scores_json = calculated_scores
        summary_payload = calculated_scores.get("summary")
        has_numeric_percent = isinstance(summary_payload, dict) and isinstance(
            summary_payload.get("percent"),
            float,
        )
        audit.summary_score = float(summary_payload["percent"]) if has_numeric_percent else None
        await self._commit_and_refresh(audit)

        return self._build_audit_session_response(
            audit=audit,
            place=audit.place,
            assignment_roles=assignment_roles,
        )

    async def _load_accessible_audit_with_roles(
        self,
        *,
        actor: CurrentUserContext,
        audit_id: uuid.UUID,
    ) -> tuple[Audit, list[AssignmentRole]]:
        """Load an audit, enforce access, and resolve assignment role in one step."""

        audit = await self._get_audit(audit_id=audit_id)
        self._ensure_audit_access(actor=actor, audit=audit)
        assignment_roles = await self._resolve_assignment_roles(
            auditor_profile_id=audit.auditor_profile_id,
            place=audit.place,
        )
        return audit, assignment_roles

    async def _get_place(self, *, place_id: uuid.UUID) -> Place:
        """Load a place and its project, failing with 404 when not found."""

        result = await self._session.execute(
            select(Place).where(Place.id == place_id).options(selectinload(Place.project))
        )
        place = result.scalar_one_or_none()
        if place is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Place not found.",
            )
        return place

    async def _get_audit(self, *, audit_id: uuid.UUID) -> Audit:
        """Load an audit with place and profile relationships."""

        result = await self._session.execute(
            select(Audit)
            .where(Audit.id == audit_id)
            .options(
                selectinload(Audit.place).selectinload(Place.project),
                selectinload(Audit.auditor_profile),
            )
        )
        audit = result.scalar_one_or_none()
        if audit is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Audit not found.",
            )
        return audit

    async def _get_active_audit(
        self,
        *,
        place_id: uuid.UUID,
        auditor_profile_id: uuid.UUID,
    ) -> Audit | None:
        """Return the latest active draft for the same place and auditor profile."""

        result = await self._session.execute(
            select(Audit)
            .where(
                Audit.place_id == place_id,
                Audit.auditor_profile_id == auditor_profile_id,
                Audit.status.in_([AuditStatus.IN_PROGRESS, AuditStatus.PAUSED]),
            )
            .order_by(Audit.started_at.desc())
            .limit(1)
            .options(
                selectinload(Audit.place).selectinload(Place.project),
                selectinload(Audit.auditor_profile),
            )
        )
        return result.scalar_one_or_none()

    async def _require_auditor_profile(self, *, actor: CurrentUserContext) -> AuditorProfile:
        """Resolve the current actor into a playspace auditor profile."""

        if actor.role is not CurrentUserRole.AUDITOR or actor.account_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Auditor access is required for this endpoint.",
            )

        result = await self._session.execute(
            select(AuditorProfile).where(AuditorProfile.account_id == actor.account_id)
        )
        profile = result.scalar_one_or_none()
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Auditor profile not found for the authenticated account.",
            )
        return profile

    async def _resolve_assignment_roles(
        self,
        *,
        auditor_profile_id: uuid.UUID,
        place: Place,
    ) -> list[AssignmentRole]:
        """Resolve place-specific capabilities by unioning roles from all matching assignments."""

        result = await self._session.execute(
            select(AuditorAssignment)
            .where(
                AuditorAssignment.auditor_profile_id == auditor_profile_id,
                or_(
                    AuditorAssignment.place_id == place.id,
                    and_(
                        AuditorAssignment.project_id == place.project_id,
                        AuditorAssignment.place_id.is_(None),
                    ),
                ),
            )
            .order_by(AuditorAssignment.place_id.is_not(None).desc())
        )
        assignments = result.scalars().all()
        if not assignments:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The current auditor is not assigned to this place.",
            )

        all_db_roles: list[str] = []
        for assignment in assignments:
            all_db_roles.extend(assignment.audit_roles)
        return self._assignment_roles_from_db_values(db_values=all_db_roles)

    def _ensure_audit_access(self, *, actor: CurrentUserContext, audit: Audit) -> None:
        """Allow managers or the owning auditor account to access an audit."""

        if actor.role is CurrentUserRole.MANAGER:
            return
        if (
            actor.role is CurrentUserRole.AUDITOR
            and actor.account_id == audit.auditor_profile.account_id
        ):
            return
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You do not have permission to access this audit.",
        )

    def _build_audit_session_response(
        self,
        *,
        audit: Audit,
        place: Place,
        assignment_roles: list[AssignmentRole],
    ) -> AuditSessionResponse:
        """Build the stable API payload shared by create/resume, save, and submit."""

        allowed_modes = get_allowed_execution_modes(assignment_roles)
        selected_mode = resolve_execution_mode(
            assignment_roles=assignment_roles,
            responses_json=dict(audit.responses_json),
        )
        progress = build_audit_progress(
            assignment_roles=assignment_roles,
            responses_json=dict(audit.responses_json),
        )
        return AuditSessionResponse(
            audit_id=audit.id,
            audit_code=audit.audit_code,
            place_id=place.id,
            place_name=place.name,
            place_type=place.place_type,
            assignment_roles=assignment_roles,
            allowed_execution_modes=allowed_modes,
            selected_execution_mode=selected_mode,
            status=audit.status,
            instrument_key=audit.instrument_key or INSTRUMENT_KEY,
            instrument_version=audit.instrument_version or INSTRUMENT_VERSION,
            started_at=audit.started_at,
            submitted_at=audit.submitted_at,
            total_minutes=audit.total_minutes,
            responses_json=dict(audit.responses_json),
            scores_json=dict(audit.scores_json),
            progress=progress,
        )

    def _build_audit_code(
        self,
        *,
        place_name: str,
        auditor_code: str,
        created_at: datetime,
    ) -> str:
        """Generate a deterministic-enough audit code for draft and export surfaces."""

        place_segment = "".join(
            character for character in place_name.upper() if character.isalnum()
        )
        trimmed_place_segment = place_segment[:12] or "PLAYSPACE"
        timestamp_segment = created_at.strftime("%Y%m%d%H%M%S")
        return f"{trimmed_place_segment}-{auditor_code}-{timestamp_segment}"

    def _progress_percent(self, progress: AuditProgressResponse) -> float:
        """Convert answered-vs-total visible questions into a simple draft percentage."""

        total_visible_questions = progress.total_visible_questions
        answered_visible_questions = progress.answered_visible_questions
        if total_visible_questions <= 0:
            return 0.0
        return round((answered_visible_questions / total_visible_questions) * 100, 2)

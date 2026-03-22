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
from app.models import (
    Audit,
    AuditorAssignment,
    AuditorProfile,
    AuditStatus,
    Place,
    PlayspaceAuditSection,
    PlayspaceQuestionResponse,
    Project,
)
from app.products.playspace.audit_state import (
    apply_draft_patch_to_relations,
    build_responses_json_from_relations,
    get_draft_progress_percent,
    get_execution_mode_value,
    set_draft_progress_percent,
    set_execution_mode_value,
)
from app.products.playspace.instrument import (
    INSTRUMENT_KEY,
    INSTRUMENT_VERSION,
)
from app.products.playspace.schemas import (
    AssignmentRole,
    AuditDraftPatchRequest,
    AuditMetaResponse,
    AuditorAuditSummaryResponse,
    AuditorDashboardSummaryResponse,
    AuditorPlaceResponse,
    AuditProgressResponse,
    AuditScoresResponse,
    AuditScoreTotalsResponse,
    AuditSectionStateResponse,
    AuditSessionResponse,
    ExecutionMode,
    PlaceAuditAccessRequest,
    PreAuditResponse,
)
from app.products.playspace.scoring import (
    build_audit_progress_for_audit,
    get_allowed_execution_modes,
    resolve_execution_mode_for_audit,
    score_audit_for_audit,
)

######################################################################################
############################## Audit Session Service Mixin ###########################
######################################################################################


def _round_score(value: float | None) -> float | None:
    """Round one score to a single decimal place when present."""

    if value is None:
        return None
    return round(value, 1)


def _average(values: list[float]) -> float | None:
    """Return a rounded average for numeric values."""

    if not values:
        return None
    return _round_score(sum(values) / len(values))


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
            .options(
                selectinload(Audit.playspace_context),
                selectinload(Audit.playspace_pre_audit_answers),
                selectinload(Audit.playspace_sections)
                .selectinload(PlayspaceAuditSection.question_responses)
                .selectinload(PlayspaceQuestionResponse.scale_answers),
            )
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
            score_payload = (
                self._resolve_score_payload(audit=latest_audit) if latest_audit is not None else {}
            )
            score_totals = self._build_score_totals_response(score_payload.get("overall"))
            summary_score = self._combined_construct_total(score_totals)
            if summary_score is None and latest_audit is not None:
                summary_score = latest_audit.summary_score

            progress_percent: float | None = None
            if latest_audit is not None and latest_audit.status is not AuditStatus.SUBMITTED:
                progress_percent = get_draft_progress_percent(latest_audit)
                if progress_percent is None:
                    progress = build_audit_progress_for_audit(
                        assignment_roles=roles,
                        audit=latest_audit,
                    )
                    progress_percent = self._progress_percent(progress)

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
                    summary_score=summary_score,
                    score_totals=score_totals,
                    progress_percent=progress_percent,
                )
            )

        return responses

    async def list_auditor_audits(
        self,
        *,
        actor: CurrentUserContext,
        status_filter: str | None = None,
    ) -> list[AuditorAuditSummaryResponse]:
        """Return audit rows for the current auditor with optional status filtering."""

        auditor_profile = await self._require_auditor_profile(actor=actor)
        status_by_filter = {
            "in_progress": AuditStatus.IN_PROGRESS,
            "paused": AuditStatus.PAUSED,
            "submitted": AuditStatus.SUBMITTED,
        }
        normalized_status_filter = (
            status_filter.strip().lower() if status_filter is not None else None
        )
        if (
            normalized_status_filter is not None
            and normalized_status_filter not in status_by_filter
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="status must be one of in_progress, paused, submitted.",
            )

        query = (
            select(Audit)
            .where(Audit.auditor_profile_id == auditor_profile.id)
            .order_by(Audit.started_at.desc())
            .options(
                selectinload(Audit.place).selectinload(Place.project),
                selectinload(Audit.playspace_context),
                selectinload(Audit.playspace_pre_audit_answers),
                selectinload(Audit.playspace_sections)
                .selectinload(PlayspaceAuditSection.question_responses)
                .selectinload(PlayspaceQuestionResponse.scale_answers),
            )
        )
        if normalized_status_filter is not None:
            query = query.where(Audit.status == status_by_filter[normalized_status_filter])

        audits_result = await self._session.execute(query)
        audits = audits_result.scalars().all()

        responses: list[AuditorAuditSummaryResponse] = []
        for audit in audits:
            score_payload = self._resolve_score_payload(audit=audit)
            score_totals = self._build_score_totals_response(score_payload.get("overall"))
            summary_score = self._combined_construct_total(score_totals)
            if summary_score is None:
                summary_score = audit.summary_score
            progress_percent = (
                get_draft_progress_percent(audit)
                if audit.status is not AuditStatus.SUBMITTED
                else None
            )
            responses.append(
                AuditorAuditSummaryResponse(
                    audit_id=audit.id,
                    audit_code=audit.audit_code,
                    place_id=audit.place_id,
                    place_name=audit.place.name,
                    project_id=audit.place.project_id,
                    project_name=audit.place.project.name,
                    status=audit.status,
                    started_at=audit.started_at,
                    submitted_at=audit.submitted_at,
                    summary_score=_round_score(summary_score),
                    score_totals=score_totals,
                    progress_percent=progress_percent,
                )
            )
        return responses

    async def get_auditor_dashboard_summary(
        self,
        *,
        actor: CurrentUserContext,
    ) -> AuditorDashboardSummaryResponse:
        """Return top-level counts and score average for the current auditor."""

        places = await self.list_auditor_places(actor=actor)
        submitted_audits = await self.list_auditor_audits(actor=actor, status_filter="submitted")
        submitted_scores = [
            audit.summary_score for audit in submitted_audits if audit.summary_score is not None
        ]
        in_progress_count = sum(
            1
            for place in places
            if place.audit_status in {AuditStatus.IN_PROGRESS, AuditStatus.PAUSED}
        )
        submitted_count = sum(1 for place in places if place.audit_status is AuditStatus.SUBMITTED)
        pending_places = sum(1 for place in places if place.audit_status is None)
        return AuditorDashboardSummaryResponse(
            total_assigned_places=len(places),
            in_progress_audits=in_progress_count,
            submitted_audits=submitted_count,
            pending_places=pending_places,
            average_submitted_score=_average([float(score) for score in submitted_scores]),
        )

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
            initial_execution_mode = self._resolve_initial_execution_mode_value(
                requested_mode=payload.execution_mode,
                allowed_modes=allowed_modes,
            )

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
                    "meta": {},
                    "pre_audit": {},
                    "sections": {},
                },
                scores_json={},
            )
            if initial_execution_mode is not None:
                set_execution_mode_value(audit=audit, execution_mode=initial_execution_mode)
            audit.responses_json = build_responses_json_from_relations(audit)
            self._session.add(audit)
            await self._commit_and_refresh(audit)
        elif payload.execution_mode is not None:
            self._set_execution_mode(audit=audit, execution_mode=payload.execution_mode)
            audit.responses_json = build_responses_json_from_relations(audit)
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

        apply_draft_patch_to_relations(audit=audit, patch=payload)
        responses_json = build_responses_json_from_relations(audit)
        audit.responses_json = responses_json
        progress = build_audit_progress_for_audit(
            assignment_roles=assignment_roles,
            audit=audit,
        )
        draft_progress_percent = self._progress_percent(progress)
        set_draft_progress_percent(audit=audit, draft_progress_percent=draft_progress_percent)
        audit.scores_json = {
            **dict(audit.scores_json),
            "draft_progress_percent": draft_progress_percent,
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

        responses_json = build_responses_json_from_relations(audit)
        audit.responses_json = responses_json
        progress = build_audit_progress_for_audit(
            assignment_roles=assignment_roles,
            audit=audit,
        )
        if not progress.ready_to_submit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Complete the pre-audit fields and all visible sections before submitting.",
            )

        calculated_scores = score_audit_for_audit(
            assignment_roles=assignment_roles,
            audit=audit,
        )
        submitted_at = datetime.now(timezone.utc)
        elapsed_minutes = int((submitted_at - audit.started_at).total_seconds() // 60)

        audit.status = AuditStatus.SUBMITTED
        audit.submitted_at = submitted_at
        audit.total_minutes = max(elapsed_minutes, 0)
        set_draft_progress_percent(audit=audit, draft_progress_percent=None)
        audit.scores_json = calculated_scores
        overall_payload = self._build_score_totals_response(calculated_scores.get("overall"))
        audit.summary_score = self._combined_construct_total(overall_payload)
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
                selectinload(Audit.playspace_context),
                selectinload(Audit.playspace_pre_audit_answers),
                selectinload(Audit.playspace_sections)
                .selectinload(PlayspaceAuditSection.question_responses)
                .selectinload(PlayspaceQuestionResponse.scale_answers),
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
                selectinload(Audit.playspace_context),
                selectinload(Audit.playspace_pre_audit_answers),
                selectinload(Audit.playspace_sections)
                .selectinload(PlayspaceAuditSection.question_responses)
                .selectinload(PlayspaceQuestionResponse.scale_answers),
            )
        )
        return result.scalar_one_or_none()

    async def _require_auditor_profile(self, *, actor: CurrentUserContext) -> AuditorProfile:
        """Resolve the current actor into a playspace auditor profile."""

        if actor.role is not CurrentUserRole.AUDITOR:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Auditor access is required for this endpoint.",
            )

        query = select(AuditorProfile)
        if actor.account_id is not None:
            query = query.where(AuditorProfile.account_id == actor.account_id)
        elif actor.auditor_code is not None and actor.auditor_code.strip():
            query = query.where(AuditorProfile.auditor_code == actor.auditor_code.strip())
        else:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Auditor identity is required for this endpoint.",
            )

        result = await self._session.execute(query)
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

        if actor.role in {CurrentUserRole.MANAGER, CurrentUserRole.ADMIN}:
            return
        if (
            actor.role is CurrentUserRole.AUDITOR
            and actor.account_id == audit.auditor_profile.account_id
        ):
            return
        if (
            actor.role is CurrentUserRole.AUDITOR
            and actor.auditor_code is not None
            and actor.auditor_code == audit.auditor_profile.auditor_code
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

        responses_json = build_responses_json_from_relations(audit)
        allowed_modes = get_allowed_execution_modes(assignment_roles)
        selected_mode = resolve_execution_mode_for_audit(
            assignment_roles=assignment_roles,
            audit=audit,
        )
        progress = build_audit_progress_for_audit(
            assignment_roles=assignment_roles,
            audit=audit,
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
            meta=AuditMetaResponse(
                execution_mode=self._parse_execution_mode(get_execution_mode_value(audit))
            ),
            pre_audit=self._build_pre_audit_response(responses_json=responses_json),
            sections=self._build_section_state_response_map(responses_json=responses_json),
            scores=self._build_audit_scores_response(audit=audit, fallback_mode=selected_mode),
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

    def _build_pre_audit_response(
        self,
        *,
        responses_json: dict[str, object],
    ) -> PreAuditResponse:
        """Build the typed pre-audit response object from the nested audit document."""

        pre_audit_payload = self._read_json_dict(responses_json.get("pre_audit"))
        return PreAuditResponse(
            season=pre_audit_payload.get("season")
            if isinstance(pre_audit_payload.get("season"), str)
            else None,
            weather_conditions=self._to_string_list(pre_audit_payload.get("weather_conditions")),
            users_present=self._to_string_list(pre_audit_payload.get("users_present")),
            user_count=pre_audit_payload.get("user_count")
            if isinstance(pre_audit_payload.get("user_count"), str)
            else None,
            age_groups=self._to_string_list(pre_audit_payload.get("age_groups")),
            place_size=pre_audit_payload.get("place_size")
            if isinstance(pre_audit_payload.get("place_size"), str)
            else None,
        )

    def _build_section_state_response_map(
        self,
        *,
        responses_json: dict[str, object],
    ) -> dict[str, AuditSectionStateResponse]:
        """Build the typed section-state response map from the nested audit document."""

        sections_payload = self._read_json_dict(responses_json.get("sections"))
        section_responses: dict[str, AuditSectionStateResponse] = {}
        for section_key, raw_section_payload in sections_payload.items():
            section_payload = self._read_json_dict(raw_section_payload)
            note_value = section_payload.get("note")
            section_responses[section_key] = AuditSectionStateResponse(
                section_key=section_key,
                responses=self._read_nested_string_dict(section_payload.get("responses")),
                note=note_value if isinstance(note_value, str) else None,
            )
        return section_responses

    def _build_audit_scores_response(
        self,
        *,
        audit: Audit,
        fallback_mode: ExecutionMode | None,
    ) -> AuditScoresResponse:
        """Build the typed Playspace score payload from cached or live audit totals."""

        raw_scores = self._resolve_score_payload(audit=audit)
        execution_mode = (
            self._parse_execution_mode(raw_scores.get("execution_mode")) or fallback_mode
        )
        return AuditScoresResponse(
            draft_progress_percent=get_draft_progress_percent(audit),
            execution_mode=execution_mode,
            overall=self._build_score_totals_response(raw_scores.get("overall")),
            by_section=self._build_score_collection_response(raw_scores.get("by_section")),
            by_domain=self._build_score_collection_response(raw_scores.get("by_domain")),
        )

    def _resolve_score_payload(self, *, audit: Audit) -> dict[str, object]:
        """Return the current score payload, recalculating submitted audits when needed."""

        raw_scores = dict(audit.scores_json) if isinstance(audit.scores_json, dict) else {}
        if audit.status is not AuditStatus.SUBMITTED:
            return raw_scores

        overall_payload = self._build_score_totals_response(raw_scores.get("overall"))
        if overall_payload is not None:
            return raw_scores

        try:
            return score_audit_for_audit(
                assignment_roles=self._assignment_roles_from_db_values(
                    db_values=["auditor", "place_admin"]
                ),
                audit=audit,
            )
        except ValueError:
            return raw_scores

    def _build_score_collection_response(
        self,
        raw_collection: object,
    ) -> dict[str, AuditScoreTotalsResponse]:
        """Parse a cached score collection into typed Playspace score totals."""

        collection_payload = self._read_json_dict(raw_collection)
        typed_collection: dict[str, AuditScoreTotalsResponse] = {}
        for score_key, raw_score_payload in collection_payload.items():
            score_response = self._build_score_totals_response(raw_score_payload)
            if score_response is not None:
                typed_collection[score_key] = score_response
        return typed_collection

    def _build_score_totals_response(
        self,
        raw_score_payload: object,
    ) -> AuditScoreTotalsResponse | None:
        """Parse one cached score payload into the typed Playspace total shape."""

        score_payload = self._read_json_dict(raw_score_payload)
        quantity_total = score_payload.get("quantity_total")
        diversity_total = score_payload.get("diversity_total")
        challenge_total = score_payload.get("challenge_total")
        sociability_total = score_payload.get("sociability_total")
        play_value_total = score_payload.get("play_value_total")
        usability_total = score_payload.get("usability_total")
        numeric_values = [
            quantity_total,
            diversity_total,
            challenge_total,
            sociability_total,
            play_value_total,
            usability_total,
        ]
        if not all(isinstance(value, int | float) for value in numeric_values):
            return None
        return AuditScoreTotalsResponse(
            quantity_total=float(quantity_total),
            diversity_total=float(diversity_total),
            challenge_total=float(challenge_total),
            sociability_total=float(sociability_total),
            play_value_total=float(play_value_total),
            usability_total=float(usability_total),
        )

    @staticmethod
    def _combined_construct_total(score_totals: AuditScoreTotalsResponse | None) -> float | None:
        """Return the combined play-value plus usability total for compact summaries."""

        if score_totals is None:
            return None
        return round(score_totals.play_value_total + score_totals.usability_total, 2)

    @staticmethod
    def _parse_execution_mode(raw_value: object) -> ExecutionMode | None:
        """Parse one stored execution-mode string into the typed enum safely."""

        if not isinstance(raw_value, str):
            return None
        try:
            return ExecutionMode(raw_value)
        except ValueError:
            return None

    @staticmethod
    def _read_json_dict(value: object) -> dict[str, object]:
        """Safely coerce unknown JSON-like values into dictionaries."""

        return dict(value) if isinstance(value, dict) else {}

    @staticmethod
    def _read_nested_string_dict(value: object) -> dict[str, dict[str, str]]:
        """Safely coerce a nested question-answer mapping into string dictionaries."""

        if not isinstance(value, dict):
            return {}

        nested_payload: dict[str, dict[str, str]] = {}
        for outer_key, outer_value in value.items():
            if not isinstance(outer_value, dict):
                nested_payload[outer_key] = {}
                continue
            nested_payload[outer_key] = {
                inner_key: inner_value
                for inner_key, inner_value in outer_value.items()
                if isinstance(inner_value, str)
            }
        return nested_payload

    @staticmethod
    def _to_string_list(value: object) -> list[str]:
        """Safely coerce one unknown JSON-like value into a string list."""

        if not isinstance(value, list):
            return []
        return [entry for entry in value if isinstance(entry, str)]

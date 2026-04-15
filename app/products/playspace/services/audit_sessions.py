"""
Audit session-focused methods for the Playspace audit service.
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timezone

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select, tuple_
from sqlalchemy.orm import selectinload

from app.core.actors import CurrentUserContext, CurrentUserRole
from app.models import (
    Audit,
    AuditorAssignment,
    AuditorProfile,
    AuditStatus,
    Place,
    PlayspaceAuditContext,
    PlayspaceAuditSection,
    PlayspaceQuestionResponse,
    Project,
    ProjectPlace,
)
from app.products.playspace.audit_state import (
    apply_draft_patch_to_relations,
    build_responses_json_from_relations,
    CURRENT_AUDIT_SCHEMA_VERSION,
    get_draft_progress_percent,
    get_aggregate_revision,
    get_aggregate_schema_version,
    get_execution_mode_value,
    replace_audit_aggregate,
    set_aggregate_revision,
    set_draft_progress_percent,
    set_execution_mode_value,
)
from app.products.playspace.instrument import (
    INSTRUMENT_KEY,
    INSTRUMENT_VERSION,
    get_canonical_instrument_response,
)
from app.products.playspace.services.instrument import get_active_instrument
from app.products.playspace.schemas import (
    AuditAggregateResponse,
    AuditDraftPatchRequest,
    AuditDraftSaveResponse,
    AuditMetaResponse,
    AuditorAuditSummaryResponse,
    AuditorDashboardSummaryResponse,
    AuditorPlaceResponse,
    AuditProgressResponse,
    AuditScoresResponse,
    AuditScoreTotalsResponse,
    AuditSectionStateResponse,
    AuditSessionResponse,
    AuditSubmitRequest,
    ExecutionMode,
    PaginatedResponse,
    PlaceAuditAccessRequest,
    PlayspaceInstrumentResponse,
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


def _total_pages(total_count: int, page_size: int) -> int:
    """Return a stable page count for paginated list responses."""

    if total_count <= 0:
        return 1
    return max(1, math.ceil(total_count / page_size))


@dataclass(slots=True)
class _AssignedPlaceSummary:
    """Compact assigned project-place row aggregated across overlapping assignments."""

    place_id: uuid.UUID
    place_name: str
    place_type: str | None
    project_id: uuid.UUID
    project_name: str
    city: str | None
    province: str | None
    country: str | None
    lat: float | None
    lng: float | None
    end_date: date | None


@dataclass(slots=True)
class _CompactAuditSnapshot:
    """Minimal audit snapshot used by auditor list and dashboard surfaces."""

    audit_id: uuid.UUID
    project_id: uuid.UUID
    place_id: uuid.UUID
    audit_code: str
    status: AuditStatus
    started_at: datetime
    submitted_at: datetime | None
    summary_score: float | None
    score_totals: AuditScoreTotalsResponse | None
    progress_percent: float | None
    selected_execution_mode: ExecutionMode | None


class PlayspaceAuditSessionsMixin:
    """Mixin containing audit-session operations. Inherits from PlayspaceAuditService."""

    async def _list_assigned_place_summaries(
        self,
        *,
        auditor_profile_id: uuid.UUID,
    ) -> list[_AssignedPlaceSummary]:
        """Resolve unique project-place assignments without eager-loading audit graphs."""

        direct_place_assignments_query = (
            select(
                Place.id.label("place_id"),
                Place.name.label("place_name"),
                Place.place_type.label("place_type"),
                AuditorAssignment.project_id.label("project_id"),
                Project.name.label("project_name"),
                Place.city.label("city"),
                Place.province.label("province"),
                Place.country.label("country"),
                Place.lat.label("lat"),
                Place.lng.label("lng"),
                Place.end_date.label("end_date"),
            )
            .select_from(AuditorAssignment)
            .join(Project, AuditorAssignment.project_id == Project.id)
            .join(Place, AuditorAssignment.place_id == Place.id)
            .where(
                AuditorAssignment.auditor_profile_id == auditor_profile_id,
                AuditorAssignment.place_id.is_not(None),
            )
        )
        project_place_assignments_query = (
            select(
                Place.id.label("place_id"),
                Place.name.label("place_name"),
                Place.place_type.label("place_type"),
                Project.id.label("project_id"),
                Project.name.label("project_name"),
                Place.city.label("city"),
                Place.province.label("province"),
                Place.country.label("country"),
                Place.lat.label("lat"),
                Place.lng.label("lng"),
                Place.end_date.label("end_date"),
            )
            .select_from(AuditorAssignment)
            .join(Project, AuditorAssignment.project_id == Project.id)
            .join(ProjectPlace, ProjectPlace.project_id == Project.id)
            .join(Place, Place.id == ProjectPlace.place_id)
            .where(
                AuditorAssignment.auditor_profile_id == auditor_profile_id,
                AuditorAssignment.place_id.is_(None),
            )
        )

        assigned_places: dict[tuple[uuid.UUID, uuid.UUID], _AssignedPlaceSummary] = {}

        def record_assignment_row(row: object) -> None:
            """Merge one compact assignment row into the place summary map."""

            place_id = getattr(row, "place_id", None)
            project_id = getattr(row, "project_id", None)
            place_name = getattr(row, "place_name", None)
            if not isinstance(place_id, uuid.UUID):
                return
            if not isinstance(project_id, uuid.UUID):
                return
            if not isinstance(place_name, str):
                return

            summary_key = (project_id, place_id)
            summary = assigned_places.get(summary_key)
            if summary is None:
                summary = _AssignedPlaceSummary(
                    place_id=place_id,
                    place_name=place_name,
                    place_type=getattr(row, "place_type", None),
                    project_id=project_id,
                    project_name=getattr(row, "project_name", None) or "Unknown project",
                    city=getattr(row, "city", None),
                    province=getattr(row, "province", None),
                    country=getattr(row, "country", None),
                    lat=getattr(row, "lat", None),
                    lng=getattr(row, "lng", None),
                    end_date=getattr(row, "end_date", None),
                )
                assigned_places[summary_key] = summary
            else:
                incoming_end_date = getattr(row, "end_date", None)
                if isinstance(incoming_end_date, date) and (
                    summary.end_date is None or incoming_end_date < summary.end_date
                ):
                    summary.end_date = incoming_end_date

        direct_place_assignments_result = await self._session.execute(
            direct_place_assignments_query
        )
        for row in direct_place_assignments_result.all():
            record_assignment_row(row)

        project_place_assignments_result = await self._session.execute(
            project_place_assignments_query
        )
        for row in project_place_assignments_result.all():
            record_assignment_row(row)

        return sorted(
            assigned_places.values(),
            key=lambda place: (place.project_name.lower(), place.place_name.lower()),
        )

    async def _get_latest_audit_snapshots(
        self,
        *,
        auditor_profile_id: uuid.UUID,
        project_place_pairs: list[tuple[uuid.UUID, uuid.UUID]],
    ) -> dict[tuple[uuid.UUID, uuid.UUID], _CompactAuditSnapshot]:
        """Return the latest compact audit row for each assigned project-place pair."""

        if not project_place_pairs:
            return {}

        latest_audit_rank = func.row_number().over(
            partition_by=(Audit.project_id, Audit.place_id),
            order_by=(Audit.started_at.desc(), Audit.created_at.desc(), Audit.id.desc()),
        )
        latest_audits_subquery = (
            select(
                Audit.id.label("audit_id"),
                Audit.project_id.label("project_id"),
                Audit.place_id.label("place_id"),
                Audit.audit_code.label("audit_code"),
                Audit.status.label("status"),
                Audit.started_at.label("started_at"),
                Audit.submitted_at.label("submitted_at"),
                Audit.summary_score.label("summary_score"),
                Audit.scores_json.label("scores_json"),
                Audit.responses_json.label("responses_json"),
                PlayspaceAuditContext.draft_progress_percent.label("draft_progress_percent"),
                PlayspaceAuditContext.execution_mode.label("selected_execution_mode"),
                latest_audit_rank.label("audit_rank"),
            )
            .outerjoin(PlayspaceAuditContext, PlayspaceAuditContext.audit_id == Audit.id)
            .where(
                Audit.auditor_profile_id == auditor_profile_id,
                tuple_(Audit.project_id, Audit.place_id).in_(project_place_pairs),
            )
            .subquery()
        )

        latest_audits_result = await self._session.execute(
            select(latest_audits_subquery).where(latest_audits_subquery.c.audit_rank == 1)
        )

        latest_audits_by_place: dict[tuple[uuid.UUID, uuid.UUID], _CompactAuditSnapshot] = {}
        for row in latest_audits_result.all():
            audit_id = getattr(row, "audit_id", None)
            audit_code = getattr(row, "audit_code", None)
            project_id = getattr(row, "project_id", None)
            place_id = getattr(row, "place_id", None)
            started_at = getattr(row, "started_at", None)
            status_value = getattr(row, "status", None)
            if not isinstance(audit_id, uuid.UUID):
                continue
            if not isinstance(audit_code, str):
                continue
            if not isinstance(project_id, uuid.UUID):
                continue
            if not isinstance(place_id, uuid.UUID):
                continue
            if not isinstance(started_at, datetime):
                continue
            if not isinstance(status_value, AuditStatus):
                continue

            score_totals, summary_score = self._resolve_compact_audit_summary(
                raw_scores=getattr(row, "scores_json", {}),
                fallback_summary_score=getattr(row, "summary_score", None),
            )
            responses_payload = self._read_json_dict(getattr(row, "responses_json", {}))
            meta_payload = self._read_json_dict(responses_payload.get("meta"))
            raw_progress_percent = getattr(row, "draft_progress_percent", None)
            progress_percent = None
            if status_value is not AuditStatus.SUBMITTED and isinstance(
                raw_progress_percent, int | float
            ):
                progress_percent = float(raw_progress_percent)

            latest_audits_by_place[(project_id, place_id)] = _CompactAuditSnapshot(
                audit_id=audit_id,
                project_id=project_id,
                place_id=place_id,
                audit_code=audit_code,
                status=status_value,
                started_at=started_at,
                submitted_at=getattr(row, "submitted_at", None),
                summary_score=summary_score,
                score_totals=score_totals,
                progress_percent=progress_percent,
                selected_execution_mode=self._parse_execution_mode(
                    getattr(row, "selected_execution_mode", None)
                )
                or self._parse_execution_mode(meta_payload.get("execution_mode")),
            )

        return latest_audits_by_place

    async def _list_submitted_audit_scores(
        self,
        *,
        auditor_profile_id: uuid.UUID,
    ) -> list[float]:
        """Return submitted-audit scores from compact cached payloads only."""

        submitted_audits_result = await self._session.execute(
            select(Audit.summary_score, Audit.scores_json).where(
                Audit.auditor_profile_id == auditor_profile_id,
                Audit.status == AuditStatus.SUBMITTED,
            )
        )

        submitted_scores: list[float] = []
        for row in submitted_audits_result.all():
            _, summary_score = self._resolve_compact_audit_summary(
                raw_scores=getattr(row, "scores_json", {}),
                fallback_summary_score=getattr(row, "summary_score", None),
            )
            if summary_score is not None:
                submitted_scores.append(float(summary_score))

        return submitted_scores

    async def list_auditor_places(
        self,
        *,
        actor: CurrentUserContext,
        page: int = 1,
        page_size: int = 8,
        search: str | None = None,
        sort: str | None = None,
        statuses: list[str] | None = None,
    ) -> PaginatedResponse[AuditorPlaceResponse]:
        """Return assigned places for the current auditor with latest audit status."""

        auditor_profile = await self._require_auditor_profile(actor=actor)
        normalized_search = (
            search.strip().lower() if search is not None and search.strip() else None
        )
        normalized_statuses = {
            raw_status
            for raw_status in (statuses or [])
            if raw_status in {"not_started", "IN_PROGRESS", "PAUSED", "SUBMITTED"}
        }
        safe_page_size = max(1, min(page_size, 100))
        offset = max(page - 1, 0) * safe_page_size
        assigned_places = await self._list_assigned_place_summaries(
            auditor_profile_id=auditor_profile.id
        )
        latest_audits_by_place = await self._get_latest_audit_snapshots(
            auditor_profile_id=auditor_profile.id,
            project_place_pairs=[(place.project_id, place.place_id) for place in assigned_places],
        )

        responses: list[AuditorPlaceResponse] = []
        for assigned_place in assigned_places:
            latest_audit = latest_audits_by_place.get(
                (assigned_place.project_id, assigned_place.place_id)
            )

            responses.append(
                AuditorPlaceResponse(
                    place_id=assigned_place.place_id,
                    place_name=assigned_place.place_name,
                    place_type=assigned_place.place_type,
                    project_id=assigned_place.project_id,
                    project_name=assigned_place.project_name,
                    city=assigned_place.city,
                    province=assigned_place.province,
                    country=assigned_place.country,
                    lat=assigned_place.lat,
                    lng=assigned_place.lng,
                    audit_status=latest_audit.status if latest_audit is not None else None,
                    audit_id=latest_audit.audit_id if latest_audit is not None else None,
                    started_at=latest_audit.started_at if latest_audit is not None else None,
                    submitted_at=latest_audit.submitted_at if latest_audit is not None else None,
                    due_date=datetime.combine(
                        assigned_place.end_date,
                        time.min,
                        tzinfo=timezone.utc,
                    )
                    if assigned_place.end_date is not None
                    else None,
                    summary_score=latest_audit.summary_score if latest_audit is not None else None,
                    score_totals=latest_audit.score_totals if latest_audit is not None else None,
                    progress_percent=latest_audit.progress_percent
                    if latest_audit is not None
                    else None,
                    selected_execution_mode=latest_audit.selected_execution_mode
                    if latest_audit is not None
                    else None,
                )
            )

        filtered_responses = responses
        if normalized_search is not None:
            filtered_responses = [
                response
                for response in filtered_responses
                if normalized_search
                in " ".join(
                    part
                    for part in [
                        response.place_name,
                        response.project_name,
                        response.place_type or "",
                        response.city or "",
                        response.province or "",
                        response.country or "",
                    ]
                ).lower()
            ]

        if normalized_statuses:
            filtered_responses = [
                response
                for response in filtered_responses
                if (response.audit_status is None and "not_started" in normalized_statuses)
                or (
                    response.audit_status is not None
                    and response.audit_status.value in normalized_statuses
                )
            ]

        raw_sort = sort.strip() if sort is not None and sort.strip() else "place_name"
        is_descending = raw_sort.startswith("-")
        sort_key = raw_sort[1:] if is_descending else raw_sort

        def build_sort_value(response: AuditorPlaceResponse) -> str | float | datetime | None:
            """Return the sortable value for the requested auditor place column."""

            if sort_key == "project_name":
                return response.project_name.lower()
            if sort_key == "audit_status":
                return response.audit_status.value if response.audit_status is not None else None
            if sort_key == "started_at":
                return response.started_at
            if sort_key == "submitted_at":
                return response.submitted_at
            if sort_key == "summary_score":
                return response.summary_score
            return response.place_name.lower()

        non_null_rows = [
            response for response in filtered_responses if build_sort_value(response) is not None
        ]
        null_rows = [
            response for response in filtered_responses if build_sort_value(response) is None
        ]
        non_null_rows = sorted(
            non_null_rows,
            key=lambda response: (build_sort_value(response), response.place_name.lower()),
            reverse=is_descending,
        )
        filtered_responses = [*non_null_rows, *null_rows]

        total_count = len(filtered_responses)
        page_items = filtered_responses[offset : offset + safe_page_size]

        return PaginatedResponse[AuditorPlaceResponse](
            items=page_items,
            total_count=total_count,
            page=page,
            page_size=safe_page_size,
            total_pages=_total_pages(total_count, safe_page_size),
        )

    async def list_auditor_audits(
        self,
        *,
        actor: CurrentUserContext,
        page: int = 1,
        page_size: int = 8,
        search: str | None = None,
        sort: str | None = None,
        statuses: list[str] | None = None,
    ) -> PaginatedResponse[AuditorAuditSummaryResponse]:
        """Return audit rows for the current auditor with optional status filtering."""

        auditor_profile = await self._require_auditor_profile(actor=actor)
        status_by_filter = {
            "in_progress": AuditStatus.IN_PROGRESS,
            "paused": AuditStatus.PAUSED,
            "submitted": AuditStatus.SUBMITTED,
        }
        normalized_search = search.strip() if search is not None and search.strip() else None
        normalized_status_filters: list[AuditStatus] = []
        invalid_statuses = []
        for raw_status in statuses or []:
            normalized_status = raw_status.strip().lower()
            resolved_status = status_by_filter.get(normalized_status)
            if resolved_status is None:
                invalid_statuses.append(raw_status)
                continue
            normalized_status_filters.append(resolved_status)
        if invalid_statuses:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="status must be one of in_progress, paused, submitted.",
            )
        safe_page_size = max(1, min(page_size, 100))
        offset = max(page - 1, 0) * safe_page_size

        query = (
            select(
                Audit.id.label("audit_id"),
                Audit.audit_code.label("audit_code"),
                Audit.place_id.label("place_id"),
                Place.name.label("place_name"),
                Audit.project_id.label("project_id"),
                Project.name.label("project_name"),
                Audit.status.label("status"),
                Audit.started_at.label("started_at"),
                Audit.submitted_at.label("submitted_at"),
                Audit.summary_score.label("summary_score"),
                Audit.scores_json.label("scores_json"),
                PlayspaceAuditContext.draft_progress_percent.label("draft_progress_percent"),
            )
            .join(Place, Audit.place_id == Place.id)
            .join(Project, Audit.project_id == Project.id)
            .outerjoin(PlayspaceAuditContext, PlayspaceAuditContext.audit_id == Audit.id)
            .where(Audit.auditor_profile_id == auditor_profile.id)
        )
        if normalized_search is not None:
            search_term = f"%{normalized_search}%"
            query = query.where(
                or_(
                    Audit.audit_code.ilike(search_term),
                    Place.name.ilike(search_term),
                    Project.name.ilike(search_term),
                )
            )
        if normalized_status_filters:
            query = query.where(Audit.status.in_(normalized_status_filters))

        filtered_rows_subquery = query.subquery()
        total_count_result = await self._session.execute(
            select(func.count()).select_from(filtered_rows_subquery)
        )
        total_count = int(total_count_result.scalar_one() or 0)

        raw_sort = sort.strip() if sort is not None and sort.strip() else "-started_at"
        is_descending = raw_sort.startswith("-")
        sort_key = raw_sort[1:] if is_descending else raw_sort
        sort_map = {
            "audit_code": filtered_rows_subquery.c.audit_code,
            "status": filtered_rows_subquery.c.status,
            "place_name": filtered_rows_subquery.c.place_name,
            "project_name": filtered_rows_subquery.c.project_name,
            "started_at": filtered_rows_subquery.c.started_at,
            "submitted_at": filtered_rows_subquery.c.submitted_at,
            "summary_score": filtered_rows_subquery.c.summary_score,
        }
        sort_column = sort_map.get(sort_key, filtered_rows_subquery.c.started_at)
        primary_order = (
            sort_column.desc().nulls_last() if is_descending else sort_column.asc().nulls_last()
        )

        audits_result = await self._session.execute(
            select(filtered_rows_subquery)
            .order_by(
                primary_order,
                filtered_rows_subquery.c.started_at.desc(),
                filtered_rows_subquery.c.audit_id.desc(),
            )
            .offset(offset)
            .limit(safe_page_size)
        )

        responses: list[AuditorAuditSummaryResponse] = []
        for row in audits_result.all():
            audit_id = getattr(row, "audit_id", None)
            audit_code = getattr(row, "audit_code", None)
            place_id = getattr(row, "place_id", None)
            place_name = getattr(row, "place_name", None)
            project_id = getattr(row, "project_id", None)
            project_name = getattr(row, "project_name", None)
            started_at = getattr(row, "started_at", None)
            status_value = getattr(row, "status", None)
            if not isinstance(audit_id, uuid.UUID):
                continue
            if not isinstance(audit_code, str):
                continue
            if not isinstance(place_id, uuid.UUID):
                continue
            if not isinstance(place_name, str):
                continue
            if not isinstance(project_id, uuid.UUID):
                continue
            if not isinstance(project_name, str):
                continue
            if not isinstance(started_at, datetime):
                continue
            if not isinstance(status_value, AuditStatus):
                continue

            score_totals, summary_score = self._resolve_compact_audit_summary(
                raw_scores=getattr(row, "scores_json", {}),
                fallback_summary_score=getattr(row, "summary_score", None),
            )
            raw_progress_percent = getattr(row, "draft_progress_percent", None)
            progress_percent = None
            if status_value is not AuditStatus.SUBMITTED and isinstance(
                raw_progress_percent, int | float
            ):
                progress_percent = float(raw_progress_percent)

            responses.append(
                AuditorAuditSummaryResponse(
                    audit_id=audit_id,
                    audit_code=audit_code,
                    place_id=place_id,
                    place_name=place_name,
                    project_id=project_id,
                    project_name=project_name,
                    status=status_value,
                    started_at=started_at,
                    submitted_at=getattr(row, "submitted_at", None),
                    summary_score=_round_score(summary_score),
                    score_totals=score_totals,
                    progress_percent=progress_percent,
                )
            )
        return PaginatedResponse[AuditorAuditSummaryResponse](
            items=responses,
            total_count=total_count,
            page=page,
            page_size=safe_page_size,
            total_pages=_total_pages(total_count, safe_page_size),
        )

    async def get_auditor_dashboard_summary(
        self,
        *,
        actor: CurrentUserContext,
    ) -> AuditorDashboardSummaryResponse:
        """Return top-level counts and score average for the current auditor."""

        auditor_profile = await self._require_auditor_profile(actor=actor)
        assigned_places = await self._list_assigned_place_summaries(
            auditor_profile_id=auditor_profile.id
        )
        latest_audits_by_place = await self._get_latest_audit_snapshots(
            auditor_profile_id=auditor_profile.id,
            project_place_pairs=[(place.project_id, place.place_id) for place in assigned_places],
        )
        submitted_scores = await self._list_submitted_audit_scores(
            auditor_profile_id=auditor_profile.id
        )
        in_progress_count = sum(
            1
            for audit_snapshot in latest_audits_by_place.values()
            if audit_snapshot.status in {AuditStatus.IN_PROGRESS, AuditStatus.PAUSED}
        )
        submitted_count = sum(
            1
            for audit_snapshot in latest_audits_by_place.values()
            if audit_snapshot.status is AuditStatus.SUBMITTED
        )
        pending_places = len(assigned_places) - in_progress_count - submitted_count
        return AuditorDashboardSummaryResponse(
            total_assigned_places=len(assigned_places),
            in_progress_audits=in_progress_count,
            submitted_audits=submitted_count,
            pending_places=pending_places,
            average_submitted_score=_average(submitted_scores),
        )

    async def create_or_resume_audit(
        self,
        *,
        actor: CurrentUserContext,
        place_id: uuid.UUID,
        payload: PlaceAuditAccessRequest,
    ) -> AuditSessionResponse:
        """Create or return the current auditor's audit for one project-place pair."""

        auditor_profile = await self._require_auditor_profile(actor=actor)
        project, place = await self._get_project_place_pair(
            project_id=payload.project_id,
            place_id=place_id,
        )
        await self._ensure_auditor_assigned_to_pair(
            auditor_profile_id=auditor_profile.id,
            project_id=project.id,
            place_id=place.id,
        )
        allowed_modes = get_allowed_execution_modes()
        self._ensure_mode_allowed(
            requested_mode=payload.execution_mode,
            allowed_modes=allowed_modes,
            detail="The requested execution mode is not valid for this audit.",
        )

        audit = await self._get_existing_audit(
            project_id=project.id,
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
                project_id=project.id,
                place_id=place.id,
                auditor_profile_id=auditor_profile.id,
                audit_code=self._build_audit_code(
                    project_name=project.name,
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
            self._refresh_draft_cache_fields(audit=audit)
            set_aggregate_revision(audit, 1)
            self._session.add(audit)
            await self._commit_and_refresh(audit)

        return await self._build_audit_session_response(
            audit=audit,
            project=project,
            place=place,
        )

    async def get_audit_session(
        self,
        *,
        actor: CurrentUserContext,
        audit_id: uuid.UUID,
    ) -> AuditSessionResponse:
        """Return the current audit state for the owning auditor or a manager."""

        audit = await self._load_accessible_audit(actor=actor, audit_id=audit_id)
        return await self._build_audit_session_response(
            audit=audit,
            project=audit.project,
            place=audit.place,
        )

    async def patch_audit_draft(
        self,
        *,
        actor: CurrentUserContext,
        audit_id: uuid.UUID,
        payload: AuditDraftPatchRequest,
    ) -> AuditDraftSaveResponse:
        """Merge a draft patch into an in-progress audit and return a light acknowledgement."""

        audit = await self._load_accessible_audit(
            actor=actor,
            audit_id=audit_id
        )

        self._ensure_not_submitted(
            audit=audit,
            detail="Submitted audits cannot be edited.",
        )
        self._ensure_expected_revision_matches(
            audit=audit,
            expected_revision=payload.expected_revision,
        )
        if payload.aggregate is not None:
            self._ensure_supported_schema_version(
                schema_version=payload.aggregate.schema_version
            )

        requested_mode = payload.meta.execution_mode if payload.meta is not None else None
        allowed_modes = get_allowed_execution_modes()
        self._ensure_mode_allowed(
            requested_mode=requested_mode,
            allowed_modes=allowed_modes,
            detail="The requested execution mode is not valid for this audit.",
        )

        if payload.aggregate is not None:
            aggregate_mode = (
                payload.aggregate.meta.execution_mode
                if payload.aggregate.meta is not None
                else None
            )
            self._ensure_mode_allowed(
                requested_mode=aggregate_mode,
                allowed_modes=allowed_modes,
                detail="The requested execution mode is not valid for this audit.",
            )
            replace_audit_aggregate(audit=audit, aggregate=payload.aggregate)
            set_execution_mode_value(
                audit=audit,
                execution_mode=aggregate_mode.value if aggregate_mode is not None else None,
            )
        else:
            apply_draft_patch_to_relations(audit=audit, patch=payload)

        set_aggregate_revision(audit, get_aggregate_revision(audit) + 1)
        responses_json = build_responses_json_from_relations(audit)
        audit.responses_json = responses_json
        progress = build_audit_progress_for_audit(audit=audit)
        draft_progress_percent = self._progress_percent(progress)
        set_draft_progress_percent(audit=audit, draft_progress_percent=draft_progress_percent)
        audit.scores_json = {
            **dict(audit.scores_json),
            "draft_progress_percent": draft_progress_percent,
            "progress": progress.model_dump(),
        }
        await self._commit_and_refresh(audit)

        return AuditDraftSaveResponse(
            audit_id=audit.id,
            status=audit.status,
            schema_version=get_aggregate_schema_version(audit),
            revision=get_aggregate_revision(audit),
            draft_progress_percent=draft_progress_percent,
            saved_at=audit.updated_at,
        )

    async def patch_place_draft(
        self,
        *,
        actor: CurrentUserContext,
        place_id: uuid.UUID,
        project_id: uuid.UUID,
        payload: AuditDraftPatchRequest,
    ) -> AuditDraftSaveResponse:
        """Compatibility helper for place-scoped draft saves used by the web scaffold."""

        session = await self.create_or_resume_audit(
            actor=actor,
            place_id=place_id,
            payload=PlaceAuditAccessRequest(
                project_id=project_id,
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
        payload: AuditSubmitRequest | None = None,
    ) -> AuditSessionResponse:
        """Validate completion, calculate scores, and submit an in-progress audit."""

        audit = await self._load_accessible_audit(
            actor=actor,
            audit_id=audit_id
        )
        self._ensure_not_submitted(
            audit=audit,
            detail="This audit has already been submitted.",
        )
        self._ensure_expected_revision_matches(
            audit=audit,
            expected_revision=payload.expected_revision if payload is not None else None,
        )

        responses_json = build_responses_json_from_relations(audit)
        audit.responses_json = responses_json
        progress = build_audit_progress_for_audit(audit=audit)
        if not progress.ready_to_submit:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Complete the pre-audit fields and all visible sections before submitting.",
            )

        calculated_scores = score_audit_for_audit(audit=audit)
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

        return await self._build_audit_session_response(
            audit=audit,
            project=audit.project,
            place=audit.place,
        )

    async def _load_accessible_audit(
        self,
        *,
        actor: CurrentUserContext,
        audit_id: uuid.UUID,
    ) -> Audit:
        """Load an audit and enforce actor-aware access rules."""

        audit = await self._get_audit(audit_id=audit_id)
        self._ensure_audit_access(actor=actor, audit=audit)
        return audit

    async def _get_project_place_pair(
        self,
        *,
        project_id: uuid.UUID,
        place_id: uuid.UUID,
    ) -> tuple[Project, Place]:
        """Load a linked project-place pair or fail with 404."""

        result = await self._session.execute(
            select(ProjectPlace)
            .where(
                ProjectPlace.project_id == project_id,
                ProjectPlace.place_id == place_id,
            )
            .options(
                selectinload(ProjectPlace.project),
                selectinload(ProjectPlace.place),
            )
        )
        project_place = result.scalar_one_or_none()
        if project_place is None or project_place.project is None or project_place.place is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="The requested place is not linked to the requested project.",
            )
        return project_place.project, project_place.place

    async def _get_audit(self, *, audit_id: uuid.UUID) -> Audit:
        """Load an audit with place and profile relationships."""

        result = await self._session.execute(
            select(Audit)
            .where(Audit.id == audit_id)
            .options(
                selectinload(Audit.project),
                selectinload(Audit.place),
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

    async def _get_existing_audit(
        self,
        *,
        project_id: uuid.UUID,
        place_id: uuid.UUID,
        auditor_profile_id: uuid.UUID,
    ) -> Audit | None:
        """Return the current audit for the same project-place pair and auditor."""

        result = await self._session.execute(
            select(Audit)
            .where(
                Audit.project_id == project_id,
                Audit.place_id == place_id,
                Audit.auditor_profile_id == auditor_profile_id,
            )
            .limit(1)
            .options(
                selectinload(Audit.project),
                selectinload(Audit.place),
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

    async def _ensure_auditor_assigned_to_pair(
        self,
        *,
        auditor_profile_id: uuid.UUID,
        project_id: uuid.UUID,
        place_id: uuid.UUID,
    ) -> None:
        """Ensure an auditor is assigned to a project or a specific project-place pair."""

        result = await self._session.execute(
            select(AuditorAssignment.id)
            .where(
                AuditorAssignment.auditor_profile_id == auditor_profile_id,
                AuditorAssignment.project_id == project_id,
                or_(
                    AuditorAssignment.place_id == place_id,
                    AuditorAssignment.place_id.is_(None),
                ),
            )
            .limit(1)
        )
        assignment_id = result.scalar_one_or_none()
        if assignment_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="The current auditor is not assigned to this project/place pair.",
            )

    def _ensure_audit_access(self, *, actor: CurrentUserContext, audit: Audit) -> None:
        """Allow admins, project-owning managers, or the owning auditor to access an audit."""

        if actor.role is CurrentUserRole.ADMIN:
            return
        if actor.role is CurrentUserRole.MANAGER:
            if actor.account_id is None or audit.project.account_id != actor.account_id:
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="You do not have permission to access this audit.",
                )
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

    async def _build_audit_session_response(
        self,
        *,
        audit: Audit,
        project: Project,
        place: Place,
    ) -> AuditSessionResponse:
        """Build the stable API payload shared by create/resume, save, and submit."""

        responses_json = build_responses_json_from_relations(audit)
        allowed_modes = get_allowed_execution_modes()
        selected_mode = resolve_execution_mode_for_audit(audit=audit)
        progress = build_audit_progress_for_audit(audit=audit)

        db_instrument = await get_active_instrument(self._session, INSTRUMENT_KEY)
        if db_instrument is not None:
            en_content = db_instrument.content.get("en")
            if isinstance(en_content, dict):
                instrument = PlayspaceInstrumentResponse.model_validate(en_content)
            else:
                instrument = get_canonical_instrument_response()
        else:
            instrument = get_canonical_instrument_response()

        meta = AuditMetaResponse(
            execution_mode=self._parse_execution_mode(get_execution_mode_value(audit))
        )
        pre_audit = self._build_pre_audit_response(responses_json=responses_json)
        sections = self._build_section_state_response_map(responses_json=responses_json)
        aggregate = self._build_audit_aggregate_response(
            audit=audit,
            responses_json=responses_json,
        )
        return AuditSessionResponse(
            audit_id=audit.id,
            audit_code=audit.audit_code,
            project_id=project.id,
            project_name=project.name,
            place_id=place.id,
            place_name=place.name,
            place_type=place.place_type,
            allowed_execution_modes=allowed_modes,
            selected_execution_mode=selected_mode,
            status=audit.status,
            instrument_key=audit.instrument_key or INSTRUMENT_KEY,
            instrument_version=audit.instrument_version or INSTRUMENT_VERSION,
            instrument=instrument,
            schema_version=aggregate.schema_version,
            revision=aggregate.revision,
            aggregate=aggregate,
            started_at=audit.started_at,
            submitted_at=audit.submitted_at,
            total_minutes=audit.total_minutes,
            meta=meta,
            pre_audit=pre_audit,
            sections=sections,
            scores=self._build_audit_scores_response(audit=audit, fallback_mode=selected_mode),
            progress=progress,
        )

    def _build_audit_code(
        self,
        *,
        project_name: str,
        place_name: str,
        auditor_code: str,
        created_at: datetime,
    ) -> str:
        """Generate a deterministic-enough audit code for draft and export surfaces."""

        project_segment = "".join(
            character for character in project_name.upper() if character.isalnum()
        )
        trimmed_project_segment = project_segment[:8] or "PROJECT"
        place_segment = "".join(
            character for character in place_name.upper() if character.isalnum()
        )
        trimmed_place_segment = place_segment[:12] or "PLAYSPACE"
        timestamp_segment = created_at.strftime("%Y%m%d%H%M%S")
        return (
            f"{trimmed_project_segment}-{trimmed_place_segment}-{auditor_code}-{timestamp_segment}"
        )

    def _progress_percent(self, progress: AuditProgressResponse) -> float:
        """Convert answered-vs-total visible questions into a simple draft percentage."""

        total_visible_questions = progress.total_visible_questions
        answered_visible_questions = progress.answered_visible_questions
        if total_visible_questions <= 0:
            return 0.0
        return round((answered_visible_questions / total_visible_questions) * 100, 2)

    def _ensure_expected_revision_matches(
        self,
        *,
        audit: Audit,
        expected_revision: int | None,
    ) -> None:
        """Reject stale writes when the client sync base is older than the server revision."""

        if expected_revision is None:
            return

        current_revision = get_aggregate_revision(audit)
        if expected_revision != current_revision:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "The audit draft has changed on the server. Fetch the latest audit "
                    "session and retry with the current revision."
                ),
            )

    def _ensure_supported_schema_version(self, *, schema_version: int | None) -> None:
        """Reject aggregate payloads targeting an unsupported schema version."""

        if schema_version is None:
            return

        if schema_version != CURRENT_AUDIT_SCHEMA_VERSION:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    "The submitted audit aggregate schema is not supported by this server."
                ),
            )

    def _build_audit_aggregate_response(
        self,
        *,
        audit: Audit,
        responses_json: dict[str, object],
    ) -> AuditAggregateResponse:
        """Build the canonical aggregate payload returned to aggregate-sync clients."""

        return AuditAggregateResponse(
            schema_version=get_aggregate_schema_version(audit),
            revision=get_aggregate_revision(audit),
            meta=AuditMetaResponse(
                execution_mode=self._parse_execution_mode(
                    get_execution_mode_value(audit)
                )
            ),
            pre_audit=self._build_pre_audit_response(responses_json=responses_json),
            sections=self._build_section_state_response_map(responses_json=responses_json),
        )

    def _refresh_draft_cache_fields(self, *, audit: Audit) -> None:
        """Rebuild cached draft JSON and progress projections after in-memory edits."""

        responses_json = build_responses_json_from_relations(audit)
        audit.responses_json = responses_json

        progress = build_audit_progress_for_audit(audit=audit)
        draft_progress_percent = self._progress_percent(progress)
        set_draft_progress_percent(audit=audit, draft_progress_percent=draft_progress_percent)

        existing_scores = dict(audit.scores_json) if isinstance(audit.scores_json, dict) else {}
        audit.scores_json = {
            **existing_scores,
            "draft_progress_percent": draft_progress_percent,
            "progress": progress.model_dump(),
        }

    def _build_pre_audit_response(
        self,
        *,
        responses_json: dict[str, object],
    ) -> PreAuditResponse:
        """Build the typed pre-audit response object from the nested audit document."""

        pre_audit_payload = self._read_json_dict(responses_json.get("pre_audit"))
        return PreAuditResponse(
            place_size=pre_audit_payload.get("place_size")
            if isinstance(pre_audit_payload.get("place_size"), str)
            else None,
            current_users_0_5=pre_audit_payload.get("current_users_0_5")
            if isinstance(pre_audit_payload.get("current_users_0_5"), str)
            else None,
            current_users_6_12=pre_audit_payload.get("current_users_6_12")
            if isinstance(pre_audit_payload.get("current_users_6_12"), str)
            else None,
            current_users_13_17=pre_audit_payload.get("current_users_13_17")
            if isinstance(pre_audit_payload.get("current_users_13_17"), str)
            else None,
            current_users_18_plus=pre_audit_payload.get("current_users_18_plus")
            if isinstance(pre_audit_payload.get("current_users_18_plus"), str)
            else None,
            playspace_busyness=pre_audit_payload.get("playspace_busyness")
            if isinstance(pre_audit_payload.get("playspace_busyness"), str)
            else None,
            season=pre_audit_payload.get("season")
            if isinstance(pre_audit_payload.get("season"), str)
            else None,
            weather_conditions=self._to_string_list(pre_audit_payload.get("weather_conditions")),
            wind_conditions=pre_audit_payload.get("wind_conditions")
            if isinstance(pre_audit_payload.get("wind_conditions"), str)
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
                responses=self._read_nested_response_payload_dict(section_payload.get("responses")),
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

    def _resolve_compact_audit_summary(
        self,
        *,
        raw_scores: object,
        fallback_summary_score: float | None,
    ) -> tuple[AuditScoreTotalsResponse | None, float | None]:
        """Resolve compact totals and summary score from cached values only."""

        score_totals = self._build_score_totals_response(
            self._read_json_dict(raw_scores).get("overall")
        )
        compact_summary_score = self._combined_construct_total(score_totals)
        if compact_summary_score is not None:
            return score_totals, compact_summary_score
        return score_totals, fallback_summary_score

    def _resolve_score_payload(self, *, audit: Audit) -> dict[str, object]:
        """Return the current score payload, recalculating submitted audits when needed."""

        raw_scores = dict(audit.scores_json) if isinstance(audit.scores_json, dict) else {}
        if audit.status is not AuditStatus.SUBMITTED:
            return raw_scores

        overall_payload = self._build_score_totals_response(raw_scores.get("overall"))
        if overall_payload is not None:
            return raw_scores

        try:
            return score_audit_for_audit(audit=audit)
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

    @classmethod
    def _read_nested_response_payload_dict(
        cls,
        value: object,
    ) -> dict[str, dict[str, str | list[str] | dict[str, str] | None]]:
        """Safely coerce stored question answers into JSON-safe nested dictionaries."""

        if not isinstance(value, dict):
            return {}

        nested_payload: dict[str, dict[str, str | list[str] | dict[str, str] | None]] = {}
        for outer_key, outer_value in value.items():
            if not isinstance(outer_value, dict):
                nested_payload[outer_key] = {}
                continue
            nested_payload[outer_key] = cls._read_response_payload(outer_value)
        return nested_payload

    @classmethod
    def _read_response_payload(
        cls,
        value: object,
    ) -> dict[str, str | list[str] | dict[str, str] | None]:
        """Safely coerce one question response payload into JSON-safe values."""

        if not isinstance(value, dict):
            return {}

        payload: dict[str, str | list[str] | dict[str, str] | None] = {}
        for entry_key, entry_value in value.items():
            if not isinstance(entry_key, str):
                continue
            coerced_value = cls._coerce_question_response_value(entry_value)
            if coerced_value is None and entry_value is not None:
                continue
            payload[entry_key] = coerced_value
        return payload

    @classmethod
    def _coerce_question_response_value(
        cls,
        value: object,
    ) -> str | list[str] | dict[str, str] | None:
        """Coerce one stored answer value into the supported checklist-response shapes."""

        if value is None or isinstance(value, str):
            return value

        if isinstance(value, list):
            return [entry for entry in value if isinstance(entry, str)]

        if isinstance(value, dict):
            return {
                entry_key: entry_value
                for entry_key, entry_value in value.items()
                if isinstance(entry_key, str) and isinstance(entry_value, str)
            }

        return None

    @staticmethod
    def _to_string_list(value: object) -> list[str]:
        """Safely coerce one unknown JSON-like value into a string list."""

        if not isinstance(value, list):
            return []
        return [entry for entry in value if isinstance(entry, str)]

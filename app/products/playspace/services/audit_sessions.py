"""
Audit session-focused methods for the Playspace audit service.
"""

from __future__ import annotations

import ast
import json
import math
import uuid
from datetime import date, datetime, time, timezone
from typing import TYPE_CHECKING

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import selectinload

from app.core.actors import CurrentUserContext, CurrentUserRole
from app.models import (
	AuditorAssignment,
	AuditorProfile,
	AuditStatus,
	Place,
	PlayspaceQuestionResponse,
	PlayspaceSubmissionContext,
	PlayspaceSubmissionSection,
	PlayspaceSubmission,
	Project,
	ProjectPlace,
)
from app.products.playspace.audit_state import (
	CURRENT_AUDIT_SCHEMA_VERSION,
	apply_draft_patch_to_relations,
	build_responses_json_from_relations,
	get_aggregate_revision,
	get_aggregate_schema_version,
	get_draft_progress_percent,
	get_execution_mode_value,
	get_final_comments_value,
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
	ScorePairResponse,
	PlaceActivityStatus,
)
from app.products.playspace.scoring import (
	build_audit_progress_for_audit,
	get_allowed_execution_modes,
	resolve_execution_mode_for_audit,
	score_audit,
	score_audit_for_audit,
)
from app.products.playspace.execution_mode_scope import (
	execution_mode_includes_audit,
	execution_mode_includes_survey,
)
from app.products.playspace.services._place_rollup import (
	overall_score_pair,
	round_score_pair,
)
from app.products.playspace.services.instrument import (
	build_instrument_response_from_row,
	get_active_instrument,
	get_instrument_version,
)

if TYPE_CHECKING:
	from sqlalchemy.ext.asyncio import AsyncSession

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


def _derive_place_axis_status(
	*,
	axis_included: bool,
	audit_status: AuditStatus | None,
) -> PlaceActivityStatus:
	"""Derive the activity status for one place axis from a single auditor submission.

	Because playspace_submissions has a unique constraint on (project_id, place_id,
	auditor_profile_id), each auditor has at most one submission per place - no
	rollup across multiple submissions is needed.

	@param axis_included Whether this submission's execution mode covers the axis.
	@param audit_status  The submission's current AuditStatus, or None if no submission exists.
	@returns             The localized place activity status for the axis.
	"""

	if audit_status is None or not axis_included:
		return "not_started"
	if audit_status is AuditStatus.SUBMITTED:
		return "submitted"
	if audit_status in {AuditStatus.IN_PROGRESS, AuditStatus.PAUSED}:
		return "in_progress"
	return "not_started"


def _resolve_composite_place_status(
	*,
	place_audit_status: PlaceActivityStatus,
	place_survey_status: PlaceActivityStatus,
	selected_execution_mode: ExecutionMode | None,
) -> PlaceActivityStatus:
	"""Compute the single auditor-facing status for a place card based on execution mode.

	Mirrors the logic in the mobile client's derivePlaceRequirementStatus helper so
	that server-side status filtering and client-side display are consistent.
	"""

	if selected_execution_mode is ExecutionMode.AUDIT:
		return place_audit_status
	if selected_execution_mode is ExecutionMode.SURVEY:
		return place_survey_status
	# "both" or mode not yet selected: both axes must reach "submitted".
	if place_audit_status == "submitted" and place_survey_status == "submitted":
		return "submitted"
	if place_audit_status == "in_progress" or place_survey_status == "in_progress":
		return "in_progress"
	return "not_started"


class PlayspaceAuditSessionsMixin:
	"""Mixin containing audit-session operations. Inherits from PlayspaceAuditService."""

	if TYPE_CHECKING:
		_session: AsyncSession

		async def _commit_and_refresh(self, instance: PlayspaceSubmission) -> None: ...
		def _ensure_mode_allowed(
			self,
			*,
			requested_mode: ExecutionMode | None,
			allowed_modes: list[ExecutionMode],
			detail: str,
		) -> None: ...
		def _ensure_not_submitted(self, *, audit: PlayspaceSubmission, detail: str) -> None: ...
		def _resolve_initial_execution_mode_value(
			self,
			*,
			requested_mode: ExecutionMode | None,
			allowed_modes: list[ExecutionMode],
		) -> str | None: ...

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
		"""Return assigned places for the current auditor with their own submission status.

		Uses a single LEFT JOIN query. The unique constraint
		uq_playspace_submissions_project_place_auditor on playspace_submissions
		guarantees at most one submission row per (project, place, auditor), so no
		window functions, rollup loops, or separate ORM object loads are required.
		"""

		auditor_profile = await self._require_auditor_profile(actor=actor)
		normalized_search = search.strip().lower() if search is not None and search.strip() else None
		safe_page_size = max(1, min(page_size, 100))
		offset = max(page - 1, 0) * safe_page_size

		# Single LEFT JOIN - assignments drive the result set; submission columns
		# are null when the auditor has not yet started a session for the place.
		query = (
			select(
				Place.id.label("place_id"),
				Place.name.label("place_name"),
				Place.place_type.label("place_type"),
				AuditorAssignment.project_id.label("project_id"),
				Project.name.label("project_name"),
				Place.city.label("city"),
				Place.province.label("province"),
				Place.country.label("country"),
				Place.postal_code.label("postal_code"),
				Place.address.label("address"),
				Place.lat.label("lat"),
				Place.lng.label("lng"),
				Place.end_date.label("end_date"),
				PlayspaceSubmission.id.label("audit_id"),
				PlayspaceSubmission.execution_mode.label("execution_mode"),
				PlayspaceSubmission.started_at.label("started_at"),
				PlayspaceSubmission.submitted_at.label("submitted_at"),
				PlayspaceSubmission.status.label("status"),
				PlayspaceSubmission.summary_score.label("summary_score"),
				PlayspaceSubmission.scores_json.label("scores_json"),
				PlayspaceSubmission.responses_json.label("responses_json"),
				PlayspaceSubmission.draft_progress_percent.label("draft_progress_percent"),
				PlayspaceSubmission.audit_play_value_score.label("audit_play_value_score"),
				PlayspaceSubmission.audit_usability_score.label("audit_usability_score"),
				PlayspaceSubmission.survey_play_value_score.label("survey_play_value_score"),
				PlayspaceSubmission.survey_usability_score.label("survey_usability_score"),
			)
			.select_from(AuditorAssignment)
			.join(Project, AuditorAssignment.project_id == Project.id)
			.join(Place, AuditorAssignment.place_id == Place.id)
			.outerjoin(
				PlayspaceSubmission,
				(PlayspaceSubmission.project_id == AuditorAssignment.project_id)
				& (PlayspaceSubmission.place_id == AuditorAssignment.place_id)
				& (PlayspaceSubmission.auditor_profile_id == auditor_profile.id),
			)
			.where(AuditorAssignment.auditor_profile_id == auditor_profile.id)
		)

		result = await self._session.execute(query)

		responses: list[AuditorPlaceResponse] = []
		for row in result.all():
			place_id = getattr(row, "place_id", None)
			project_id = getattr(row, "project_id", None)
			if not isinstance(place_id, uuid.UUID) or not isinstance(project_id, uuid.UUID):
				continue

			status_value: AuditStatus | None = getattr(row, "status", None)
			# Resolve execution mode: column value first, meta JSON as legacy fallback.
			raw_execution_mode: str | None = getattr(row, "execution_mode", None)
			if raw_execution_mode is None:
				responses_payload = self._read_json_dict(getattr(row, "responses_json", {}))
				meta_payload = self._read_json_dict(responses_payload.get("meta"))
				raw_meta_mode = meta_payload.get("execution_mode")
				raw_execution_mode = raw_meta_mode if isinstance(raw_meta_mode, str) else None
			selected_execution_mode = self._parse_execution_mode(raw_execution_mode)

			# Derive per-axis activity from the single submission row.
			place_audit_status = _derive_place_axis_status(
				axis_included=execution_mode_includes_audit(raw_execution_mode),
				audit_status=status_value,
			)
			place_survey_status = _derive_place_axis_status(
				axis_included=execution_mode_includes_survey(raw_execution_mode),
				audit_status=status_value,
			)

			raw_end_date = getattr(row, "end_date", None)
			due_date = (
				datetime.combine(raw_end_date, time.min, tzinfo=timezone.utc)
				if isinstance(raw_end_date, date)
				else None
			)

			score_totals, summary_score = self._resolve_compact_audit_summary(
				raw_scores=getattr(row, "scores_json", {}),
				responses_json=getattr(row, "responses_json", {}),
				fallback_summary_score=getattr(row, "summary_score", None),
			)

			raw_progress_percent = getattr(row, "draft_progress_percent", None)
			progress_percent = (
				float(raw_progress_percent)
				if status_value is not AuditStatus.SUBMITTED and isinstance(raw_progress_percent, int | float)
				else None
			)

			# Build score pairs from dedicated score columns (written at submission) to
			# avoid redundant JSONB parsing when computing the compact PV/U display values.
			audit_pv = getattr(row, "audit_play_value_score", None)
			audit_u = getattr(row, "audit_usability_score", None)
			survey_pv = getattr(row, "survey_play_value_score", None)
			survey_u = getattr(row, "survey_usability_score", None)
			audit_scores = round_score_pair(
				float(audit_pv) if isinstance(audit_pv, int | float) else None,
				float(audit_u) if isinstance(audit_u, int | float) else None,
			)
			survey_scores = round_score_pair(
				float(survey_pv) if isinstance(survey_pv, int | float) else None,
				float(survey_u) if isinstance(survey_u, int | float) else None,
			)

			responses.append(
				AuditorPlaceResponse(
					place_id=place_id,
					place_name=getattr(row, "place_name", None) or "",
					place_type=getattr(row, "place_type", None),
					project_id=project_id,
					project_name=getattr(row, "project_name", None) or "",
					city=getattr(row, "city", None),
					province=getattr(row, "province", None),
					country=getattr(row, "country", None),
					postal_code=getattr(row, "postal_code", None),
					address=getattr(row, "address", None),
					lat=getattr(row, "lat", None),
					lng=getattr(row, "lng", None),
					audit_id=getattr(row, "audit_id", None),
					started_at=getattr(row, "started_at", None),
					submitted_at=getattr(row, "submitted_at", None),
					due_date=due_date,
					summary_score=_round_score(summary_score),
					score_totals=score_totals,
					progress_percent=progress_percent,
					selected_execution_mode=selected_execution_mode,
					place_audit_status=place_audit_status,
					place_survey_status=place_survey_status,
					audit_scores=audit_scores,
					survey_scores=survey_scores,
					overall_scores=overall_score_pair(audit_scores, survey_scores),
				)
			)

		if normalized_search is not None:
			responses = [
				response
				for response in responses
				if normalized_search
				in " ".join(
					part
					for part in [
						response.place_name,
						response.project_name,
						response.place_type or "",
						response.postal_code or "",
						response.address or "",
						response.city or "",
						response.province or "",
						response.country or "",
					]
				).lower()
			]

		if statuses:
			# Normalise the requested filter values and compare against the composite
			# status so filtering matches exactly what the client displays.
			status_filter_map: dict[str, PlaceActivityStatus] = {
				"not_started": "not_started",
				"in_progress": "in_progress",
				"paused": "in_progress",
				"submitted": "submitted",
			}
			requested_statuses: set[PlaceActivityStatus] = {
				status_filter_map[norm] for raw in statuses if (norm := raw.strip().lower()) in status_filter_map
			}
			if requested_statuses:
				responses = [
					response
					for response in responses
					if _resolve_composite_place_status(
						place_audit_status=response.place_audit_status,
						place_survey_status=response.place_survey_status,
						selected_execution_mode=response.selected_execution_mode,
					)
					in requested_statuses
				]

		raw_sort = sort.strip() if sort is not None and sort.strip() else "place_name"
		is_descending = raw_sort.startswith("-")
		sort_key = raw_sort[1:] if is_descending else raw_sort

		def build_sort_value(response: AuditorPlaceResponse) -> str | float | datetime | None:
			"""Return the sortable value for the requested auditor place column."""

			if sort_key == "project_name":
				return response.project_name.lower()
			if sort_key == "status":
				return _resolve_composite_place_status(
					place_audit_status=response.place_audit_status,
					place_survey_status=response.place_survey_status,
					selected_execution_mode=response.selected_execution_mode,
				)
			if sort_key == "started_at":
				return response.started_at
			if sort_key == "submitted_at":
				return response.submitted_at
			if sort_key == "summary_score":
				return response.summary_score
			return response.place_name.lower()

		non_null_rows = [r for r in responses if build_sort_value(r) is not None]
		null_rows = [r for r in responses if build_sort_value(r) is None]
		non_null_rows = sorted(
			non_null_rows,
			key=lambda r: (build_sort_value(r), r.place_name.lower()),
			reverse=is_descending,
		)
		responses = [*non_null_rows, *null_rows]

		total_count = len(responses)
		page_items = responses[offset : offset + safe_page_size]

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
				PlayspaceSubmission.id.label("audit_id"),
				PlayspaceSubmission.audit_code.label("audit_code"),
				PlayspaceSubmission.place_id.label("place_id"),
				Place.name.label("place_name"),
				PlayspaceSubmission.project_id.label("project_id"),
				Project.name.label("project_name"),
				PlayspaceSubmission.status.label("status"),
				PlayspaceSubmission.started_at.label("started_at"),
				PlayspaceSubmission.submitted_at.label("submitted_at"),
				PlayspaceSubmission.summary_score.label("summary_score"),
				PlayspaceSubmission.scores_json.label("scores_json"),
				PlayspaceSubmission.responses_json.label("responses_json"),
				PlayspaceSubmission.draft_progress_percent.label("draft_progress_percent"),
				PlayspaceSubmission.execution_mode.label("execution_mode"),
			)
			.join(Place, PlayspaceSubmission.place_id == Place.id)
			.join(Project, PlayspaceSubmission.project_id == Project.id)
			.where(PlayspaceSubmission.auditor_profile_id == auditor_profile.id)
		)
		if normalized_search is not None:
			search_term = f"%{normalized_search}%"
			query = query.where(
				or_(
					PlayspaceSubmission.audit_code.ilike(search_term),
					Place.name.ilike(search_term),
					Project.name.ilike(search_term),
				)
			)
		if normalized_status_filters:
			query = query.where(PlayspaceSubmission.status.in_(normalized_status_filters))

		filtered_rows_subquery = query.subquery()
		total_count_result = await self._session.execute(select(func.count()).select_from(filtered_rows_subquery))
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
		primary_order = sort_column.desc().nulls_last() if is_descending else sort_column.asc().nulls_last()

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
				responses_json=getattr(row, "responses_json", {}),
				fallback_summary_score=getattr(row, "summary_score", None),
			)
			raw_progress_percent = getattr(row, "draft_progress_percent", None)
			progress_percent = None
			if status_value is not AuditStatus.SUBMITTED and isinstance(raw_progress_percent, int | float):
				progress_percent = float(raw_progress_percent)

			responses.append(
				AuditorAuditSummaryResponse(
					audit_id=audit_id,
					audit_code=audit_code,
					auditor_code=auditor_profile.auditor_code,
					place_id=place_id,
					place_name=place_name,
					project_id=project_id,
					project_name=project_name,
					status=status_value,
					execution_mode=self._parse_execution_mode(getattr(row, "execution_mode", None)),
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
		"""Return top-level counts and score average for the current auditor.

		Two targeted queries replace the previous three-query approach:
		- A COUNT on auditor_assignments for the total assigned places.
		- A lightweight select on playspace_submissions for status tallies and scores.
		"""

		auditor_profile = await self._require_auditor_profile(actor=actor)

		total_result = await self._session.execute(
			select(func.count())
			.select_from(AuditorAssignment)
			.where(AuditorAssignment.auditor_profile_id == auditor_profile.id)
		)
		total_assigned = int(total_result.scalar_one() or 0)

		submissions_result = await self._session.execute(
			select(
				PlayspaceSubmission.status,
				PlayspaceSubmission.summary_score,
				PlayspaceSubmission.scores_json,
			).where(PlayspaceSubmission.auditor_profile_id == auditor_profile.id)
		)

		in_progress_count = 0
		submitted_count = 0
		submitted_scores: list[float] = []
		for row in submissions_result.all():
			sub_status: AuditStatus | None = getattr(row, "status", None)
			if sub_status in {AuditStatus.IN_PROGRESS, AuditStatus.PAUSED}:
				in_progress_count += 1
			elif sub_status is AuditStatus.SUBMITTED:
				submitted_count += 1
				_, summary_score = self._resolve_compact_audit_summary(
					raw_scores=getattr(row, "scores_json", {}),
					fallback_summary_score=getattr(row, "summary_score", None),
				)
				if summary_score is not None:
					submitted_scores.append(float(summary_score))

		pending_places = max(0, total_assigned - in_progress_count - submitted_count)
		return AuditorDashboardSummaryResponse(
			total_assigned_places=total_assigned,
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

			audit_code = self._build_audit_code(
				project_name=project.name,
				place_name=place.name,
				auditor_code=auditor_profile.auditor_code,
				created_at=now,
			)

			active_instrument = await get_active_instrument(self._session, INSTRUMENT_KEY)
			instrument_key = active_instrument.instrument_key if active_instrument is not None else INSTRUMENT_KEY
			instrument_version = (
				active_instrument.instrument_version if active_instrument is not None else INSTRUMENT_VERSION
			)

			# Create the submission and its context row together so the normalized
			# draft tables are immediately active once the session commits.
			audit = PlayspaceSubmission(
				project_id=project.id,
				place_id=place.id,
				auditor_profile_id=auditor_profile.id,
				audit_code=audit_code,
				instrument_key=instrument_key,
				instrument_version=instrument_version,
				status=AuditStatus.IN_PROGRESS,
				started_at=now,
				execution_mode=initial_execution_mode,
				responses_json={},
				scores_json={},
			)
			context = PlayspaceSubmissionContext(
				submission_id=audit.id,
				execution_mode=initial_execution_mode,
				schema_version=CURRENT_AUDIT_SCHEMA_VERSION,
				revision=1,
			)
			audit.submission_context = context
			self._session.add(audit)
			await self._commit_and_refresh(audit)

			audit = await self._get_existing_audit(
				project_id=project.id,
				place_id=place.id,
				auditor_profile_id=auditor_profile.id,
			)
			if audit is None:
				raise RuntimeError("Audit was created but could not be reloaded.")

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

		audit = await self._load_accessible_audit(actor=actor, audit_id=audit_id)

		self._ensure_not_submitted(
			audit=audit,
			detail="Submitted audits cannot be edited.",
		)
		self._ensure_expected_revision_matches(
			audit=audit,
			expected_revision=payload.expected_revision,
		)
		if payload.aggregate is not None:
			self._ensure_supported_schema_version(schema_version=payload.aggregate.schema_version)

		requested_mode = payload.meta.execution_mode if payload.meta is not None else None
		allowed_modes = get_allowed_execution_modes()
		self._ensure_mode_allowed(
			requested_mode=requested_mode,
			allowed_modes=allowed_modes,
			detail="The requested execution mode is not valid for this audit.",
		)

		if payload.started_at is not None:
			self._apply_pristine_started_at_correction(
				audit=audit,
				new_started_at=payload.started_at,
			)

		if payload.aggregate is not None:
			aggregate_mode = payload.aggregate.meta.execution_mode if payload.aggregate.meta is not None else None
			self._ensure_mode_allowed(
				requested_mode=aggregate_mode,
				allowed_modes=allowed_modes,
				detail="The requested execution mode is not valid for this audit.",
			)
			replace_audit_aggregate(audit=audit, aggregate=payload.aggregate)
			set_execution_mode_value(
				audit=audit,
				execution_mode=(aggregate_mode.value if aggregate_mode is not None else None),
			)
		else:
			apply_draft_patch_to_relations(audit=audit, patch=payload)

		set_aggregate_revision(audit, get_aggregate_revision(audit) + 1)
		instrument = await self._resolve_playspace_instrument_for_audit(audit=audit)
		progress = build_audit_progress_for_audit(audit=audit, instrument=instrument)
		draft_progress_percent = self._progress_percent(progress)
		set_draft_progress_percent(audit=audit, draft_progress_percent=draft_progress_percent)
		# scores_json keeps the progress cache for list/dashboard surfaces.
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

	async def submit_audit(
		self,
		*,
		actor: CurrentUserContext,
		audit_id: uuid.UUID,
		payload: AuditSubmitRequest | None = None,
	) -> AuditSessionResponse:
		"""Validate completion, calculate scores, and submit an in-progress audit."""

		audit = await self._load_accessible_audit(actor=actor, audit_id=audit_id)
		self._ensure_not_submitted(
			audit=audit,
			detail="This audit has already been submitted.",
		)
		self._ensure_expected_revision_matches(
			audit=audit,
			expected_revision=(payload.expected_revision if payload is not None else None),
		)

		# Build the JSONB snapshot from normalized tables before status changes.
		# This is the only time responses_json is written for a Playspace submission.
		responses_json = build_responses_json_from_relations(audit)
		audit.responses_json = responses_json
		instrument = await self._resolve_playspace_instrument_for_audit(audit=audit)
		progress = build_audit_progress_for_audit(audit=audit, instrument=instrument)
		if not progress.ready_to_submit:
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="Complete the pre-audit fields and all visible sections before submitting.",
			)

		calculated_scores = score_audit_for_audit(
			audit=audit,
			instrument=instrument,
			include_maximums=True,
		)
		submitted_at = datetime.now(timezone.utc)
		elapsed_minutes = int((submitted_at - audit.started_at).total_seconds() // 60)

		audit.status = AuditStatus.SUBMITTED
		audit.submitted_at = submitted_at
		audit.total_minutes = max(elapsed_minutes, 0)
		set_draft_progress_percent(audit=audit, draft_progress_percent=None)
		audit.scores_json = calculated_scores
		audit_partition = self._build_score_totals_response(calculated_scores.get("audit"))
		survey_partition = self._build_score_totals_response(calculated_scores.get("survey"))
		audit.audit_play_value_score = audit_partition.play_value_total if audit_partition is not None else None
		audit.audit_usability_score = audit_partition.usability_total if audit_partition is not None else None
		audit.survey_play_value_score = survey_partition.play_value_total if survey_partition is not None else None
		audit.survey_usability_score = survey_partition.usability_total if survey_partition is not None else None
		overall_payload = self._build_score_totals_response(calculated_scores.get("overall"))
		audit.summary_score = self._combined_construct_total(overall_payload)
		await self._commit_and_refresh(audit)

		return await self._build_audit_session_response(
			audit=audit,
			project=audit.project,
			place=audit.place,
		)

	async def notify_submit_failure(
		self,
		*,
		actor: CurrentUserContext,
		audit_id: uuid.UUID,
	) -> None:
		"""Send the owning auditor an email when a background offline submit fails.

		Only the auditor who owns the audit may call this endpoint - the mobile
		app fires it best-effort after the background sync fails, so we enforce
		auditor-only access and silently swallow email delivery errors to avoid
		blocking the response.
		"""
		import asyncio
		import logging
		from app.email_service.send_email import send_audit_submit_failure_email

		_log = logging.getLogger(__name__)

		# Auditor-only: only the owning auditor may request this notification.
		if actor.role is not CurrentUserRole.AUDITOR:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Auditor access is required for this endpoint.",
			)

		audit = await self._load_accessible_audit(actor=actor, audit_id=audit_id)
		auditor_profile = audit.auditor_profile

		to_email = auditor_profile.email
		if not to_email:
			# No email address on file - nothing to send; return silently.
			_log.warning(
				"notify_submit_failure: no email for auditor_profile_id=%s audit_id=%s",
				auditor_profile.id,
				audit_id,
			)
			return

		place_name = audit.place.name if audit.place is not None else str(audit.place_id)
		project_name = audit.project.name if audit.project is not None else str(audit.project_id)
		auditor_name = auditor_profile.full_name
		audit_code = audit.audit_code

		# Fire-and-forget in the default executor so we never block the event loop on
		# the synchronous Brevo HTTP call.
		loop = asyncio.get_event_loop()
		loop.run_in_executor(
			None,
			lambda: send_audit_submit_failure_email(
				to_email=to_email,
				auditor_name=auditor_name,
				place_name=place_name,
				audit_code=audit_code,
				project_name=project_name,
			),
		)

	async def _load_accessible_audit(
		self,
		*,
		actor: CurrentUserContext,
		audit_id: uuid.UUID,
	) -> PlayspaceSubmission:
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

	async def _get_audit(self, *, audit_id: uuid.UUID) -> PlayspaceSubmission:
		"""Load an audit with all relationships needed for draft state access."""

		result = await self._session.execute(
			select(PlayspaceSubmission)
			.where(PlayspaceSubmission.id == audit_id)
			.options(
				selectinload(PlayspaceSubmission.project),
				selectinload(PlayspaceSubmission.place),
				selectinload(PlayspaceSubmission.auditor_profile),
				selectinload(PlayspaceSubmission.submission_context),
				selectinload(PlayspaceSubmission.pre_submission_answers),
				selectinload(PlayspaceSubmission.submission_sections)
				.selectinload(PlayspaceSubmissionSection.question_responses)
				.selectinload(PlayspaceQuestionResponse.scale_answers),
				selectinload(PlayspaceSubmission.submission_sections)
				.selectinload(PlayspaceSubmissionSection.question_responses)
				.selectinload(PlayspaceQuestionResponse.checklist_answer),
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
	) -> PlayspaceSubmission | None:
		"""Return the current audit for the same project-place pair and auditor."""

		result = await self._session.execute(
			select(PlayspaceSubmission)
			.where(
				PlayspaceSubmission.project_id == project_id,
				PlayspaceSubmission.place_id == place_id,
				PlayspaceSubmission.auditor_profile_id == auditor_profile_id,
			)
			.limit(1)
			.options(
				selectinload(PlayspaceSubmission.project),
				selectinload(PlayspaceSubmission.place),
				selectinload(PlayspaceSubmission.auditor_profile),
				selectinload(PlayspaceSubmission.submission_context),
				selectinload(PlayspaceSubmission.pre_submission_answers),
				selectinload(PlayspaceSubmission.submission_sections)
				.selectinload(PlayspaceSubmissionSection.question_responses)
				.selectinload(PlayspaceQuestionResponse.scale_answers),
				selectinload(PlayspaceSubmission.submission_sections)
				.selectinload(PlayspaceSubmissionSection.question_responses)
				.selectinload(PlayspaceQuestionResponse.checklist_answer),
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
		if actor.user_id is not None:
			query = query.where(AuditorProfile.user_id == actor.user_id)
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
				detail="Auditor profile not found for the authenticated user.",
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
				AuditorAssignment.place_id == place_id,
			)
			.limit(1)
		)
		assignment_id = result.scalar_one_or_none()
		if assignment_id is None:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="The current auditor is not assigned to this project/place pair.",
			)

	def _ensure_audit_access(self, *, actor: CurrentUserContext, audit: PlayspaceSubmission) -> None:
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
		if actor.role is CurrentUserRole.AUDITOR and actor.account_id == audit.auditor_profile.account_id:
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

	async def _resolve_playspace_instrument_for_audit(self, audit: PlayspaceSubmission) -> PlayspaceInstrumentResponse:
		"""Return the submission's exact instrument version when present, otherwise active/canonical fallback."""

		instrument_key = audit.instrument_key or INSTRUMENT_KEY
		instrument_version = audit.instrument_version
		stored_instrument = None
		if instrument_version is not None:
			stored_instrument = await get_instrument_version(self._session, instrument_key, instrument_version)
		active_instrument = await get_active_instrument(self._session, instrument_key)

		stored_response = (
			build_instrument_response_from_row(stored_instrument) if stored_instrument is not None else None
		)
		active_response = (
			build_instrument_response_from_row(active_instrument) if active_instrument is not None else None
		)
		if stored_response is not None and active_response is not None:
			responses_json = build_responses_json_from_relations(audit)
			stored_match_count = self._count_matching_response_question_keys(
				instrument=stored_response,
				responses_json=responses_json,
			)
			active_match_count = self._count_matching_response_question_keys(
				instrument=active_response,
				responses_json=responses_json,
			)
			if active_match_count > stored_match_count:
				return active_response
			return stored_response
		if stored_response is not None:
			return stored_response
		if active_response is not None:
			return active_response
		return get_canonical_instrument_response()

	@classmethod
	def _count_matching_response_question_keys(
		cls,
		*,
		instrument: PlayspaceInstrumentResponse,
		responses_json: dict[str, object],
	) -> int:
		"""Count stored response question keys that exist in an instrument definition."""

		response_question_keys = cls._collect_response_question_keys(responses_json)
		instrument_question_keys = {
			question.question_key for section in instrument.sections for question in section.questions
		}
		return len(response_question_keys.intersection(instrument_question_keys))

	@staticmethod
	def _collect_response_question_keys(responses_json: dict[str, object]) -> set[str]:
		"""Collect question keys present in a canonical audit response payload."""

		sections_payload = responses_json.get("sections")
		if not isinstance(sections_payload, dict):
			return set()
		question_keys: set[str] = set()
		for section_payload in sections_payload.values():
			if not isinstance(section_payload, dict):
				continue
			responses_payload = section_payload.get("responses")
			if not isinstance(responses_payload, dict):
				continue
			question_keys.update(key for key in responses_payload.keys() if isinstance(key, str))
		return question_keys

	# make it verbose
	async def _build_audit_session_response(
		self,
		*,
		audit: PlayspaceSubmission,
		project: Project,
		place: Place,
	) -> AuditSessionResponse:
		"""Build the stable API payload shared by create/resume, save, and submit."""

		responses_json = build_responses_json_from_relations(audit)
		allowed_modes = get_allowed_execution_modes()
		selected_mode = resolve_execution_mode_for_audit(audit=audit)

		instrument = await self._resolve_playspace_instrument_for_audit(audit=audit)
		progress = build_audit_progress_for_audit(audit=audit, instrument=instrument)
		meta = AuditMetaResponse(
			execution_mode=self._parse_execution_mode(get_execution_mode_value(audit)),
			final_comments=get_final_comments_value(audit),
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
			auditor_code=audit.auditor_profile.auditor_code,
			project_id=project.id,
			project_name=project.name,
			place_id=place.id,
			place_name=place.name,
			place_type=place.place_type,
			execution_mode=self._parse_execution_mode(audit.execution_mode),
			allowed_execution_modes=allowed_modes,
			selected_execution_mode=selected_mode,
			status=audit.status,
			instrument_key=instrument.instrument_key,
			instrument_version=instrument.instrument_version,
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
			scores=self._build_audit_scores_response(
				audit=audit,
				fallback_mode=selected_mode,
				instrument=instrument,
			),
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

		project_segment = "".join(character for character in project_name.upper() if character.isalnum())
		trimmed_project_segment = project_segment[:8] or "PROJECT"
		place_segment = "".join(character for character in place_name.upper() if character.isalnum())
		trimmed_place_segment = place_segment[:12] or "PLAYSPACE"
		timestamp_segment = created_at.strftime("%Y%m%d%H%M%S")
		return f"{trimmed_project_segment}-{trimmed_place_segment}-{auditor_code}-{timestamp_segment}"

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
		audit: PlayspaceSubmission,
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
				detail=("The submitted audit aggregate schema is not supported by this server."),
			)

	def _apply_pristine_started_at_correction(
		self,
		*,
		audit: PlayspaceSubmission,
		new_started_at: datetime,
	) -> None:
		"""Update audit.started_at to the mobile execute-time stamp.

		Only honored when the audit is still pristine on the server and the
		new timestamp is strictly later than the current placeholder. Mirrors
		mobile canStampLocalAuditStart() in store-sync-core.ts.
		"""

		# Normalize to a timezone-aware UTC datetime for monotonic comparison.
		if new_started_at.tzinfo is None:
			candidate = new_started_at.replace(tzinfo=timezone.utc)
		else:
			candidate = new_started_at.astimezone(timezone.utc)

		current = audit.started_at
		if current is not None and current.tzinfo is None:
			current = current.replace(tzinfo=timezone.utc)

		if current is not None and candidate <= current:
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="A later started_at correction is required.",
			)

		if not self._is_audit_pristine(audit=audit):
			raise HTTPException(
				status_code=status.HTTP_400_BAD_REQUEST,
				detail="started_at can only be corrected while the audit is pristine.",
			)

		audit.started_at = candidate

	def _is_audit_pristine(self, *, audit: PlayspaceSubmission) -> bool:
		"""Return True when no execution mode, pre-audit, or section content exists."""

		if get_execution_mode_value(audit) is not None:
			return False
		if get_final_comments_value(audit) is not None:
			return False

		draft_progress = get_draft_progress_percent(audit)
		if draft_progress is not None and draft_progress > 0:
			return False

		payload = build_responses_json_from_relations(audit)
		pre_audit_value = payload.get("pre_audit")
		if isinstance(pre_audit_value, dict):
			for value in pre_audit_value.values():
				if value is None or value == "" or value == []:
					continue
				return False

		sections_value = payload.get("sections")
		if isinstance(sections_value, dict):
			for section_value in sections_value.values():
				if not isinstance(section_value, dict):
					continue
				note = section_value.get("note")
				if isinstance(note, str) and note.strip():
					return False
				responses = section_value.get("responses")
				if isinstance(responses, dict) and len(responses) > 0:
					return False

		return True

	def _build_audit_aggregate_response(
		self,
		*,
		audit: PlayspaceSubmission,
		responses_json: dict[str, object],
	) -> AuditAggregateResponse:
		"""Build the canonical aggregate payload returned to aggregate-sync clients."""

		return AuditAggregateResponse(
			schema_version=get_aggregate_schema_version(audit),
			revision=get_aggregate_revision(audit),
			meta=AuditMetaResponse(
				execution_mode=self._parse_execution_mode(get_execution_mode_value(audit)),
				final_comments=get_final_comments_value(audit),
			),
			pre_audit=self._build_pre_audit_response(responses_json=responses_json),
			sections=self._build_section_state_response_map(responses_json=responses_json),
		)

	async def _refresh_draft_cache_fields(self, *, audit: PlayspaceSubmission) -> None:
		"""Rebuild cached progress projections.

		Does NOT write responses_json - for drafts the normalized tables are the
		source of truth. responses_json is written only at submission time.
		"""

		instrument = await self._resolve_playspace_instrument_for_audit(audit=audit)
		progress = build_audit_progress_for_audit(audit=audit, instrument=instrument)
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
			place_size=self._read_optional_string(pre_audit_payload, "place_size"),
			current_users_0_5=self._read_optional_string(pre_audit_payload, "current_users_0_5"),
			current_users_6_12=self._read_optional_string(pre_audit_payload, "current_users_6_12"),
			current_users_13_17=self._read_optional_string(pre_audit_payload, "current_users_13_17"),
			current_users_18_plus=self._read_optional_string(pre_audit_payload, "current_users_18_plus"),
			playspace_busyness=self._read_optional_string(pre_audit_payload, "playspace_busyness"),
			season=self._read_optional_string(pre_audit_payload, "season"),
			weather_conditions=self._to_string_list(pre_audit_payload.get("weather_conditions")),
			wind_conditions=self._read_optional_string(pre_audit_payload, "wind_conditions"),
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
		audit: PlayspaceSubmission,
		fallback_mode: ExecutionMode | None,
		instrument: PlayspaceInstrumentResponse | None = None,
	) -> AuditScoresResponse:
		"""Build the typed Playspace score payload from cached or live audit totals."""

		raw_scores = self._resolve_score_payload(audit=audit, instrument=instrument)
		execution_mode = self._parse_execution_mode(raw_scores.get("execution_mode")) or fallback_mode
		return AuditScoresResponse(
			draft_progress_percent=get_draft_progress_percent(audit),
			execution_mode=execution_mode,
			audit=self._build_score_totals_response(raw_scores.get("audit")),
			survey=self._build_score_totals_response(raw_scores.get("survey")),
			overall=self._build_score_totals_response(raw_scores.get("overall")),
			by_section=self._build_score_collection_response(raw_scores.get("by_section")),
			by_domain=self._build_score_collection_response(raw_scores.get("by_domain")),
		)

	def _resolve_compact_audit_summary(
		self,
		*,
		raw_scores: object,
		responses_json: object | None = None,
		fallback_summary_score: float | None,
	) -> tuple[AuditScoreTotalsResponse | None, float | None]:
		"""Resolve compact totals and summary score from cached values only."""

		live_score_totals = self._build_live_score_totals_response(responses_json=responses_json)
		if live_score_totals is not None:
			live_summary_score = self._combined_construct_total(live_score_totals)
			if live_summary_score is not None:
				return live_score_totals, live_summary_score

		score_totals = self._build_score_totals_response(self._read_json_dict(raw_scores).get("overall"))
		compact_summary_score = self._combined_construct_total(score_totals)
		if compact_summary_score is not None:
			return score_totals, compact_summary_score
		return score_totals, fallback_summary_score

	def _resolve_score_payload(
		self,
		*,
		audit: PlayspaceSubmission,
		instrument: PlayspaceInstrumentResponse | None = None,
	) -> dict[str, object]:
		"""Return the current score payload, recalculating submitted audits when needed."""

		raw_scores = dict(audit.scores_json) if isinstance(audit.scores_json, dict) else {}
		if audit.status is not AuditStatus.SUBMITTED:
			return raw_scores

		try:
			return score_audit_for_audit(
				audit=audit,
				include_maximums=True,
				instrument=instrument,
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
		provision_total = score_payload.get("provision_total")
		provision_total_max = score_payload.get("provision_total_max")
		diversity_total = score_payload.get("diversity_total")
		diversity_total_max = score_payload.get("diversity_total_max")
		challenge_total = score_payload.get("challenge_total")
		challenge_total_max = score_payload.get("challenge_total_max")
		sociability_total = score_payload.get("sociability_total")
		sociability_total_max = score_payload.get("sociability_total_max")
		play_value_total = score_payload.get("play_value_total")
		play_value_total_max = score_payload.get("play_value_total_max")
		usability_total = score_payload.get("usability_total")
		usability_total_max = score_payload.get("usability_total_max")
		numeric_values = [
			provision_total,
			provision_total_max,
			diversity_total,
			diversity_total_max,
			challenge_total,
			challenge_total_max,
			sociability_total,
			sociability_total_max,
			play_value_total,
			play_value_total_max,
			usability_total,
			usability_total_max,
		]
		if not all(isinstance(value, int | float) for value in numeric_values):
			return None

		float_values = [float(value) for value in numeric_values if isinstance(value, int | float)]
		if any(value is None for value in float_values) or len(float_values) != 12:
			return None

		return AuditScoreTotalsResponse(
			provision_total=float_values[0],
			provision_total_max=float_values[1],
			diversity_total=float_values[2],
			diversity_total_max=float_values[3],
			challenge_total=float_values[4],
			challenge_total_max=float_values[5],
			sociability_total=float_values[6],
			sociability_total_max=float_values[7],
			play_value_total=float_values[8],
			play_value_total_max=float_values[9],
			usability_total=float_values[10],
			usability_total_max=float_values[11],
		)

	def _build_live_score_totals_response(
		self,
		*,
		responses_json: object | None,
	) -> AuditScoreTotalsResponse | None:
		"""Calculate live totals with max values from one nested audit payload."""

		responses_payload = self._read_json_dict(responses_json)
		if not responses_payload:
			return None

		try:
			live_scores = score_audit(
				responses_json=responses_payload,
				include_maximums=True,
			)
		except ValueError:
			return None

		return self._build_score_totals_response(live_scores.get("overall"))

	@staticmethod
	def _combined_construct_total(
		score_totals: AuditScoreTotalsResponse | None,
	) -> float | None:
		"""Return the combined play-value plus usability total for compact summaries."""

		if score_totals is None:
			return None
		return round(score_totals.play_value_total + score_totals.usability_total, 2)

	@staticmethod
	def _build_score_pair(
		score_totals: AuditScoreTotalsResponse | None,
	) -> ScorePairResponse | None:
		"""Collapse one score bucket into the compact PV/U pair used by dashboards."""

		if score_totals is None:
			return None
		return ScorePairResponse(
			pv=round(score_totals.play_value_total, 1),
			u=round(score_totals.usability_total, 1),
		)

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
			coerced_value = cls._coerce_question_response_entry(entry_key, entry_value)
			if coerced_value is None and entry_value is not None:
				continue
			payload[entry_key] = coerced_value
		return payload

	@classmethod
	def _coerce_question_response_entry(
		cls,
		key: str,
		value: object,
	) -> str | list[str] | dict[str, str] | None:
		"""Coerce one response entry, including legacy stringified checklist values."""

		if key == "selected_option_keys":
			return cls._to_string_list(cls._parse_stringified_json_value(value) if isinstance(value, str) else value)
		if key == "other_details":
			return cls._to_string_dict(cls._parse_stringified_json_value(value) if isinstance(value, str) else value)
		return cls._coerce_question_response_value(value)

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

	@staticmethod
	def _to_string_dict(value: object) -> dict[str, str]:
		"""Safely coerce one unknown JSON-like value into a string dictionary."""

		if not isinstance(value, dict):
			return {}
		return {key: entry for key, entry in value.items() if isinstance(key, str) and isinstance(entry, str)}

	@staticmethod
	def _parse_stringified_json_value(value: str) -> object:
		"""Parse legacy checklist values saved as JSON strings or Python literal strings."""

		trimmed_value = value.strip()
		if not trimmed_value:
			return value
		try:
			return json.loads(trimmed_value)
		except json.JSONDecodeError:
			pass
		try:
			return ast.literal_eval(trimmed_value)
		except (SyntaxError, ValueError):
			return value

	@staticmethod
	def _read_optional_string(payload: dict[str, object], key: str) -> str | None:
		"""Read one optional string key from a JSON-like mapping."""

		value = payload.get(key)
		return value if isinstance(value, str) else None

	@staticmethod
	def _coerce_float(value: object) -> float | None:
		"""Convert ints/floats to float while rejecting other runtime types."""

		if isinstance(value, int | float):
			return float(value)
		return None

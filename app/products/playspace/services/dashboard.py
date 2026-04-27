"""
Playspace dashboard query service.
"""

from __future__ import annotations

import math
import uuid
from datetime import date, datetime

from fastapi import HTTPException, status
from sqlalchemy import and_, distinct, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.actors import CurrentUserContext, require_manager_user
from app.products.playspace.execution_mode_scope import (
	execution_mode_includes_audit,
	execution_mode_includes_survey,
)
from app.models import (
	Account,
	AuditorAssignment,
	AuditorProfile,
	AuditStatus,
	ManagerProfile,
	Place,
	PlayspaceSubmission,
	Project,
	ProjectPlace,
)
from app.products.playspace.schemas import (
	AccountDetailResponse,
	AccountStatsResponse,
	AuditorSummaryResponse,
	ManagerAuditRowResponse,
	ManagerAuditsListResponse,
	ManagerAuditsSummaryResponse,
	ManagerPlaceRowResponse,
	ManagerPlacesListResponse,
	ManagerPlacesSummaryResponse,
	ManagerProfileResponse,
	PlayspacePlaceRollup,
	PlaceActivityStatus,
	PlaceAuditHistoryItemResponse,
	PlaceHistoryResponse,
	PlaceSummaryResponse,
	ProjectDetailResponse,
	ProjectStatsResponse,
	ProjectStatus,
	ProjectSummaryResponse,
	RecentActivityResponse,
	ScorePairResponse,
)

PROJECT_NOT_FOUND_DETAIL = "Project not found."
DEFAULT_PAGE_SIZE = 10
MAX_PAGE_SIZE = 100


def _derive_project_status(start_date: date | None, end_date: date | None) -> ProjectStatus:
	"""Classify a project into a simple planned/active/completed state."""

	today = date.today()
	if start_date is not None and start_date > today:
		return "planned"
	if end_date is not None and end_date < today:
		return "completed"
	return "active"


def _derive_row_place_status(submissions: list[PlayspaceSubmission]) -> PlaceActivityStatus:
	"""Single lifecycle column for a place: any in-progress work, else any submitted, else not started."""

	if any(s.status in {AuditStatus.IN_PROGRESS, AuditStatus.PAUSED} for s in submissions):
		return "in_progress"
	if any(s.status == AuditStatus.SUBMITTED for s in submissions):
		return "submitted"
	return "not_started"


def _derive_place_activity_status(
	submissions: list[PlayspaceSubmission],
) -> tuple[PlaceActivityStatus, PlaceActivityStatus]:
	"""Summarize audit vs survey activity; `both` mode counts on both axes."""
	place_audit_status: PlaceActivityStatus = "not_started"
	place_survey_status: PlaceActivityStatus = "not_started"
	audits = [s for s in submissions if execution_mode_includes_audit(s.execution_mode)]
	surveys = [s for s in submissions if execution_mode_includes_survey(s.execution_mode)]
	if any(submission.status == AuditStatus.SUBMITTED for submission in audits):
		place_audit_status = "submitted"
	elif any(submission.status in {AuditStatus.IN_PROGRESS, AuditStatus.PAUSED} for submission in audits):
		place_audit_status = "in_progress"
	if any(submission.status == AuditStatus.SUBMITTED for submission in surveys):
		place_survey_status = "submitted"
	elif any(submission.status in {AuditStatus.IN_PROGRESS, AuditStatus.PAUSED} for submission in surveys):
		place_survey_status = "in_progress"
	return place_audit_status, place_survey_status


def _round_score(value: float | None) -> float | None:
	"""Round a score to one decimal place when present."""

	if value is None:
		return None
	return round(value, 1)


def _average(values: list[float]) -> float | None:
	"""Return the mean of a non-empty list of floats, or None if empty."""

	if not values:
		return None
	return _round_score(sum(values) / len(values))


def _average_submitted_score(audits: list[PlayspaceSubmission]) -> float | None:
	"""Return the mean summary score across submitted audits."""

	submitted_scores = [
		audit.summary_score
		for audit in audits
		if audit.status == AuditStatus.SUBMITTED and audit.summary_score is not None
	]
	if not submitted_scores:
		return None

	return _round_score(sum(submitted_scores) / len(submitted_scores))


def _latest_activity_timestamp(audits: list[PlayspaceSubmission]) -> datetime | None:
	"""Return the latest visible audit activity timestamp for an auditor or place."""

	timestamps = [audit.submitted_at if audit.submitted_at is not None else audit.started_at for audit in audits]
	if not timestamps:
		return None
	return max(timestamps)


def _round_score_pair(pv: float | None, u: float | None) -> ScorePairResponse | None:
	"""Build the compact PV/U pair when both values are present."""

	if pv is None or u is None:
		return None
	return ScorePairResponse(pv=round(pv, 1), u=round(u, 1))


def _mean_score_pair(
	submissions: list[PlayspaceSubmission],
	*,
	partition: str,
) -> ScorePairResponse | None:
	"""Average the persisted partition scores across submitted Playspace submissions."""

	pv_values: list[float] = []
	u_values: list[float] = []
	for submission in submissions:
		if submission.status != AuditStatus.SUBMITTED:
			continue
		if partition == "audit":
			pv_value = submission.audit_play_value_score
			u_value = submission.audit_usability_score
		else:
			pv_value = submission.survey_play_value_score
			u_value = submission.survey_usability_score
		if pv_value is None or u_value is None:
			continue
		pv_values.append(float(pv_value))
		u_values.append(float(u_value))

	if not pv_values or not u_values:
		return None
	return _round_score_pair(sum(pv_values) / len(pv_values), sum(u_values) / len(u_values))


def _overall_score_pair(
	audit_scores: ScorePairResponse | None,
	survey_scores: ScorePairResponse | None,
) -> ScorePairResponse | None:
	"""Combine the audit and survey mean score pairs into the requested overall pair."""

	if audit_scores is None or survey_scores is None:
		return None
	return _round_score_pair(audit_scores.pv + survey_scores.pv, audit_scores.u + survey_scores.u)


def _mean_score_pairs(score_pairs: list[ScorePairResponse | None]) -> ScorePairResponse | None:
	"""Average a list of compact PV/U score pairs while ignoring missing values."""

	valid_pairs = [score_pair for score_pair in score_pairs if score_pair is not None]
	if not valid_pairs:
		return None
	return _round_score_pair(
		sum(score_pair.pv for score_pair in valid_pairs) / len(valid_pairs),
		sum(score_pair.u for score_pair in valid_pairs) / len(valid_pairs),
	)


def _total_pages(total_count: int, page_size: int) -> int:
	"""Return a stable page count for paginated responses."""

	if total_count <= 0:
		return 1
	return max(1, math.ceil(total_count / page_size))


def _collect_project_auditor_ids(project: Project) -> set[uuid.UUID]:
	"""Collect unique auditor profile IDs assigned at project or place scope."""

	return {assignment.auditor_profile_id for assignment in project.assignments}


def _build_place_rollup(submissions: list[PlayspaceSubmission]) -> PlayspacePlaceRollup:
	"""Compute place-level coverage statuses, counts, and score pairs."""

	place_audit_status, place_survey_status = _derive_place_activity_status(submissions)
	audit_mean_scores = _mean_score_pair(submissions, partition="audit")
	survey_mean_scores = _mean_score_pair(submissions, partition="survey")
	overall_scores = _overall_score_pair(audit_mean_scores, survey_mean_scores)

	return {
		"place_audit_status": place_audit_status,
		"place_survey_status": place_survey_status,
		"place_audit_count": sum(
			1 for submission in submissions if execution_mode_includes_audit(submission.execution_mode)
		),
		"place_survey_count": sum(
			1 for submission in submissions if execution_mode_includes_survey(submission.execution_mode)
		),
		"audit_mean_scores": audit_mean_scores,
		"survey_mean_scores": survey_mean_scores,
		"overall_scores": overall_scores,
	}


class PlayspaceDashboardService:
	"""Read service for Playspace manager dashboard screens."""

	def __init__(self, session: AsyncSession):
		self._session = session

	def _ensure_manager_scope(self, actor: CurrentUserContext, account_id: uuid.UUID) -> None:
		"""Enforce manager access and account ownership boundaries."""

		require_manager_user(actor)
		if actor.account_id is None:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="Manager account context is required for this endpoint.",
			)
		if actor.account_id != account_id:
			raise HTTPException(
				status_code=status.HTTP_403_FORBIDDEN,
				detail="This actor cannot access the requested account.",
			)

	async def _get_account_model(self, account_id: uuid.UUID) -> Account | None:
		"""Load an account row without hydrating the full dashboard graph."""

		stmt = select(Account).where(Account.id == account_id)
		result = await self._session.execute(stmt)
		return result.scalar_one_or_none()

	async def _get_project_model(self, project_id: uuid.UUID) -> Project | None:
		"""Load a project with the relationships needed for project screens."""

		stmt = (
			select(Project)
			.where(Project.id == project_id)
			.options(
				selectinload(Project.assignments),
				selectinload(Project.places).selectinload(Place.playspace_submissions),
				selectinload(Project.places).selectinload(Place.assignments),
			)
		)
		result = await self._session.execute(stmt)
		return result.scalar_one_or_none()

	async def _get_place_model(self, place_id: uuid.UUID) -> Place | None:
		"""Load a place with linked projects plus audit relationships."""

		stmt = (
			select(Place)
			.where(Place.id == place_id)
			.options(
				selectinload(Place.projects),
				selectinload(Place.playspace_submissions).selectinload(PlayspaceSubmission.auditor_profile),
				selectinload(Place.playspace_submissions).selectinload(PlayspaceSubmission.project),
			)
		)
		result = await self._session.execute(stmt)
		return result.scalar_one_or_none()

	async def _require_account_for_actor(
		self,
		*,
		actor: CurrentUserContext,
		account_id: uuid.UUID,
	) -> Account:
		"""Load an account and enforce manager scope."""

		self._ensure_manager_scope(actor, account_id)
		account = await self._get_account_model(account_id)
		if account is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Account not found.",
			)
		return account

	async def _require_project_for_actor(
		self,
		*,
		actor: CurrentUserContext,
		project_id: uuid.UUID,
	) -> Project:
		"""Load a project and enforce manager scope."""

		project = await self._get_project_model(project_id)
		if project is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail=PROJECT_NOT_FOUND_DETAIL,
			)
		self._ensure_manager_scope(actor, project.account_id)
		return project

	async def _require_place_for_actor(
		self,
		*,
		actor: CurrentUserContext,
		project_id: uuid.UUID,
		place_id: uuid.UUID,
	) -> tuple[Project, Place]:
		"""Load a project-place pair and enforce manager access to the project owner."""

		project = await self._require_project_for_actor(actor=actor, project_id=project_id)
		place = await self._get_place_model(place_id)
		if place is None:
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Place not found.",
			)
		if not any(linked_project.id == project.id for linked_project in place.projects):
			raise HTTPException(
				status_code=status.HTTP_404_NOT_FOUND,
				detail="Place not found in the requested project.",
			)
		return project, place

	async def list_auditors(self, actor: CurrentUserContext, account_id: uuid.UUID) -> list[AuditorSummaryResponse]:
		"""Return all auditors in the system that the manager has not assigned to any projects."""

		self._ensure_manager_scope(actor, account_id)
		result = await self._session.execute(select(AuditorProfile))
		auditor_profiles = result.scalars().all()
		auditor_profile_ids = [profile.id for profile in auditor_profiles]
		assignment_counts_subquery = (
			select(
				AuditorProfile.id.label("id"),
				AuditorProfile.account_id.label("account_id"),
				AuditorProfile.auditor_code.label("auditor_code"),
				AuditorProfile.full_name.label("full_name"),
				AuditorProfile.email.label("email"),
				AuditorProfile.age_range.label("age_range"),
				AuditorProfile.gender.label("gender"),
				AuditorProfile.country.label("country"),
				AuditorProfile.role.label("role"),
				func.count(AuditorAssignment.id).label("assignments_count"),
				func.max(func.coalesce(PlayspaceSubmission.submitted_at, PlayspaceSubmission.started_at)).label(
					"last_active_at"
				),
				func.count(PlayspaceSubmission.id)
				.filter(PlayspaceSubmission.status == AuditStatus.SUBMITTED)
				.label("completed_audits"),
			)
			.select_from(AuditorProfile)
			.outerjoin(
				AuditorAssignment,
				AuditorAssignment.auditor_profile_id.in_(auditor_profile_ids),
			)
			.outerjoin(
				PlayspaceSubmission,
				PlayspaceSubmission.auditor_profile_id == AuditorProfile.id,
			)
			.group_by(AuditorProfile.id)
			.subquery()
		)
		stmt = (
			select(
				AuditorProfile.id.label("id"),
				AuditorProfile.account_id.label("account_id"),
				AuditorProfile.auditor_code.label("auditor_code"),
				AuditorProfile.full_name.label("full_name"),
				AuditorProfile.email.label("email"),
				AuditorProfile.age_range.label("age_range"),
				AuditorProfile.gender.label("gender"),
				AuditorProfile.country.label("country"),
				AuditorProfile.role.label("role"),
				assignment_counts_subquery.c.assignments_count.label("assignments_count"),
				assignment_counts_subquery.c.last_active_at.label("last_active_at"),
				assignment_counts_subquery.c.completed_audits.label("completed_audits"),
			)
			.join(
				assignment_counts_subquery,
				assignment_counts_subquery.c.id == AuditorProfile.id,
			)
			.order_by(func.lower(AuditorProfile.auditor_code).asc())
			.limit(MAX_PAGE_SIZE)
		)

		result = await self._session.execute(stmt)
		rows = result.all()

		return [
			AuditorSummaryResponse(
				id=row.id,
				account_id=row.account_id,
				auditor_code=row.auditor_code,
				full_name=row.full_name,
				email=row.email,
				age_range=row.age_range,
				gender=row.gender,
				country=row.country,
				role=row.role,
				assignments_count=int(row.assignments_count or 0),
				completed_audits=int(row.completed_audits or 0),
				last_active_at=row.last_active_at,
			)
			for row in rows
		]

	async def _get_account_auditor_summaries_db(
		self,
		account_id: uuid.UUID,
	) -> list[AuditorSummaryResponse]:
		"""Fetch manager-facing auditor summaries for a real account."""

		assignment_counts_subquery = (
			select(
				AuditorAssignment.auditor_profile_id.label("auditor_profile_id"),
				func.count(AuditorAssignment.id).label("assignments_count"),
			)
			.select_from(AuditorAssignment)
			.join(Project, AuditorAssignment.project_id == Project.id)
			.where(Project.account_id == account_id)
			.group_by(AuditorAssignment.auditor_profile_id)
			.subquery()
		)

		audit_stats_subquery = (
			select(
				PlayspaceSubmission.auditor_profile_id.label("auditor_profile_id"),
				func.count(PlayspaceSubmission.id)
				.filter(PlayspaceSubmission.status == AuditStatus.SUBMITTED)
				.label("completed_audits"),
				func.max(func.coalesce(PlayspaceSubmission.submitted_at, PlayspaceSubmission.started_at)).label(
					"last_active_at"
				),
			)
			.select_from(PlayspaceSubmission)
			.join(Project, PlayspaceSubmission.project_id == Project.id)
			.where(Project.account_id == account_id)
			.group_by(PlayspaceSubmission.auditor_profile_id)
			.subquery()
		)

		stmt = (
			select(
				AuditorProfile.id.label("id"),
				AuditorProfile.account_id.label("account_id"),
				AuditorProfile.auditor_code.label("auditor_code"),
				AuditorProfile.full_name.label("full_name"),
				AuditorProfile.email.label("email"),
				AuditorProfile.age_range.label("age_range"),
				AuditorProfile.gender.label("gender"),
				AuditorProfile.country.label("country"),
				AuditorProfile.role.label("role"),
				assignment_counts_subquery.c.assignments_count.label("assignments_count"),
				audit_stats_subquery.c.completed_audits.label("completed_audits"),
				audit_stats_subquery.c.last_active_at.label("last_active_at"),
			)
			.join(
				assignment_counts_subquery,
				assignment_counts_subquery.c.auditor_profile_id == AuditorProfile.id,
			)
			.outerjoin(
				audit_stats_subquery,
				audit_stats_subquery.c.auditor_profile_id == AuditorProfile.id,
			)
			.order_by(func.lower(AuditorProfile.auditor_code).asc())
		)
		result = await self._session.execute(stmt)
		rows = result.all()

		return [
			AuditorSummaryResponse(
				id=row.id,
				account_id=row.account_id,
				auditor_code=row.auditor_code,
				full_name=row.full_name,
				email=row.email,
				age_range=row.age_range,
				gender=row.gender,
				country=row.country,
				role=row.role,
				assignments_count=int(row.assignments_count or 0),
				completed_audits=int(row.completed_audits or 0),
				last_active_at=row.last_active_at,
			)
			for row in rows
		]

	async def get_account_detail(
		self,
		actor: CurrentUserContext,
		account_id: uuid.UUID,
	) -> AccountDetailResponse:
		"""Return the manager dashboard payload for the requested account."""

		account = await self._require_account_for_actor(
			actor=actor,
			account_id=account_id,
		)

		manager_profiles = await self.list_manager_profiles(actor=actor, account_id=account_id)
		auditors = await self._get_account_auditor_summaries_db(account_id=account_id)
		total_projects_result = await self._session.execute(
			select(func.count(Project.id)).where(Project.account_id == account_id)
		)
		total_places_result = await self._session.execute(
			select(func.count(ProjectPlace.place_id))
			.select_from(ProjectPlace)
			.join(Project, ProjectPlace.project_id == Project.id)
			.where(Project.account_id == account_id)
		)
		total_audits_completed_result = await self._session.execute(
			select(func.count(PlayspaceSubmission.id))
			.select_from(PlayspaceSubmission)
			.join(Project, PlayspaceSubmission.project_id == Project.id)
			.where(
				Project.account_id == account_id,
				PlayspaceSubmission.status == AuditStatus.SUBMITTED,
			)
		)
		recent_activity_result = await self._session.execute(
			select(
				PlayspaceSubmission.id.label("audit_id"),
				PlayspaceSubmission.audit_code.label("audit_code"),
				Project.id.label("project_id"),
				Project.name.label("project_name"),
				Place.id.label("place_id"),
				Place.name.label("place_name"),
				PlayspaceSubmission.submitted_at.label("completed_at"),
				PlayspaceSubmission.summary_score.label("score"),
				PlayspaceSubmission.audit_play_value_score.label("audit_play_value_score"),
				PlayspaceSubmission.audit_usability_score.label("audit_usability_score"),
				PlayspaceSubmission.survey_play_value_score.label("survey_play_value_score"),
				PlayspaceSubmission.survey_usability_score.label("survey_usability_score"),
			)
			.select_from(PlayspaceSubmission)
			.join(Place, PlayspaceSubmission.place_id == Place.id)
			.join(Project, PlayspaceSubmission.project_id == Project.id)
			.where(
				Project.account_id == account_id,
				PlayspaceSubmission.status == AuditStatus.SUBMITTED,
				PlayspaceSubmission.submitted_at.is_not(None),
			)
			.order_by(PlayspaceSubmission.submitted_at.desc())
			.limit(5)
		)
		recent_activity = [
			RecentActivityResponse(
				audit_id=row.audit_id,
				audit_code=row.audit_code,
				project_id=row.project_id,
				project_name=row.project_name,
				place_id=row.place_id,
				place_name=row.place_name,
				completed_at=row.completed_at,
				score=_round_score(row.score),
				score_pair=_overall_score_pair(
					_round_score_pair(
						float(row.audit_play_value_score) if row.audit_play_value_score is not None else None,
						float(row.audit_usability_score) if row.audit_usability_score is not None else None,
					),
					_round_score_pair(
						float(row.survey_play_value_score) if row.survey_play_value_score is not None else None,
						float(row.survey_usability_score) if row.survey_usability_score is not None else None,
					),
				),
			)
			for row in recent_activity_result.all()
		]
		primary_manager = next(
			(profile for profile in manager_profiles if profile.is_primary),
			manager_profiles[0] if manager_profiles else None,
		)

		return AccountDetailResponse(
			id=account.id,
			name=account.name,
			email=account.email,
			account_type=account.account_type,
			created_at=account.created_at,
			primary_manager=primary_manager,
			stats=AccountStatsResponse(
				total_projects=int(total_projects_result.scalar_one() or 0),
				total_places=int(total_places_result.scalar_one() or 0),
				total_auditors=len(auditors),
				total_audits_completed=int(total_audits_completed_result.scalar_one() or 0),
			),
			recent_activity=recent_activity,
		)

	async def list_manager_profiles(
		self,
		actor: CurrentUserContext,
		account_id: uuid.UUID,
	) -> list[ManagerProfileResponse]:
		"""Return manager profiles for the requested account."""

		self._ensure_manager_scope(actor, account_id)

		stmt = (
			select(ManagerProfile)
			.where(ManagerProfile.account_id == account_id)
			.order_by(ManagerProfile.is_primary.desc(), ManagerProfile.full_name.asc())
		)
		result = await self._session.execute(stmt)
		profiles = result.scalars().all()

		return [
			ManagerProfileResponse(
				id=profile.id,
				account_id=profile.account_id,
				full_name=profile.full_name,
				email=profile.email,
				phone=profile.phone,
				position=profile.position,
				organization=profile.organization,
				is_primary=profile.is_primary,
				created_at=profile.created_at,
			)
			for profile in profiles
		]

	async def list_account_projects(
		self,
		actor: CurrentUserContext,
		account_id: uuid.UUID,
	) -> list[ProjectSummaryResponse]:
		"""Return project summaries for the requested account."""

		self._ensure_manager_scope(actor, account_id)
		places_count_subquery = (
			select(
				ProjectPlace.project_id.label("project_id"),
				func.count(ProjectPlace.place_id).label("places_count"),
			)
			.group_by(ProjectPlace.project_id)
			.subquery()
		)
		project_assignment_scope = (
			select(
				AuditorAssignment.project_id.label("project_id"),
				AuditorAssignment.auditor_profile_id.label("auditor_profile_id"),
			)
			.where(AuditorAssignment.project_id.is_not(None))
			.subquery()
		)
		auditors_count_subquery = (
			select(
				project_assignment_scope.c.project_id.label("project_id"),
				func.count(distinct(project_assignment_scope.c.auditor_profile_id)).label("auditors_count"),
			)
			.group_by(project_assignment_scope.c.project_id)
			.subquery()
		)
		audit_stats_subquery = (
			select(
				PlayspaceSubmission.project_id.label("project_id"),
				func.count(PlayspaceSubmission.id)
				.filter(PlayspaceSubmission.status == AuditStatus.SUBMITTED)
				.label("audits_completed"),
				func.avg(PlayspaceSubmission.summary_score)
				.filter(
					and_(
						PlayspaceSubmission.status == AuditStatus.SUBMITTED,
						PlayspaceSubmission.summary_score.is_not(None),
					)
				)
				.label("average_score"),
				func.avg(PlayspaceSubmission.audit_play_value_score).label("audit_mean_pv"),
				func.avg(PlayspaceSubmission.audit_usability_score).label("audit_mean_u"),
				func.avg(PlayspaceSubmission.survey_play_value_score).label("survey_mean_pv"),
				func.avg(PlayspaceSubmission.survey_usability_score).label("survey_mean_u"),
			)
			.select_from(PlayspaceSubmission)
			.group_by(PlayspaceSubmission.project_id)
			.subquery()
		)

		result = await self._session.execute(
			select(
				Project.id.label("id"),
				Project.account_id.label("account_id"),
				Project.name.label("name"),
				Project.overview.label("overview"),
				Project.place_types.label("place_types"),
				Project.start_date.label("start_date"),
				Project.end_date.label("end_date"),
				places_count_subquery.c.places_count.label("places_count"),
				auditors_count_subquery.c.auditors_count.label("auditors_count"),
				audit_stats_subquery.c.audits_completed.label("audits_completed"),
				audit_stats_subquery.c.average_score.label("average_score"),
				audit_stats_subquery.c.audit_mean_pv.label("audit_mean_pv"),
				audit_stats_subquery.c.audit_mean_u.label("audit_mean_u"),
				audit_stats_subquery.c.survey_mean_pv.label("survey_mean_pv"),
				audit_stats_subquery.c.survey_mean_u.label("survey_mean_u"),
			)
			.select_from(Project)
			.outerjoin(
				places_count_subquery,
				places_count_subquery.c.project_id == Project.id,
			)
			.outerjoin(
				auditors_count_subquery,
				auditors_count_subquery.c.project_id == Project.id,
			)
			.outerjoin(
				audit_stats_subquery,
				audit_stats_subquery.c.project_id == Project.id,
			)
			.where(Project.account_id == account_id)
			.order_by(Project.created_at.desc(), Project.name.asc())
		)

		return [
			ProjectSummaryResponse(
				id=row.id,
				account_id=row.account_id,
				name=row.name,
				overview=row.overview,
				place_types=list(row.place_types),
				start_date=row.start_date,
				end_date=row.end_date,
				status=_derive_project_status(row.start_date, row.end_date),
				places_count=int(row.places_count or 0),
				auditors_count=int(row.auditors_count or 0),
				audits_completed=int(row.audits_completed or 0),
				average_score=_round_score(float(row.average_score) if row.average_score is not None else None),
				average_scores=_overall_score_pair(
					_round_score_pair(
						float(row.audit_mean_pv) if row.audit_mean_pv is not None else None,
						float(row.audit_mean_u) if row.audit_mean_u is not None else None,
					),
					_round_score_pair(
						float(row.survey_mean_pv) if row.survey_mean_pv is not None else None,
						float(row.survey_mean_u) if row.survey_mean_u is not None else None,
					),
				),
			)
			for row in result.all()
		]

	async def list_account_auditors(
		self,
		actor: CurrentUserContext,
		account_id: uuid.UUID,
	) -> list[AuditorSummaryResponse]:
		"""Return manager-facing auditor summaries for the requested account."""

		self._ensure_manager_scope(actor, account_id)
		return await self._get_account_auditor_summaries_db(account_id=account_id)

	async def list_account_places(
		self,
		*,
		actor: CurrentUserContext,
		account_id: uuid.UUID,
		page: int = 1,
		page_size: int = DEFAULT_PAGE_SIZE,
		search: str | None = None,
		sort: str | None = None,
		project_ids: list[uuid.UUID] | None = None,
		auditor_ids: list[uuid.UUID] | None = None,
		audit_statuses: list[str] | None = None,
		survey_statuses: list[str] | None = None,
	) -> ManagerPlacesListResponse:
		"""Return paginated manager place rows with Playspace-specific rollups."""

		await self._require_account_for_actor(actor=actor, account_id=account_id)

		_valid_axis_statuses = {"not_started", "in_progress", "submitted", "complete"}
		normalized_search = search.strip() if search is not None and search.strip() else None
		normalized_project_ids = project_ids or []
		normalized_auditor_ids = set(auditor_ids) if auditor_ids else set()
		normalized_audit_statuses = {s for s in (audit_statuses or []) if s in _valid_axis_statuses}
		normalized_survey_statuses = {s for s in (survey_statuses or []) if s in _valid_axis_statuses}
		safe_page_size = max(1, min(page_size, MAX_PAGE_SIZE))
		offset = max(page - 1, 0) * safe_page_size

		projects_result = await self._session.execute(
			select(Project)
			.where(Project.account_id == account_id)
			.options(selectinload(Project.places).selectinload(Place.playspace_submissions))
		)
		projects = projects_result.scalars().all()

		all_rows: list[ManagerPlaceRowResponse] = []
		for project in projects:
			if normalized_project_ids and project.id not in normalized_project_ids:
				continue
			for place in project.places:
				project_submissions = [
					submission for submission in place.playspace_submissions if submission.project_id == project.id
				]
				if normalized_auditor_ids:
					has_matching_auditor = any(
						s.auditor_profile_id in normalized_auditor_ids for s in project_submissions
					)
					if not has_matching_auditor:
						continue
				submitted_submissions = [
					submission for submission in project_submissions if submission.status == AuditStatus.SUBMITTED
				]
				rollup = _build_place_rollup(project_submissions)
				all_rows.append(
					ManagerPlaceRowResponse(
						id=place.id,
						project_id=project.id,
						project_name=project.name,
						name=place.name,
						city=place.city,
						province=place.province,
						country=place.country,
						postal_code=place.postal_code,
						address=place.address,
						place_type=place.place_type,
						status=_derive_row_place_status(project_submissions),
						place_audit_status=rollup["place_audit_status"],
						place_survey_status=rollup["place_survey_status"],
						audits_completed=len(submitted_submissions),
						average_score=_average_submitted_score(submitted_submissions),
						last_audited_at=_latest_activity_timestamp(submitted_submissions),
						place_audit_count=rollup["place_audit_count"],
						place_survey_count=rollup["place_survey_count"],
						audit_mean_scores=rollup["audit_mean_scores"],
						survey_mean_scores=rollup["survey_mean_scores"],
						overall_scores=rollup["overall_scores"],
					)
				)

		summary_rows = list(all_rows)
		filtered_rows = list(all_rows)
		if normalized_search is not None:
			search_term = normalized_search.lower()
			filtered_rows = [
				row
				for row in filtered_rows
				if search_term
				in " ".join(
					part
					for part in [
						row.name,
						row.project_name,
						row.address or "",
						row.postal_code or "",
						row.city or "",
						row.province or "",
						row.country or "",
						row.place_type or "",
					]
				).lower()
			]

		if normalized_audit_statuses:
			filtered_rows = [row for row in filtered_rows if row.place_audit_status in normalized_audit_statuses]
		if normalized_survey_statuses:
			filtered_rows = [row for row in filtered_rows if row.place_survey_status in normalized_survey_statuses]

		raw_sort = sort.strip() if sort is not None and sort.strip() else "-last_audited_at"
		is_descending = raw_sort.startswith("-")
		sort_key = raw_sort[1:] if is_descending else raw_sort

		def sort_value(row: ManagerPlaceRowResponse) -> str | float | datetime | None:
			if sort_key == "project_name":
				return row.project_name.lower()
			if sort_key == "status":
				return row.status
			if sort_key == "audits_completed":
				return row.audits_completed
			if sort_key == "average_score":
				return row.average_score
			if sort_key == "name":
				return row.name.lower()
			return row.last_audited_at

		non_null_rows = [row for row in filtered_rows if sort_value(row) is not None]
		null_rows = [row for row in filtered_rows if sort_value(row) is None]
		non_null_rows = sorted(
			non_null_rows,
			key=lambda row: (sort_value(row), row.name.lower(), str(row.id)),
			reverse=is_descending,
		)
		filtered_rows = [*non_null_rows, *null_rows]

		total_count = len(filtered_rows)
		paged_rows = filtered_rows[offset : offset + safe_page_size]

		return ManagerPlacesListResponse(
			items=paged_rows,
			total_count=total_count,
			page=page,
			page_size=safe_page_size,
			total_pages=_total_pages(total_count, safe_page_size),
			summary=ManagerPlacesSummaryResponse(
				total_places=len(summary_rows),
				submitted_places=sum(1 for row in summary_rows if row.status == "submitted"),
				in_progress_places=sum(1 for row in summary_rows if row.status == "in_progress"),
				average_score=_average(
					[float(row.average_score) for row in summary_rows if row.average_score is not None]
				),
				completed_place_audits=sum(1 for row in summary_rows if row.place_audit_status == "submitted"),
				completed_place_surveys=sum(1 for row in summary_rows if row.place_survey_status == "submitted"),
				audit_mean_scores=_mean_score_pairs([row.audit_mean_scores for row in summary_rows]),
				survey_mean_scores=_mean_score_pairs([row.survey_mean_scores for row in summary_rows]),
				overall_scores=_mean_score_pairs([row.overall_scores for row in summary_rows]),
			),
		)

	async def list_account_audits(
		self,
		*,
		actor: CurrentUserContext,
		account_id: uuid.UUID,
		page: int = 1,
		page_size: int = DEFAULT_PAGE_SIZE,
		search: str | None = None,
		sort: str | None = None,
		project_ids: list[uuid.UUID] | None = None,
		auditor_ids: list[uuid.UUID] | None = None,
		statuses: list[str] | None = None,
	) -> ManagerAuditsListResponse:
		"""Return paginated manager audit rows with SQL-backed filtering."""

		await self._require_account_for_actor(actor=actor, account_id=account_id)

		normalized_search = search.strip() if search is not None and search.strip() else None
		normalized_project_ids = project_ids or []
		normalized_auditor_ids = auditor_ids or []
		normalized_statuses = {
			raw_status for raw_status in (statuses or []) if raw_status in {"IN_PROGRESS", "PAUSED", "SUBMITTED"}
		}
		safe_page_size = max(1, min(page_size, MAX_PAGE_SIZE))
		offset = max(page - 1, 0) * safe_page_size

		filtered_rows_query = (
			select(
				PlayspaceSubmission.id.label("audit_id"),
				PlayspaceSubmission.audit_code.label("audit_code"),
				PlayspaceSubmission.status.label("status"),
				PlayspaceSubmission.execution_mode.label("execution_mode"),
				AuditorProfile.auditor_code.label("auditor_code"),
				Project.id.label("project_id"),
				Project.name.label("project_name"),
				Place.id.label("place_id"),
				Place.name.label("place_name"),
				PlayspaceSubmission.started_at.label("started_at"),
				PlayspaceSubmission.submitted_at.label("submitted_at"),
				PlayspaceSubmission.summary_score.label("summary_score"),
				PlayspaceSubmission.audit_play_value_score.label("audit_play_value_score"),
				PlayspaceSubmission.audit_usability_score.label("audit_usability_score"),
				PlayspaceSubmission.survey_play_value_score.label("survey_play_value_score"),
				PlayspaceSubmission.survey_usability_score.label("survey_usability_score"),
			)
			.select_from(PlayspaceSubmission)
			.join(Place, PlayspaceSubmission.place_id == Place.id)
			.join(Project, PlayspaceSubmission.project_id == Project.id)
			.join(AuditorProfile, PlayspaceSubmission.auditor_profile_id == AuditorProfile.id)
			.where(Project.account_id == account_id)
		)

		if normalized_search is not None:
			search_term = f"%{normalized_search}%"
			filtered_rows_query = filtered_rows_query.where(
				or_(
					PlayspaceSubmission.audit_code.ilike(search_term),
					AuditorProfile.auditor_code.ilike(search_term),
					Place.name.ilike(search_term),
					Project.name.ilike(search_term),
				)
			)

		if normalized_project_ids:
			filtered_rows_query = filtered_rows_query.where(Project.id.in_(normalized_project_ids))

		if normalized_auditor_ids:
			filtered_rows_query = filtered_rows_query.where(AuditorProfile.id.in_(normalized_auditor_ids))

		if normalized_statuses:
			filtered_rows_query = filtered_rows_query.where(PlayspaceSubmission.status.in_(normalized_statuses))

		filtered_rows_subquery = filtered_rows_query.subquery()
		total_count_result = await self._session.execute(select(func.count()).select_from(filtered_rows_subquery))
		total_count = int(total_count_result.scalar_one() or 0)

		raw_sort = sort.strip() if sort is not None and sort.strip() else "-submitted_at"
		is_descending = raw_sort.startswith("-")
		sort_key = raw_sort[1:] if is_descending else raw_sort
		sort_map = {
			"audit_code": filtered_rows_subquery.c.audit_code,
			"status": filtered_rows_subquery.c.status,
			"auditor_code": filtered_rows_subquery.c.auditor_code,
			"project_name": filtered_rows_subquery.c.project_name,
			"place_name": filtered_rows_subquery.c.place_name,
			"started_at": filtered_rows_subquery.c.started_at,
			"submitted_at": filtered_rows_subquery.c.submitted_at,
			"summary_score": filtered_rows_subquery.c.summary_score,
		}
		sort_column = sort_map.get(sort_key, filtered_rows_subquery.c.submitted_at)
		primary_order = sort_column.desc().nulls_last() if is_descending else sort_column.asc().nulls_last()

		rows_result = await self._session.execute(
			select(filtered_rows_subquery)
			.order_by(
				primary_order,
				filtered_rows_subquery.c.started_at.desc(),
				filtered_rows_subquery.c.audit_id.desc(),
			)
			.offset(offset)
			.limit(safe_page_size)
		)
		rows = rows_result.all()

		summary_result = await self._session.execute(
			select(
				func.count(PlayspaceSubmission.id).label("total_audits"),
				func.count(PlayspaceSubmission.id)
				.filter(PlayspaceSubmission.status == AuditStatus.SUBMITTED)
				.label("submitted_audits"),
				func.count(PlayspaceSubmission.id)
				.filter(PlayspaceSubmission.status.in_([AuditStatus.IN_PROGRESS, AuditStatus.PAUSED]))
				.label("in_progress_audits"),
				func.avg(PlayspaceSubmission.summary_score)
				.filter(PlayspaceSubmission.summary_score.is_not(None))
				.label("average_score"),
				func.avg(PlayspaceSubmission.audit_play_value_score).label("audit_mean_pv"),
				func.avg(PlayspaceSubmission.audit_usability_score).label("audit_mean_u"),
				func.avg(PlayspaceSubmission.survey_play_value_score).label("survey_mean_pv"),
				func.avg(PlayspaceSubmission.survey_usability_score).label("survey_mean_u"),
			)
			.select_from(PlayspaceSubmission)
			.join(Project, PlayspaceSubmission.project_id == Project.id)
			.where(Project.account_id == account_id)
		)
		summary_row = summary_result.one()

		return ManagerAuditsListResponse(
			items=[
				ManagerAuditRowResponse(
					audit_id=row.audit_id,
					audit_code=row.audit_code,
					status=(row.status.value if isinstance(row.status, AuditStatus) else row.status),
					auditor_code=row.auditor_code,
					project_id=row.project_id,
					project_name=row.project_name,
					place_id=row.place_id,
					place_name=row.place_name,
					started_at=row.started_at,
					submitted_at=row.submitted_at,
					summary_score=_round_score(float(row.summary_score) if row.summary_score is not None else None),
					execution_mode=row.execution_mode,
					score_pair=_overall_score_pair(
						_round_score_pair(
							float(row.audit_play_value_score) if row.audit_play_value_score is not None else None,
							float(row.audit_usability_score) if row.audit_usability_score is not None else None,
						),
						_round_score_pair(
							float(row.survey_play_value_score) if row.survey_play_value_score is not None else None,
							float(row.survey_usability_score) if row.survey_usability_score is not None else None,
						),
					),
				)
				for row in rows
			],
			total_count=total_count,
			page=page,
			page_size=safe_page_size,
			total_pages=_total_pages(total_count, safe_page_size),
			summary=ManagerAuditsSummaryResponse(
				total_audits=int(summary_row.total_audits or 0),
				submitted_audits=int(summary_row.submitted_audits or 0),
				in_progress_audits=int(summary_row.in_progress_audits or 0),
				average_score=_round_score(
					float(summary_row.average_score) if summary_row.average_score is not None else None
				),
				average_scores=_overall_score_pair(
					_round_score_pair(
						float(summary_row.audit_mean_pv) if summary_row.audit_mean_pv is not None else None,
						float(summary_row.audit_mean_u) if summary_row.audit_mean_u is not None else None,
					),
					_round_score_pair(
						float(summary_row.survey_mean_pv) if summary_row.survey_mean_pv is not None else None,
						float(summary_row.survey_mean_u) if summary_row.survey_mean_u is not None else None,
					),
				),
			),
		)

	async def get_project_detail(
		self,
		actor: CurrentUserContext,
		project_id: uuid.UUID,
	) -> ProjectDetailResponse:
		"""Return a project detail payload."""

		project = await self._require_project_for_actor(
			actor=actor,
			project_id=project_id,
		)
		return ProjectDetailResponse(
			id=project.id,
			account_id=project.account_id,
			name=project.name,
			overview=project.overview,
			place_types=list(project.place_types),
			start_date=project.start_date,
			end_date=project.end_date,
			est_places=project.est_places,
			est_auditors=project.est_auditors,
			auditor_description=project.auditor_description,
			created_at=project.created_at,
			created_by_user_id=project.created_by_user_id,
		)

	async def get_project_stats(
		self,
		actor: CurrentUserContext,
		project_id: uuid.UUID,
	) -> ProjectStatsResponse:
		"""Return manager-facing project summary stats."""

		project = await self._require_project_for_actor(
			actor=actor,
			project_id=project_id,
		)

		submitted_audits = [
			audit
			for place in project.places
			for audit in place.playspace_submissions
			if audit.project_id == project.id and audit.status == AuditStatus.SUBMITTED
		]
		in_progress_audits = sum(
			1
			for place in project.places
			for audit in place.playspace_submissions
			if audit.project_id == project.id and audit.status == AuditStatus.IN_PROGRESS
		)
		auditors_count = _collect_project_auditor_ids(project)

		places_with_audits = sum(
			1
			for place in project.places
			if any(audit.project_id == project.id for audit in place.playspace_submissions)
		)
		overall_scores = _mean_score_pairs(
			[
				_overall_score_pair(
					_mean_score_pair(
						[audit for audit in place.playspace_submissions if audit.project_id == project.id],
						partition="audit",
					),
					_mean_score_pair(
						[audit for audit in place.playspace_submissions if audit.project_id == project.id],
						partition="survey",
					),
				)
				for place in project.places
			]
		)
		return ProjectStatsResponse(
			project_id=project.id,
			places_count=len(project.places),
			places_with_audits=places_with_audits,
			audits_completed=len(submitted_audits),
			auditors_count=len(auditors_count),
			in_progress_audits=in_progress_audits,
			average_score=_average_submitted_score(submitted_audits),
			average_scores=overall_scores,
		)

	async def list_project_places(
		self,
		actor: CurrentUserContext,
		project_id: uuid.UUID,
	) -> list[PlaceSummaryResponse]:
		"""Return project-scoped place summaries."""

		project = await self._require_project_for_actor(
			actor=actor,
			project_id=project_id,
		)

		place_summaries: list[PlaceSummaryResponse] = []
		for place in sorted(project.places, key=lambda current_place: current_place.name.lower()):
			submitted_audits = [
				audit
				for audit in place.playspace_submissions
				if audit.project_id == project.id and audit.status == AuditStatus.SUBMITTED
			]
			project_audits = [audit for audit in place.playspace_submissions if audit.project_id == project.id]
			rollup = _build_place_rollup(project_audits)
			place_summaries.append(
				PlaceSummaryResponse(
					id=place.id,
					project_id=project.id,
					name=place.name,
					city=place.city,
					province=place.province,
					country=place.country,
					postal_code=place.postal_code,
					address=place.address,
					place_type=place.place_type,
					place_audit_status=rollup["place_audit_status"],
					place_survey_status=rollup["place_survey_status"],
					audits_completed=len(submitted_audits),
					average_score=_average_submitted_score(submitted_audits),
					last_audited_at=_latest_activity_timestamp(submitted_audits),
					place_audit_count=rollup["place_audit_count"],
					place_survey_count=rollup["place_survey_count"],
					audit_mean_scores=rollup["audit_mean_scores"],
					survey_mean_scores=rollup["survey_mean_scores"],
					overall_scores=rollup["overall_scores"],
				),
			)

		return place_summaries

	async def list_place_audits(
		self,
		*,
		actor: CurrentUserContext,
		project_id: uuid.UUID,
		place_id: uuid.UUID,
	) -> list[PlaceAuditHistoryItemResponse]:
		"""Return audit history rows for one place."""

		project, place = await self._require_place_for_actor(
			actor=actor,
			project_id=project_id,
			place_id=place_id,
		)
		sorted_audits = sorted(
			[audit for audit in place.playspace_submissions if audit.project_id == project.id],
			key=lambda audit: audit.submitted_at if audit.submitted_at is not None else audit.started_at,
			reverse=True,
		)
		history_rows: list[PlaceAuditHistoryItemResponse] = []
		for audit in sorted_audits:
			history_rows.append(
				PlaceAuditHistoryItemResponse(
					audit_id=audit.id,
					audit_code=audit.audit_code,
					project_id=project.id,
					project_name=project.name,
					auditor_code=audit.auditor_profile.auditor_code,
					status=audit.status.value,
					started_at=audit.started_at,
					submitted_at=audit.submitted_at,
					summary_score=_round_score(audit.summary_score),
					execution_mode=audit.execution_mode,
					score_pair=_overall_score_pair(
						_round_score_pair(audit.audit_play_value_score, audit.audit_usability_score),
						_round_score_pair(audit.survey_play_value_score, audit.survey_usability_score),
					),
				)
			)
		return history_rows

	async def get_place_history(
		self,
		*,
		actor: CurrentUserContext,
		project_id: uuid.UUID,
		place_id: uuid.UUID,
	) -> PlaceHistoryResponse:
		"""Return aggregate history metrics plus audit rows for one place."""

		project, place = await self._require_place_for_actor(
			actor=actor,
			project_id=project_id,
			place_id=place_id,
		)
		audits = [audit for audit in place.playspace_submissions if audit.project_id == project.id]
		submitted_audits = [audit for audit in audits if audit.status == AuditStatus.SUBMITTED]
		in_progress_audits = [
			audit for audit in audits if audit.status in {AuditStatus.IN_PROGRESS, AuditStatus.PAUSED}
		]
		rollup = _build_place_rollup(audits)
		history_rows = await self.list_place_audits(
			actor=actor,
			project_id=project.id,
			place_id=place_id,
		)

		latest_submitted_at = _latest_activity_timestamp(submitted_audits)
		return PlaceHistoryResponse(
			place_id=place.id,
			place_name=place.name,
			address=place.address,
			city=place.city,
			province=place.province,
			country=place.country,
			postal_code=place.postal_code,
			lat=place.lat,
			lng=place.lng,
			project_id=project.id,
			project_name=project.name,
			total_audits=len(audits),
			submitted_audits=len(submitted_audits),
			in_progress_audits=len(in_progress_audits),
			average_submitted_score=_average_submitted_score(submitted_audits),
			latest_submitted_at=latest_submitted_at,
			audits=history_rows,
			place_audit_status=rollup["place_audit_status"],
			place_survey_status=rollup["place_survey_status"],
			place_audit_count=rollup["place_audit_count"],
			place_survey_count=rollup["place_survey_count"],
			audit_mean_scores=rollup["audit_mean_scores"],
			survey_mean_scores=rollup["survey_mean_scores"],
			overall_scores=rollup["overall_scores"],
		)

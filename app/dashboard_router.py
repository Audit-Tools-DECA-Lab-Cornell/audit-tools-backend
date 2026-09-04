"""Dashboard REST API endpoints for manager/admin views."""

from __future__ import annotations

import json
import re
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request as FastAPIRequest, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import Select, and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.auth import (
	_build_invite_url,
	_build_manager_invite_url,
	_ensure_manager_profile_for_user,
	_get_manager_profile_for_user,
	get_auth_session,
	get_current_user,
	_clean_name,
)
from app.auth_security import generate_email_verification_token, hash_verification_token
from app.email_service import send_auditor_invite_email, send_manager_invite_email
from app.models import (
	Account,
	AccountType,
	Assignment,
	Audit,
	AuditStatus,
	Auditor,
	AuditorInvite,
	ManagerInvite,
	ManagerProfile,
	Place,
	Project,
	ProjectPlace,
	User,
	YeeAuditSubmission,
)
from app.products.yee.schemas.dashboard import (
	ManagerAuditEditRequest,
	ManagerAuditEditState,
	PlaceComparisonGroup,
	RawDataExportRow,
)
from app.products.yee.services.dashboard import (
	_build_submission_scores,
	_canonical_score_from_submission,
	_decode_audit_participant_payload,
	_display_auditor_code,
	_empty_domain_scores,
	_extract_domain_weights,
	_extract_score,
	_format_timestamp,
	_repair_missing_yee_submission,
	_score_from_audit_fallback,
	participant_id_from_info,
)
from app.products.yee.services.runtime_scoring import RuntimeScorer, RuntimeScoringResolutionError
from app.products.yee.services.dashboard import (
	fetch_manager_audit_edit_state as _service_fetch_manager_audit_edit_state,
)
from app.products.yee.services.dashboard import (
	fetch_place_comparison_groups as _service_fetch_place_comparison_groups,
)
from app.products.yee.services.dashboard import (
	fetch_raw_data_rows as _service_fetch_raw_data_rows,
)
from app.products.yee.services.dashboard import (
	update_manager_audit_edit_state as _service_update_manager_audit_edit_state,
)

router: APIRouter = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardMetricResponse(BaseModel):
	title: str
	value: str
	description: str
	trend: str


class AuditListItem(BaseModel):
	id: str
	submission_id: str | None = None
	organization: str | None = None
	project_id: str
	project_name: str
	place_id: str
	place: str
	auditor: str
	participant_id: str | None = None
	date: str
	submitted_at: str | None = None
	score: int
	total_raw_score: int = 0
	total_raw_maximum: int | None = None
	total_weighted_score: float = 0.0
	total_weighted_maximum: float | None = None
	domain_weights: dict[str, int] = Field(default_factory=dict)
	status: str


class DashboardOverviewResponse(BaseModel):
	metrics: list[DashboardMetricResponse]
	recent_activity: list[str]
	latest_audits: list[AuditListItem]
	organization_summaries: list["OrganizationSummaryItem"] = Field(default_factory=list)


class ProjectListItem(BaseModel):
	id: str
	name: str
	summary: str
	organization: str | None = None
	places: int
	audits: int
	status: str


class PlaceListItem(BaseModel):
	id: str
	name: str
	project_id: str
	project: str
	organization: str | None = None
	address: str
	postal_code: str | None = None
	assigned_auditors: list[str] = Field(default_factory=list)
	audits: int
	last_audit: str
	status: str


class ProjectPlaceItem(BaseModel):
	id: str
	name: str
	address: str
	audits: int
	last_audit: str
	status: str


class AuditorListItem(BaseModel):
	id: str
	name: str
	auditor_id: str
	email: str
	assigned_places: list[str] = Field(default_factory=list)
	completed_audits: int
	status: str


class ProjectAuditorItem(BaseModel):
	id: str
	name: str
	auditor_id: str
	assigned_places: int
	completed_audits: int
	status: str


class ProjectDetailResponse(BaseModel):
	id: str
	name: str
	description: str
	status: str
	organization: str
	place_types: list[str]
	start_date: str | None = None
	end_date: str | None = None
	estimated_places: int | None = None
	auditor_population_types: list[str]
	auditor_inclusion_exclusion_criteria: str
	auditor_notes: str
	total_places: int
	total_audits: int
	submitted_audits: int
	assigned_auditors: int
	places: list[ProjectPlaceItem]
	auditors: list[ProjectAuditorItem]
	latest_audits: list[AuditListItem]


class UserListItem(BaseModel):
	id: str
	name: str
	email: str
	role: str
	account_id: str | None = None
	organization: str
	status: str
	approved: bool
	email_verified: bool
	profile_completed: bool
	contact_info: str
	project_assignments: str


class ApproveUserRequest(BaseModel):
	user_id: uuid.UUID
	account_id: uuid.UUID | None = None


class CreateProjectRequest(BaseModel):
	name: str = Field(..., min_length=1, max_length=200)
	description: str | None = Field(default=None, max_length=2000)
	place_types: list[str] = Field(default_factory=list)
	start_date: date | None = None
	end_date: date | None = None
	estimated_places: int | None = Field(default=None, ge=0)
	auditor_population_types: list[str] = Field(default_factory=list)
	auditor_inclusion_exclusion_criteria: str | None = Field(default=None, max_length=2000)
	auditor_notes: str | None = Field(default=None, max_length=2000)


class UpdateProjectRequest(BaseModel):
	name: str = Field(..., min_length=1, max_length=200)
	description: str | None = Field(default=None, max_length=2000)
	place_types: list[str] = Field(default_factory=list)
	start_date: date | None = None
	end_date: date | None = None
	estimated_places: int | None = Field(default=None, ge=0)
	auditor_population_types: list[str] = Field(default_factory=list)
	auditor_inclusion_exclusion_criteria: str | None = Field(default=None, max_length=2000)
	auditor_notes: str | None = Field(default=None, max_length=2000)


class CreatePlaceRequest(BaseModel):
	project_id: uuid.UUID
	name: str = Field(..., min_length=1, max_length=200)
	address: str = Field(..., min_length=1, max_length=500)
	city: str | None = Field(default=None, max_length=120)
	province: str | None = Field(default=None, max_length=120)
	country: str | None = Field(default=None, max_length=120)
	postal_code: str | None = Field(default=None, max_length=32)
	place_type: str | None = Field(default=None, max_length=100)
	start_date: date | None = None
	end_date: date | None = None
	estimated_auditors: int | None = Field(default=None, ge=0)
	auditor_population_types: list[str] = Field(default_factory=list)
	auditor_inclusion_exclusion_criteria: str | None = Field(default=None, max_length=2000)
	auditor_notes: str | None = Field(default=None, max_length=2000)
	lat: float | None = None
	lng: float | None = None


class UpdatePlaceRequest(BaseModel):
	project_id: uuid.UUID
	name: str = Field(..., min_length=1, max_length=200)
	address: str = Field(..., min_length=1, max_length=500)
	city: str | None = Field(default=None, max_length=120)
	province: str | None = Field(default=None, max_length=120)
	country: str | None = Field(default=None, max_length=120)
	postal_code: str | None = Field(default=None, max_length=32)
	place_type: str | None = Field(default=None, max_length=100)
	start_date: date | None = None
	end_date: date | None = None
	estimated_auditors: int | None = Field(default=None, ge=0)
	auditor_population_types: list[str] = Field(default_factory=list)
	auditor_inclusion_exclusion_criteria: str | None = Field(default=None, max_length=2000)
	auditor_notes: str | None = Field(default=None, max_length=2000)
	lat: float | None = None
	lng: float | None = None


class CreateAuditorInviteRequest(BaseModel):
	email: str = Field(..., max_length=320)


class AuditorInviteResponse(BaseModel):
	id: str
	email: str
	status: str
	expires_at: datetime
	invite_url: str


class CreateManagerInviteRequest(BaseModel):
	full_name: str = Field(..., min_length=1, max_length=200)
	email: str = Field(..., max_length=320)


class ManagerInviteResponse(BaseModel):
	id: str
	email: str
	status: str
	expires_at: datetime
	invite_url: str | None = None
	created_at: datetime | None = None
	accepted_at: datetime | None = None


class CreateAssignmentRequest(BaseModel):
	project_id: uuid.UUID
	auditor_ids: list[uuid.UUID] = Field(..., min_length=1)
	place_ids: list[uuid.UUID] = Field(..., min_length=1)


class DeleteAssignmentRequest(BaseModel):
	project_id: uuid.UUID
	auditor_id: uuid.UUID
	place_id: uuid.UUID | None = None


class AssignmentResultItem(BaseModel):
	id: str
	auditor_id: str
	place_id: str
	project_id: str


class AssignmentResponse(BaseModel):
	created_count: int
	existing_count: int
	assignments: list[AssignmentResultItem]


class DeleteAssignmentResponse(BaseModel):
	deleted_count: int


class AuditorAssignedPlaceItem(BaseModel):
	id: str
	name: str
	project: str
	address: str
	audits: int


class PlaceAuditorItem(BaseModel):
	id: str
	name: str
	auditor_id: str
	status: str
	audit_count: int
	last_audit: str


class PlaceDetailResponse(BaseModel):
	id: str
	name: str
	address: str
	city: str
	province: str
	country: str
	postal_code: str | None = None
	place_type: str
	start_date: str | None = None
	end_date: str | None = None
	estimated_auditors: int | None = None
	auditor_population_types: list[str]
	auditor_inclusion_exclusion_criteria: str
	auditor_notes: str
	lat: float | None = None
	lng: float | None = None
	notes: str
	status: str
	project_id: str
	project_name: str
	assigned_auditors: int
	total_audits: int
	submitted_audits: int
	last_audit: str
	auditors: list[PlaceAuditorItem]
	comparisons: PlaceComparisonGroup


class OrganizationSummaryItem(BaseModel):
	organization: str
	users: int
	projects: int
	places: int
	audits: int


class ManagerProfileResponse(BaseModel):
	id: str
	full_name: str
	email: str
	job_title: str | None = None
	profession_disciplines: list[str] = Field(default_factory=list)
	organization: str | None = None
	phone_number: str | None = None
	manager_type: str
	date_joined: datetime
	account_creation_date: datetime | None = None
	profile_completed: bool


class UpdateManagerProfileRequest(BaseModel):
	full_name: str = Field(..., min_length=1, max_length=200)
	job_title: str = Field(..., min_length=1, max_length=200)
	# Required, but validated in the handler so the empty case returns a friendly
	# "Profession / discipline is required." 400 (matching the phone-number rule)
	# instead of a raw 422, and so whitespace-only entries are caught too.
	profession_disciplines: list[str] = Field(default_factory=list)
	organization: str = Field(..., min_length=1, max_length=200)
	phone_number: str | None = Field(default=None, max_length=50)


class ManagerTeamMemberResponse(BaseModel):
	id: str
	full_name: str
	email: str
	manager_type: str
	job_title: str | None = None
	profession_disciplines: list[str] = Field(default_factory=list)
	organization: str | None = None
	phone_number: str | None = None
	date_joined: datetime
	account_creation_date: datetime | None = None
	profile_completed: bool


class CreateSelfAuditorProfileResponse(BaseModel):
	id: str
	auditor_id: str
	email: str | None = None
	full_name: str
	account_id: str


def _require_manager_or_admin(user: User) -> None:
	if user.account_type not in {AccountType.MANAGER, AccountType.ADMIN}:
		raise HTTPException(status_code=403, detail="Manager or admin access is required.")


def _require_admin(user: User) -> None:
	if user.account_type != AccountType.ADMIN:
		raise HTTPException(status_code=403, detail="Admin access is required.")


def _manager_account_id(user: User) -> uuid.UUID | None:
	if user.account_type == AccountType.ADMIN:
		return None
	if user.account_id is None:
		raise HTTPException(status_code=409, detail="Manager account scope is not configured yet.")
	return user.account_id


def _status_for_user(user: User) -> str:
	if not user.email_verified:
		return "Email not verified"
	if not user.approved:
		return "Pending approval"
	if not user.profile_completed:
		return "Profile incomplete"
	return "Active"


def _project_scope_filter(user: User) -> ColumnElement[bool] | None:
	if user.account_type == AccountType.ADMIN:
		return None
	return Project.account_id == _manager_account_id(user)


def _manager_project_ids_subquery(user: User):
	return select(Project.id).where(Project.account_id == _manager_account_id(user))


def _manager_invited_auditor_ids_subquery(user: User):
	if user.account_type == AccountType.ADMIN:
		return select(Auditor.id).distinct()
	return select(Auditor.id).where(Auditor.account_id == _manager_account_id(user)).distinct()


def _normalize_text_list(values: list[str]) -> list[str]:
	normalized: list[str] = []
	seen: set[str] = set()
	for value in values:
		candidate = value.strip()
		if not candidate:
			continue
		key = candidate.casefold()
		if key in seen:
			continue
		seen.add(key)
		normalized.append(candidate)
	return normalized


def _serialize_auditor_profile(
	population_types: list[str],
	inclusion_exclusion_criteria: str | None,
	notes: str | None,
) -> str | None:
	payload = {
		"population_types": _normalize_text_list(population_types),
		"inclusion_exclusion_criteria": inclusion_exclusion_criteria.strip()
		if inclusion_exclusion_criteria and inclusion_exclusion_criteria.strip()
		else "",
		"notes": notes.strip() if notes and notes.strip() else "",
	}
	if not payload["population_types"] and not payload["inclusion_exclusion_criteria"] and not payload["notes"]:
		return None
	return json.dumps(payload)


def _deserialize_auditor_profile(value: str | None) -> tuple[list[str], str, str]:
	if value is None or not value.strip():
		return [], "", ""
	try:
		payload = json.loads(value)
	except json.JSONDecodeError:
		return [], "", value.strip()
	if not isinstance(payload, dict):
		return [], "", value.strip()
	population_types = payload.get("population_types")
	inclusion_exclusion_criteria = payload.get("inclusion_exclusion_criteria")
	notes = payload.get("notes")
	return (
		_normalize_text_list(population_types if isinstance(population_types, list) else []),
		inclusion_exclusion_criteria.strip() if isinstance(inclusion_exclusion_criteria, str) else "",
		notes.strip() if isinstance(notes, str) else "",
	)


async def _count_rows(
	session: AsyncSession,
	model: type[object],
	where_clause: ColumnElement[bool] | None = None,
) -> int:
	stmt = select(func.count()).select_from(model)
	if where_clause is not None:
		stmt = stmt.where(where_clause)
	return int((await session.execute(stmt)).scalar_one())


async def _fetch_audits(
	session: AsyncSession,
	user: User,
	*,
	limit: int | None = None,
) -> list[AuditListItem]:
	submission_alias = aliased(YeeAuditSubmission)
	stmt = (
		select(
			Audit,
			Project,
			Place,
			Auditor,
			submission_alias,
			Account.name,
		)
		.join(Project, Audit.project_id == Project.id)
		.join(Account, Project.account_id == Account.id)
		.join(Place, Audit.place_id == Place.id)
		.join(Auditor, Audit.auditor_profile_id == Auditor.id)
		.outerjoin(
			submission_alias,
			and_(
				submission_alias.auditor_id == Audit.auditor_profile_id,
				submission_alias.place_id == Audit.place_id,
			),
		)
		.order_by(Audit.submitted_at.desc().nullslast(), Audit.started_at.desc())
	)
	project_scope = _project_scope_filter(user)
	if project_scope is not None:
		stmt = stmt.where(project_scope)
	if user.account_type != AccountType.ADMIN:
		stmt = stmt.where(Audit.auditor_profile_id.in_(_manager_invited_auditor_ids_subquery(user)))
	if limit is not None:
		stmt = stmt.limit(limit)
	rows = (await session.execute(stmt)).all()
	items: list[AuditListItem] = []
	needs_commit = False
	scorer = RuntimeScorer(session)
	for (
		audit,
		project,
		place,
		auditor,
		submission,
		organization_name,
	) in rows:
		resolved_submission_id = submission.id if submission is not None else None
		resolved_submitted_at = submission.submitted_at if submission is not None else None
		resolved_total_raw_score = (
			submission.total_score if submission is not None else _extract_score(audit.scores_json)
		)
		resolved_total_raw_maximum: int | None = None
		resolved_total_weighted_score = 0.0
		resolved_total_weighted_maximum: float | None = None
		resolved_domain_weights = _empty_domain_scores()
		resolved_participant_info = submission.participant_info_json if submission is not None else None
		resolved_section_scores = submission.section_scores_json if submission is not None else None

		try:
			if submission is not None:
				canonical_score = await _canonical_score_from_submission(scorer, submission)
				resolved_total_raw_score = canonical_score["raw"]["total_score"]
				resolved_total_raw_maximum = canonical_score["raw"]["total_maximum"]
				resolved_total_weighted_score = canonical_score["weighted"]["total_weighted_score"]
				resolved_total_weighted_maximum = canonical_score["weighted"]["total_maximum"]
				resolved_domain_weights = canonical_score["weighted"]["raw_domain_weights"]

			if audit.status == AuditStatus.SUBMITTED and resolved_submission_id is None:
				repaired_submission = await _repair_missing_yee_submission(
					session,
					audit=audit,
					place=place,
					auditor=auditor,
					scorer=scorer,
				)
				if repaired_submission is not None:
					needs_commit = True
					resolved_submission_id = repaired_submission.id
					resolved_submitted_at = repaired_submission.submitted_at
					canonical_score = await _canonical_score_from_submission(scorer, repaired_submission)
					resolved_total_raw_score = canonical_score["raw"]["total_score"]
					resolved_total_raw_maximum = canonical_score["raw"]["total_maximum"]
					resolved_total_weighted_score = canonical_score["weighted"]["total_weighted_score"]
					resolved_total_weighted_maximum = canonical_score["weighted"]["total_maximum"]
					resolved_domain_weights = canonical_score["weighted"]["raw_domain_weights"]
					resolved_participant_info = repaired_submission.participant_info_json
					resolved_section_scores = repaired_submission.section_scores_json
				else:
					resolved_participant_info, responses = _decode_audit_participant_payload(audit)
					score = await _score_from_audit_fallback(
						scorer,
						audit,
						participant_info=resolved_participant_info,
						responses=responses,
					)
					canonical_score = dict(score["canonical_score"])
					resolved_total_raw_score = canonical_score["raw"]["total_score"]
					resolved_total_raw_maximum = canonical_score["raw"]["total_maximum"]
					resolved_total_weighted_score = canonical_score["weighted"]["total_weighted_score"]
					resolved_total_weighted_maximum = canonical_score["weighted"]["total_maximum"]
					resolved_domain_weights = canonical_score["weighted"]["raw_domain_weights"]
		except RuntimeScoringResolutionError:
			# Keep unresolved historical rows visible without fabricating maxima
			# from whichever instrument happens to be active now.
			pass

		if (
			resolved_submission_id is None
			and isinstance(resolved_participant_info, dict)
			and isinstance(resolved_section_scores, dict)
		):
			_, _, resolved_total_weighted_score = _build_submission_scores(
				resolved_section_scores,
				resolved_participant_info,
			)
			resolved_domain_weights = _extract_domain_weights(resolved_participant_info)

		items.append(
			AuditListItem(
				id=str(audit.id),
				submission_id=str(resolved_submission_id) if resolved_submission_id is not None else None,
				organization=organization_name,
				project_id=str(project.id),
				project_name=project.name,
				place_id=str(place.id),
				place=place.name,
				auditor=_display_auditor_code(auditor.auditor_code),
				participant_id=participant_id_from_info(resolved_participant_info),
				date=_format_timestamp(audit.submitted_at or audit.started_at),
				submitted_at=resolved_submitted_at.isoformat() if resolved_submitted_at is not None else None,
				score=resolved_total_raw_score,
				total_raw_score=resolved_total_raw_score,
				total_raw_maximum=resolved_total_raw_maximum,
				total_weighted_score=resolved_total_weighted_score,
				total_weighted_maximum=resolved_total_weighted_maximum,
				domain_weights=resolved_domain_weights,
				status="Submitted" if audit.status == AuditStatus.SUBMITTED else "Draft",
			)
		)

	if needs_commit:
		await session.commit()

	deduped: dict[tuple[str, str, str], AuditListItem] = {}
	for item in items:
		key = (item.project_id, item.place_id, item.auditor)
		current = deduped.get(key)
		if current is None:
			deduped[key] = item
			continue
		current_rank = 2 if current.status == "Submitted" else 1
		next_rank = 2 if item.status == "Submitted" else 1
		if next_rank > current_rank:
			deduped[key] = item
			continue
		if next_rank == current_rank:
			current_time = current.submitted_at or current.date
			next_time = item.submitted_at or item.date
			if next_time > current_time:
				deduped[key] = item
	return list(deduped.values())


async def _fetch_projects(session: AsyncSession, user: User) -> list[ProjectListItem]:
	audit_count = func.count(Audit.id)
	place_count = func.count(func.distinct(Place.id))
	stmt: Select[tuple[Project, str, int, int]] = (
		select(Project, Account.name, place_count, audit_count)
		.join(Account, Project.account_id == Account.id)
		.outerjoin(ProjectPlace, ProjectPlace.project_id == Project.id)
		.outerjoin(Place, Place.id == ProjectPlace.place_id)
		.outerjoin(Audit, and_(Audit.project_id == Project.id, Audit.place_id == ProjectPlace.place_id))
		.group_by(Project.id, Account.name)
		.order_by(Project.name.asc())
	)
	project_scope = _project_scope_filter(user)
	if project_scope is not None:
		stmt = stmt.where(project_scope)
	rows = (await session.execute(stmt)).all()
	return [
		ProjectListItem(
			id=str(project.id),
			name=project.name,
			summary=project.description or "Project summary pending",
			organization=organization_name,
			places=int(places),
			audits=int(audits),
			status="Planning" if project.start_date is None else "Active",
		)
		for project, organization_name, places, audits in rows
	]


async def _fetch_places(session: AsyncSession, user: User) -> list[PlaceListItem]:
	last_audit = func.max(Audit.submitted_at)
	audit_count = func.count(Audit.id)
	stmt = (
		select(Place, Project.id, Project.name, Account.name, audit_count, last_audit)
		.join(ProjectPlace, ProjectPlace.place_id == Place.id)
		.join(Project, ProjectPlace.project_id == Project.id)
		.join(Account, Project.account_id == Account.id)
		.outerjoin(Audit, and_(Audit.project_id == Project.id, Audit.place_id == Place.id))
		.group_by(Place.id, Project.id, Project.name, Account.name)
		.order_by(Account.name.asc(), Project.name.asc(), Place.name.asc())
	)
	project_scope = _project_scope_filter(user)
	if project_scope is not None:
		stmt = stmt.where(project_scope)
	rows = (await session.execute(stmt)).all()
	place_ids = [place.id for place, *_ in rows]
	assigned_auditors_by_place: dict[uuid.UUID, list[str]] = defaultdict(list)
	if place_ids:
		assignment_stmt = (
			select(ProjectPlace.place_id, Auditor.auditor_code)
			.join(Assignment, Assignment.project_id == ProjectPlace.project_id)
			.join(Auditor, Auditor.id == Assignment.auditor_profile_id)
			.where(
				ProjectPlace.place_id.in_(place_ids),
				or_(Assignment.place_id.is_(None), Assignment.place_id == ProjectPlace.place_id),
			)
			.order_by(Auditor.auditor_code.asc())
		)
		if user.account_type != AccountType.ADMIN:
			assignment_stmt = assignment_stmt.where(Auditor.id.in_(_manager_invited_auditor_ids_subquery(user)))
		assignment_rows = (await session.execute(assignment_stmt)).all()
		for place_id, auditor_code in assignment_rows:
			display_code = _display_auditor_code(auditor_code)
			if display_code not in assigned_auditors_by_place[place_id]:
				assigned_auditors_by_place[place_id].append(display_code)
	return [
		PlaceListItem(
			id=str(place.id),
			name=place.name,
			project_id=str(project_id),
			project=project_name,
			organization=organization_name,
			address=place.address or "",
			postal_code=place.postal_code,
			assigned_auditors=assigned_auditors_by_place.get(place.id, []),
			audits=int(audits),
			last_audit=_format_timestamp(last_submitted_at),
			status="Needs review" if int(audits) == 0 else "Up to date",
		)
		for place, project_id, project_name, organization_name, audits, last_submitted_at in rows
	]


async def _get_scoped_project(
	session: AsyncSession,
	user: User,
	project_id: uuid.UUID,
) -> tuple[Project, str]:
	stmt = select(Project, Account.name).join(Account, Project.account_id == Account.id).where(Project.id == project_id)
	project_scope = _project_scope_filter(user)
	if project_scope is not None:
		stmt = stmt.where(project_scope)
	row = (await session.execute(stmt)).one_or_none()
	if row is None:
		raise HTTPException(status_code=404, detail="Project not found.")
	project, organization_name = row
	return project, organization_name


async def _get_scoped_place(
	session: AsyncSession,
	user: User,
	place_id: uuid.UUID,
) -> tuple[Place, Project]:
	stmt = (
		select(Place, Project)
		.join(ProjectPlace, ProjectPlace.place_id == Place.id)
		.join(Project, ProjectPlace.project_id == Project.id)
		.where(Place.id == place_id)
	)
	project_scope = _project_scope_filter(user)
	if project_scope is not None:
		stmt = stmt.where(project_scope)
	row = (await session.execute(stmt)).one_or_none()
	if row is None:
		raise HTTPException(status_code=404, detail="Place not found.")
	place, project = row
	return place, project


async def _fetch_project_detail(
	session: AsyncSession,
	user: User,
	project_id: uuid.UUID,
) -> ProjectDetailResponse:
	project, organization_name = await _get_scoped_project(session, user, project_id)
	auditor_population_types, auditor_inclusion_exclusion_criteria, auditor_notes = _deserialize_auditor_profile(
		project.auditor_description
	)

	last_audit = func.max(Audit.submitted_at)
	audit_count = func.count(Audit.id)
	place_stmt = (
		select(Place, audit_count, last_audit)
		.join(ProjectPlace, ProjectPlace.place_id == Place.id)
		.outerjoin(Audit, and_(Audit.project_id == project.id, Audit.place_id == Place.id))
		.where(ProjectPlace.project_id == project.id)
		.group_by(Place.id)
		.order_by(Place.name.asc())
	)
	place_rows = (await session.execute(place_stmt)).all()
	places = [
		ProjectPlaceItem(
			id=str(place.id),
			name=place.name,
			address=place.address or "",
			audits=int(audits),
			last_audit=_format_timestamp(last_submitted_at),
			status="Needs review" if int(audits) == 0 else "Up to date",
		)
		for place, audits, last_submitted_at in place_rows
	]

	submitted_audits = await _count_rows(
		session,
		Audit,
		(Audit.status == AuditStatus.SUBMITTED) & (Audit.project_id == project.id),
	)

	# Resolve the matching submission so latest_audits carries participant_id like
	# the audits list does. The unique (auditor_id, place_id) constraint on
	# yee_audit_submissions means this outer join matches at most one row per audit,
	# so it cannot fan out the preview.
	latest_submission_alias = aliased(YeeAuditSubmission)
	latest_stmt = (
		select(Audit, Place, Auditor, latest_submission_alias)
		.join(Place, Audit.place_id == Place.id)
		.join(Auditor, Audit.auditor_profile_id == Auditor.id)
		.outerjoin(
			latest_submission_alias,
			and_(
				latest_submission_alias.auditor_id == Audit.auditor_profile_id,
				latest_submission_alias.place_id == Audit.place_id,
			),
		)
		.where(Audit.project_id == project.id)
		.order_by(Audit.submitted_at.desc().nullslast(), Audit.started_at.desc())
		.limit(8)
	)
	latest_rows = (await session.execute(latest_stmt)).all()
	latest_audits: list[AuditListItem] = []
	latest_needs_commit = False
	scorer = RuntimeScorer(session)
	for audit, place, auditor, submission in latest_rows:
		resolved_submission = submission
		resolved_participant_info = submission.participant_info_json if submission is not None else None
		resolved_total_raw_score = (
			resolved_submission.total_score
			if resolved_submission is not None
			else _extract_score(audit.scores_json)
		)
		resolved_total_raw_maximum: int | None = None
		resolved_total_weighted_score = 0.0
		resolved_total_weighted_maximum: float | None = None
		resolved_domain_weights = _empty_domain_scores()

		try:
			if resolved_submission is not None:
				canonical_score = await _canonical_score_from_submission(scorer, resolved_submission)
				resolved_total_raw_score = canonical_score["raw"]["total_score"]
				resolved_total_raw_maximum = canonical_score["raw"]["total_maximum"]
				resolved_total_weighted_score = canonical_score["weighted"]["total_weighted_score"]
				resolved_total_weighted_maximum = canonical_score["weighted"]["total_maximum"]
				resolved_domain_weights = canonical_score["weighted"]["raw_domain_weights"]
			elif audit.status == AuditStatus.SUBMITTED:
				resolved_submission = await _repair_missing_yee_submission(
					session,
					audit=audit,
					place=place,
					auditor=auditor,
					scorer=scorer,
				)
				if resolved_submission is not None:
					latest_needs_commit = True
					resolved_participant_info = resolved_submission.participant_info_json
					canonical_score = await _canonical_score_from_submission(scorer, resolved_submission)
				else:
					resolved_participant_info, responses = _decode_audit_participant_payload(audit)
					score = await _score_from_audit_fallback(
						scorer,
						audit,
						participant_info=resolved_participant_info,
						responses=responses,
					)
					canonical_score = dict(score["canonical_score"])
				resolved_total_raw_score = canonical_score["raw"]["total_score"]
				resolved_total_raw_maximum = canonical_score["raw"]["total_maximum"]
				resolved_total_weighted_score = canonical_score["weighted"]["total_weighted_score"]
				resolved_total_weighted_maximum = canonical_score["weighted"]["total_maximum"]
				resolved_domain_weights = canonical_score["weighted"]["raw_domain_weights"]
		except RuntimeScoringResolutionError:
			# Keep unresolved historical rows visible without fabricating maxima
			# from whichever instrument happens to be active now.
			pass

		latest_audits.append(
			AuditListItem(
				id=str(audit.id),
				submission_id=str(resolved_submission.id) if resolved_submission is not None else None,
				project_id=str(project.id),
				project_name=project.name,
				place_id=str(place.id),
				place=place.name,
				auditor=_display_auditor_code(auditor.auditor_code),
				participant_id=participant_id_from_info(resolved_participant_info),
				date=_format_timestamp(audit.submitted_at or audit.started_at),
				submitted_at=(
					resolved_submission.submitted_at.isoformat() if resolved_submission is not None else None
				),
				score=resolved_total_raw_score,
				total_raw_score=resolved_total_raw_score,
				total_raw_maximum=resolved_total_raw_maximum,
				total_weighted_score=resolved_total_weighted_score,
				total_weighted_maximum=resolved_total_weighted_maximum,
				domain_weights=resolved_domain_weights,
				status="Submitted" if audit.status == AuditStatus.SUBMITTED else "Draft",
			)
		)
	if latest_needs_commit:
		await session.commit()

	assigned_places = func.count(func.distinct(ProjectPlace.place_id))
	completed_audits = func.count(func.distinct(Audit.id))
	auditor_stmt = (
		select(Auditor, User.name, assigned_places, completed_audits)
		.join(Assignment, Assignment.auditor_profile_id == Auditor.id)
		.join(ProjectPlace, ProjectPlace.project_id == Assignment.project_id)
		.outerjoin(User, Auditor.user_id == User.id)
		.outerjoin(
			Audit,
			(Audit.auditor_profile_id == Auditor.id)
			& (Audit.status == AuditStatus.SUBMITTED)
			& (Audit.project_id == project.id),
		)
		.where(
			Assignment.project_id == project.id,
			or_(Assignment.place_id.is_(None), Assignment.place_id == ProjectPlace.place_id),
		)
		.group_by(Auditor.id, User.name)
		.order_by(User.name.asc().nullslast(), Auditor.auditor_code.asc())
	)
	if user.account_type != AccountType.ADMIN:
		auditor_stmt = auditor_stmt.where(Auditor.id.in_(_manager_invited_auditor_ids_subquery(user)))
	auditor_rows = (await session.execute(auditor_stmt)).all()
	auditors = [
		ProjectAuditorItem(
			id=str(auditor.id),
			name=user_name or _display_auditor_code(auditor.auditor_code),
			auditor_id=_display_auditor_code(auditor.auditor_code),
			assigned_places=int(place_total),
			completed_audits=int(audit_total),
			status="Active" if auditor.user_id else "Invite pending",
		)
		for auditor, user_name, place_total, audit_total in auditor_rows
	]

	return ProjectDetailResponse(
		id=str(project.id),
		name=project.name,
		description=project.description or "No project summary has been added yet.",
		status="Planning" if project.start_date is None else "Active",
		organization=organization_name,
		place_types=project.place_types or [],
		start_date=project.start_date.isoformat() if project.start_date else None,
		end_date=project.end_date.isoformat() if project.end_date else None,
		estimated_places=project.est_places,
		auditor_population_types=auditor_population_types,
		auditor_inclusion_exclusion_criteria=auditor_inclusion_exclusion_criteria,
		auditor_notes=auditor_notes,
		total_places=len(places),
		total_audits=sum(place.audits for place in places),
		submitted_audits=submitted_audits,
		assigned_auditors=len(auditors),
		places=places,
		auditors=auditors,
		latest_audits=latest_audits,
	)


async def _fetch_place_detail(
	session: AsyncSession,
	user: User,
	place_id: uuid.UUID,
) -> PlaceDetailResponse:
	place, project = await _get_scoped_place(session, user, place_id)
	auditor_population_types, auditor_inclusion_exclusion_criteria, auditor_notes = _deserialize_auditor_profile(
		place.auditor_description
	)

	comparisons = await _service_fetch_place_comparison_groups(session, _project_scope_filter(user))
	comparison_group = next((group for group in comparisons if group.place_id == str(place.id)), None)
	if comparison_group is None:
		comparison_group = PlaceComparisonGroup(
			place_id=str(place.id),
			place_name=place.name,
			project_id=str(project.id),
			project_name=project.name,
			audits=[],
		)

	last_audit = func.max(Audit.submitted_at)
	audit_count = func.count(Audit.id)
	auditor_stmt = (
		select(Auditor, User.name, audit_count, last_audit)
		.join(Assignment, Assignment.auditor_profile_id == Auditor.id)
		.outerjoin(User, Auditor.user_id == User.id)
		.outerjoin(
			Audit,
			(Audit.auditor_profile_id == Auditor.id)
			& (Audit.place_id == place.id)
			& (Audit.status == AuditStatus.SUBMITTED),
		)
		.where(
			Assignment.project_id == project.id,
			or_(Assignment.place_id == place.id, Assignment.place_id.is_(None)),
		)
		.group_by(Auditor.id, User.name)
		.order_by(User.name.asc().nullslast(), Auditor.auditor_code.asc())
	)
	if user.account_type != AccountType.ADMIN:
		auditor_stmt = auditor_stmt.where(Auditor.id.in_(_manager_invited_auditor_ids_subquery(user)))
	auditor_rows = (await session.execute(auditor_stmt)).all()
	auditors = [
		PlaceAuditorItem(
			id=str(auditor.id),
			name=user_name or _display_auditor_code(auditor.auditor_code),
			auditor_id=_display_auditor_code(auditor.auditor_code),
			status="Active" if auditor.user_id else "Invite pending",
			audit_count=int(audit_total),
			last_audit=_format_timestamp(last_submitted_at),
		)
		for auditor, user_name, audit_total, last_submitted_at in auditor_rows
	]

	total_audits = await _count_rows(session, Audit, Audit.place_id == place.id)
	submitted_count = await _count_rows(
		session,
		Audit,
		(Audit.place_id == place.id) & (Audit.status == AuditStatus.SUBMITTED),
	)
	last_submitted_at = (
		await session.execute(
			select(func.max(Audit.submitted_at)).where(
				Audit.place_id == place.id,
				Audit.status == AuditStatus.SUBMITTED,
			)
		)
	).scalar_one()

	return PlaceDetailResponse(
		id=str(place.id),
		name=place.name,
		address=place.address or "",
		city=place.city or "",
		province=place.province or "",
		country=place.country or "",
		postal_code=place.postal_code,
		place_type=place.place_type or "",
		start_date=place.start_date.isoformat() if place.start_date else None,
		end_date=place.end_date.isoformat() if place.end_date else None,
		estimated_auditors=place.est_auditors,
		auditor_population_types=auditor_population_types,
		auditor_inclusion_exclusion_criteria=auditor_inclusion_exclusion_criteria,
		auditor_notes=auditor_notes,
		lat=place.lat,
		lng=place.lng,
		notes=auditor_notes or "No additional place notes have been added yet.",
		status="Needs review" if submitted_count == 0 else "Up to date",
		project_id=str(project.id),
		project_name=project.name,
		assigned_auditors=len(auditors),
		total_audits=total_audits,
		submitted_audits=submitted_count,
		last_audit=_format_timestamp(last_submitted_at),
		auditors=auditors,
		comparisons=comparison_group,
	)


async def _fetch_auditors(session: AsyncSession, user: User) -> list[AuditorListItem]:
	completed_audits = func.count(Audit.id)
	audit_join_condition = (Audit.auditor_profile_id == Auditor.id) & (Audit.status == AuditStatus.SUBMITTED)
	if user.account_type != AccountType.ADMIN:
		audit_join_condition = audit_join_condition & Audit.project_id.in_(_manager_project_ids_subquery(user))
	stmt = (
		select(Auditor, User.name, User.email, completed_audits)
		.outerjoin(User, Auditor.user_id == User.id)
		.outerjoin(Audit, audit_join_condition)
		.group_by(Auditor.id, User.name, User.email)
		.order_by(User.name.asc().nullslast(), Auditor.auditor_code.asc())
	)
	if user.account_type != AccountType.ADMIN:
		stmt = stmt.where(Auditor.id.in_(_manager_invited_auditor_ids_subquery(user)))
	rows = (await session.execute(stmt)).all()
	auditor_ids = [auditor.id for auditor, *_ in rows]
	assigned_places_by_auditor: dict[uuid.UUID, list[str]] = defaultdict(list)
	if auditor_ids:
		place_stmt = (
			select(Assignment.auditor_profile_id, Place.name)
			.join(ProjectPlace, ProjectPlace.project_id == Assignment.project_id)
			.join(
				Place,
				and_(
					Place.id == ProjectPlace.place_id,
					or_(Assignment.place_id.is_(None), Assignment.place_id == ProjectPlace.place_id),
				),
			)
			.join(Project, Project.id == Assignment.project_id)
			.where(Assignment.auditor_profile_id.in_(auditor_ids))
			.order_by(Place.name.asc())
		)
		if user.account_type != AccountType.ADMIN:
			place_stmt = place_stmt.where(Project.account_id == _manager_account_id(user))
		place_rows = (await session.execute(place_stmt)).all()
		for auditor_id, place_name in place_rows:
			if place_name not in assigned_places_by_auditor[auditor_id]:
				assigned_places_by_auditor[auditor_id].append(place_name)
	return [
		AuditorListItem(
			id=str(auditor.id),
			name=(
				_display_auditor_code(auditor.auditor_code)
				if user.account_type == AccountType.ADMIN
				else user_name or _display_auditor_code(auditor.auditor_code)
			),
			auditor_id=_display_auditor_code(auditor.auditor_code),
			email="" if user.account_type == AccountType.ADMIN else user_email or auditor.email or "",
			assigned_places=assigned_places_by_auditor.get(auditor.id, []),
			completed_audits=int(audit_total),
			status="Active" if auditor.user_id else "Invite pending",
		)
		for auditor, user_name, user_email, audit_total in rows
	]


async def _fetch_users(session: AsyncSession, current_user: User) -> list[UserListItem]:
	account_alias = aliased(Account)
	stmt = (
		select(User, account_alias.name, Auditor.auditor_code)
		.outerjoin(account_alias, account_alias.id == User.account_id)
		.outerjoin(Auditor, Auditor.user_id == User.id)
		.order_by(User.email.asc())
	)
	rows = (await session.execute(stmt)).all()

	manager_projects_rows = (
		await session.execute(
			select(User.id, Project.name)
			.join(Project, Project.account_id == User.account_id)
			.where(User.account_type == AccountType.MANAGER)
			.order_by(Project.name.asc())
		)
	).all()
	auditor_projects_rows = (
		await session.execute(
			select(User.id, Project.name)
			.join(Auditor, Auditor.user_id == User.id)
			.join(Assignment, Assignment.auditor_profile_id == Auditor.id)
			.join(Project, Project.id == Assignment.project_id)
			.where(User.account_type == AccountType.AUDITOR)
			.distinct()
			.order_by(Project.name.asc())
		)
	).all()
	project_names_by_user: dict[uuid.UUID, list[str]] = defaultdict(list)
	for user_id, project_name in [*manager_projects_rows, *auditor_projects_rows]:
		if project_name not in project_names_by_user[user_id]:
			project_names_by_user[user_id].append(project_name)

	return [
		UserListItem(
			id=str(user.id),
			name=(
				_display_auditor_code(auditor_code or "AUD-PENDING")
				if current_user.account_type == AccountType.ADMIN and user.account_type == AccountType.AUDITOR
				else user.name or user.email
			),
			email=""
			if current_user.account_type == AccountType.ADMIN and user.account_type == AccountType.AUDITOR
			else user.email,
			role=user.account_type.value,
			account_id=str(user.account_id) if user.account_id is not None else None,
			organization=account_name or "Unassigned",
			status=_status_for_user(user),
			approved=user.approved,
			email_verified=user.email_verified,
			profile_completed=user.profile_completed,
			contact_info="",
			project_assignments=", ".join(project_names_by_user.get(user.id, [])) or "None",
		)
		for user, account_name, auditor_code in rows
	]


async def _fetch_organization_summaries(session: AsyncSession) -> list[OrganizationSummaryItem]:
	account_rows = (await session.execute(select(Account).order_by(Account.name.asc()))).scalars().all()
	project_counts: dict[uuid.UUID, int] = {
		account_id: count
		for account_id, count in (
			await session.execute(select(Project.account_id, func.count(Project.id)).group_by(Project.account_id))
		).all()
	}
	place_counts: dict[uuid.UUID, int] = {
		account_id: count
		for account_id, count in (
			await session.execute(
				select(Project.account_id, func.count(func.distinct(ProjectPlace.place_id)))
				.join(ProjectPlace, ProjectPlace.project_id == Project.id)
				.group_by(Project.account_id)
			)
		).all()
	}
	audit_counts: dict[uuid.UUID, int] = {
		account_id: count
		for account_id, count in (
			await session.execute(
				select(Project.account_id, func.count(Audit.id))
				.join(Audit, Audit.project_id == Project.id)
				.where(Audit.status == AuditStatus.SUBMITTED)
				.group_by(Project.account_id)
			)
		).all()
	}
	user_counts: dict[uuid.UUID, int] = {
		account_id: count
		for account_id, count in (
			await session.execute(
				select(User.account_id, func.count(User.id))
				.where(User.account_id.is_not(None))
				.group_by(User.account_id)
			)
		).all()
		if account_id is not None
	}
	return [
		OrganizationSummaryItem(
			organization=account.name,
			users=int(user_counts.get(account.id, 0)),
			projects=int(project_counts.get(account.id, 0)),
			places=int(place_counts.get(account.id, 0)),
			audits=int(audit_counts.get(account.id, 0)),
		)
		for account in account_rows
	]


def _normalize_email(email: str) -> str:
	return email.strip().lower()


def _derive_manager_invite_status(invite: ManagerInvite) -> str:
	if invite.accepted_at is not None:
		return "ACCEPTED"
	if datetime.now(timezone.utc) > invite.expires_at:
		return "EXPIRED"
	return "PENDING"


def _serialize_manager_invite(invite: ManagerInvite) -> ManagerInviteResponse:
	return ManagerInviteResponse(
		id=str(invite.id),
		email=invite.email,
		status=_derive_manager_invite_status(invite),
		expires_at=invite.expires_at,
		created_at=invite.created_at,
		accepted_at=invite.accepted_at,
	)


async def _generate_unique_auditor_code(session: AsyncSession) -> str:
	existing_codes = (await session.execute(select(Auditor.auditor_code))).scalars().all()
	max_suffix = 0
	for existing_code in existing_codes:
		match = re.search(r"(\d+)$", existing_code or "")
		if match is not None:
			max_suffix = max(max_suffix, int(match.group(1)))
	return f"AUD{max_suffix + 1:03d}"


async def _get_current_auditor(session: AsyncSession, user: User) -> Auditor:
	result = await session.execute(select(Auditor).where(Auditor.user_id == user.id))
	auditor = result.scalar_one_or_none()
	if auditor is None:
		raise HTTPException(status_code=404, detail="Auditor profile not found.")
	return auditor


async def _require_primary_manager(session: AsyncSession, user: User) -> uuid.UUID:
	if user.account_type != AccountType.MANAGER:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="Only managers can manage manager invites.",
		)
	if user.account_id is None:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="Manager account scope is required.",
		)

	profile_result = await session.execute(
		select(ManagerProfile).where(
			ManagerProfile.user_id == user.id,
			ManagerProfile.account_id == user.account_id,
		)
	)
	profile = profile_result.scalar_one_or_none()
	if profile is None or not profile.is_primary:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="Only the primary manager can manage manager invites.",
		)

	return user.account_id


def _manager_type_label(profile: ManagerProfile) -> str:
	return "Primary" if profile.is_primary else "Secondary"


async def _count_secondary_manager_slots(session: AsyncSession, account_id: uuid.UUID) -> int:
	"""Count accepted or pending secondary manager slots for one organization."""

	result = await session.execute(
		select(func.count(ManagerProfile.id)).where(
			ManagerProfile.account_id == account_id,
			ManagerProfile.is_primary.is_(False),
		)
	)
	return int(result.scalar_one() or 0)


async def _serialize_manager_profile_response(
	session: AsyncSession,
	profile: ManagerProfile,
) -> ManagerProfileResponse:
	user = await session.get(User, profile.user_id) if profile.user_id is not None else None
	return ManagerProfileResponse(
		id=str(profile.id),
		full_name=profile.full_name,
		email=profile.email,
		job_title=profile.position,
		profession_disciplines=list(profile.profession_disciplines or []),
		organization=profile.organization,
		phone_number=profile.phone,
		manager_type=_manager_type_label(profile),
		date_joined=profile.created_at,
		account_creation_date=user.created_at if user is not None else None,
		profile_completed=user.profile_completed if user is not None else False,
	)


@router.get("/overview", response_model=DashboardOverviewResponse)
async def get_dashboard_overview(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> DashboardOverviewResponse:
	"""Return overview metrics and recent audit activity for dashboard landing pages."""

	_require_manager_or_admin(user)
	if user.account_type == AccountType.ADMIN:
		projects_count = await _count_rows(session, Project)
		places_count = await _count_rows(session, Place)
		auditors_count = await _count_rows(session, Auditor)
		completed_audits = await _count_rows(session, Audit, Audit.status == AuditStatus.SUBMITTED)
	else:
		owned_project_ids = _manager_project_ids_subquery(user)
		invited_auditor_ids = _manager_invited_auditor_ids_subquery(user)
		projects_count = await _count_rows(session, Project, Project.account_id == _manager_account_id(user))
		places_count = int(
			(
				await session.execute(
					select(func.count(func.distinct(ProjectPlace.place_id)))
					.join(Project, ProjectPlace.project_id == Project.id)
					.where(Project.account_id == _manager_account_id(user))
				)
			).scalar_one()
		)
		auditors_count = int(
			(
				await session.execute(
					select(func.count(func.distinct(Auditor.id))).where(Auditor.id.in_(invited_auditor_ids))
				)
			).scalar_one()
		)
		completed_audits = await _count_rows(
			session,
			Audit,
			(Audit.project_id.in_(owned_project_ids)) & (Audit.status == AuditStatus.SUBMITTED),
		)

	latest_audits = await _fetch_audits(session, user, limit=6)
	recent_activity = [
		f"{audit.place} was submitted by {audit.auditor} on {audit.date}." for audit in latest_audits[:3]
	]
	if not recent_activity:
		recent_activity = [
			"No audit submissions are available yet.",
			"Create projects, places, and auditor assignments to start collecting fieldwork.",
		]

	return DashboardOverviewResponse(
		metrics=[
			DashboardMetricResponse(
				title="Projects",
				value=f"{projects_count:02d}",
				description="Projects currently stored in the backend.",
				trend="Live backend data",
			),
			DashboardMetricResponse(
				title="Places",
				value=f"{places_count:02d}",
				description="Places currently available for assignment and review.",
				trend="Live backend data",
			),
			DashboardMetricResponse(
				title="Auditors",
				value=f"{auditors_count:02d}",
				description="Auditor profiles in the current database.",
				trend="Live backend data",
			),
			DashboardMetricResponse(
				title="Completed Audits",
				value=f"{completed_audits:02d}",
				description="Submitted audits currently available for reporting.",
				trend="Live backend data",
			),
		],
		recent_activity=recent_activity,
		latest_audits=latest_audits,
		organization_summaries=(
			[] if user.account_type != AccountType.ADMIN else await _fetch_organization_summaries(session)
		),
	)


@router.get("/projects", response_model=list[ProjectListItem])
async def list_projects(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[ProjectListItem]:
	_require_manager_or_admin(user)
	return await _fetch_projects(session, user)


@router.get("/projects/{project_id}", response_model=ProjectDetailResponse)
async def get_project_detail(
	project_id: uuid.UUID,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> ProjectDetailResponse:
	_require_manager_or_admin(user)
	return await _fetch_project_detail(session, user, project_id)


@router.get("/places", response_model=list[PlaceListItem])
async def list_places(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[PlaceListItem]:
	_require_manager_or_admin(user)
	return await _fetch_places(session, user)


@router.get("/places/{place_id}", response_model=PlaceDetailResponse)
async def get_place_detail(
	place_id: uuid.UUID,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> PlaceDetailResponse:
	_require_manager_or_admin(user)
	return await _fetch_place_detail(session, user, place_id)


@router.get("/auditors", response_model=list[AuditorListItem])
async def list_auditors(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[AuditorListItem]:
	_require_manager_or_admin(user)
	return await _fetch_auditors(session, user)


@router.get("/audits", response_model=list[AuditListItem])
async def list_audits(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[AuditListItem]:
	_require_manager_or_admin(user)
	return await _fetch_audits(session, user)


@router.get("/audits/{audit_id}/edit", response_model=ManagerAuditEditState)
async def get_manager_audit_edit_state(
	audit_id: uuid.UUID,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> ManagerAuditEditState:
	_require_manager_or_admin(user)
	is_admin = user.account_type == AccountType.ADMIN
	return await _service_fetch_manager_audit_edit_state(
		session,
		audit_id,
		is_admin=is_admin,
		manager_account_id=None if is_admin else _manager_account_id(user),
	)


@router.patch("/audits/{audit_id}/edit", response_model=ManagerAuditEditState)
async def update_manager_audit_edit_state(
	audit_id: uuid.UUID,
	payload: ManagerAuditEditRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> ManagerAuditEditState:
	_require_manager_or_admin(user)
	is_admin = user.account_type == AccountType.ADMIN
	return await _service_update_manager_audit_edit_state(
		session,
		audit_id,
		payload,
		is_admin=is_admin,
		manager_account_id=None if is_admin else _manager_account_id(user),
	)


@router.get("/users", response_model=list[UserListItem])
async def list_users(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[UserListItem]:
	_require_admin(user)
	return await _fetch_users(session, user)


@router.get("/manager-profile", response_model=ManagerProfileResponse)
async def get_manager_profile(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> ManagerProfileResponse:
	"""Return the current manager's profile and recorded metadata."""

	if user.account_type != AccountType.MANAGER:
		raise HTTPException(status_code=403, detail="Manager access is required.")
	profile = await _get_manager_profile_for_user(session=session, user=user)
	if profile is None:
		raise HTTPException(status_code=404, detail="Manager profile not found.")
	return await _serialize_manager_profile_response(session, profile)


@router.put("/manager-profile", response_model=ManagerProfileResponse)
async def update_manager_profile(
	payload: UpdateManagerProfileRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> ManagerProfileResponse:
	"""Create or update the current manager profile and mark onboarding complete."""

	if user.account_type != AccountType.MANAGER:
		raise HTTPException(status_code=403, detail="Manager access is required.")
	clean_name = _clean_name(payload.full_name)
	job_title = _clean_name(payload.job_title)
	organization_name = _clean_name(payload.organization)
	phone_number = _clean_name(payload.phone_number)
	profession_disciplines = _normalize_text_list(payload.profession_disciplines)
	if clean_name is None:
		raise HTTPException(status_code=400, detail="Full name is required.")
	if job_title is None:
		raise HTTPException(status_code=400, detail="Job title / role is required.")
	if organization_name is None:
		raise HTTPException(status_code=400, detail="Organization name is required.")
	if not profession_disciplines:
		raise HTTPException(status_code=400, detail="Profession / discipline is required.")

	profile = await _get_manager_profile_for_user(session=session, user=user)
	if profile is None:
		raise HTTPException(status_code=404, detail="Manager profile not found.")
	if profile.is_primary and phone_number is None:
		raise HTTPException(status_code=400, detail="Phone number is required for the primary manager.")
	if user.account is not None and profile.is_primary:
		user.account.name = organization_name
	elif user.account is not None and organization_name != user.account.name:
		raise HTTPException(status_code=400, detail="Secondary managers cannot change the organization name.")

	user.name = clean_name
	user.profile_completed = True
	user.profile_completed_at = datetime.now(timezone.utc)
	profile.full_name = clean_name
	profile.position = job_title
	profile.profession_disciplines = profession_disciplines
	profile.organization = organization_name
	profile.phone = phone_number
	profile.email = user.email
	await session.commit()
	await session.refresh(profile)
	return await _serialize_manager_profile_response(session, profile)


@router.get("/managers", response_model=list[ManagerTeamMemberResponse])
async def list_managers(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[ManagerTeamMemberResponse]:
	"""Return the current organization's management team."""

	if user.account_type != AccountType.MANAGER:
		raise HTTPException(status_code=403, detail="Manager access is required.")
	account_id = _manager_account_id(user)
	if account_id is None:
		raise HTTPException(status_code=403, detail="Manager account scope is required.")
	result = await session.execute(
		select(ManagerProfile).where(ManagerProfile.account_id == account_id).order_by(ManagerProfile.is_primary.desc())
	)
	profiles = result.scalars().all()
	items: list[ManagerTeamMemberResponse] = []
	for profile in profiles:
		serialized = await _serialize_manager_profile_response(session, profile)
		items.append(ManagerTeamMemberResponse(**serialized.model_dump()))
	return items


@router.delete("/managers/{manager_profile_id}", status_code=204, response_model=None, response_class=Response)
async def remove_manager_from_organization(
	manager_profile_id: uuid.UUID,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> None:
	"""Remove one secondary manager from the current organization."""

	account_id = await _require_primary_manager(session, user)
	profile = await session.get(ManagerProfile, manager_profile_id)
	if profile is None or profile.account_id != account_id:
		raise HTTPException(status_code=404, detail="Manager not found.")
	if profile.is_primary:
		raise HTTPException(status_code=400, detail="The primary manager cannot be removed from the organization.")

	target_user = await session.get(User, profile.user_id) if profile.user_id is not None else None
	invites = (
		await session.execute(
			select(ManagerInvite).where(
				ManagerInvite.account_id == account_id,
				ManagerInvite.email == profile.email,
			)
		)
	).scalars()
	for invite in invites:
		await session.delete(invite)
	if target_user is not None:
		target_user.account_id = None
		target_user.profile_completed = False
		target_user.profile_completed_at = None
	await session.delete(profile)
	await session.commit()


@router.post("/my-auditor-profile", response_model=CreateSelfAuditorProfileResponse, status_code=201)
async def create_self_auditor_profile(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> CreateSelfAuditorProfileResponse:
	"""Allow a manager to create an auditor profile for themselves in the same organization."""

	if user.account_type != AccountType.MANAGER:
		raise HTTPException(status_code=403, detail="Manager access is required.")
	account_id = _manager_account_id(user)
	if account_id is None:
		raise HTTPException(status_code=403, detail="Manager account scope is required.")

	existing = (await session.execute(select(Auditor).where(Auditor.user_id == user.id))).scalar_one_or_none()
	if existing is None:
		manager_profile = await _get_manager_profile_for_user(session=session, user=user)
		display_name = (
			manager_profile.full_name
			if manager_profile is not None and manager_profile.full_name
			else user.name or user.email
		)
		existing = Auditor(
			account_id=account_id,
			user_id=user.id,
			auditor_code=await _generate_unique_auditor_code(session),
			email=user.email,
			full_name=display_name,
		)
		session.add(existing)
		await session.commit()
		await session.refresh(existing)

	return CreateSelfAuditorProfileResponse(
		id=str(existing.id),
		auditor_id=_display_auditor_code(existing.auditor_code),
		email=existing.email,
		full_name=existing.full_name,
		account_id=str(existing.account_id),
	)


@router.post("/users/approve", response_model=UserListItem)
async def approve_user(
	payload: ApproveUserRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> UserListItem:
	_require_admin(user)

	target_user = await session.get(User, payload.user_id)
	if target_user is None:
		raise HTTPException(status_code=404, detail="User not found.")

	target_account = None
	if target_user.account_type == AccountType.AUDITOR:
		account_id = payload.account_id or target_user.account_id
		if account_id is None:
			raise HTTPException(status_code=400, detail="An account is required to approve this auditor.")
		target_account = await session.get(Account, account_id)
		if target_account is None:
			raise HTTPException(status_code=404, detail="Account not found.")
		target_user.account_id = target_account.id

		auditor_result = await session.execute(select(Auditor).where(Auditor.user_id == target_user.id))
		auditor = auditor_result.scalar_one_or_none()
		if auditor is None:
			auditor = Auditor(
				account_id=target_account.id,
				auditor_code=await _generate_unique_auditor_code(session),
				user_id=target_user.id,
			)
			session.add(auditor)
		else:
			auditor.account_id = target_account.id
	elif target_user.account_type == AccountType.MANAGER:
		account_id = payload.account_id or target_user.account_id
		if account_id is None:
			raise HTTPException(status_code=400, detail="An organization is required to approve this manager.")
		target_account = await session.get(Account, account_id)
		if target_account is None:
			raise HTTPException(status_code=404, detail="Account not found.")
		target_user.account_id = target_account.id
		await _ensure_manager_profile_for_user(
			session=session,
			user=target_user,
			email=target_user.email,
			clean_name=_clean_name(target_user.name),
			prefer_primary=True,
		)

	target_user.approved = True
	target_user.approved_at = datetime.now(timezone.utc)
	await session.commit()

	account_name = "Unassigned"
	if target_user.account_id is not None:
		account = target_account or await session.get(Account, target_user.account_id)
		if account is not None:
			account_name = account.name

	return UserListItem(
		id=str(target_user.id),
		name=target_user.name or target_user.email,
		email=target_user.email,
		role=target_user.account_type.value,
		account_id=str(target_user.account_id) if target_user.account_id is not None else None,
		organization=account_name,
		status=_status_for_user(target_user),
		approved=target_user.approved,
		email_verified=target_user.email_verified,
		profile_completed=target_user.profile_completed,
		contact_info=target_user.email if target_user.account_type == AccountType.MANAGER else "",
		project_assignments="None",
	)


@router.get("/reports/place-comparisons", response_model=list[PlaceComparisonGroup])
async def list_place_comparisons(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[PlaceComparisonGroup]:
	_require_manager_or_admin(user)
	return await _service_fetch_place_comparison_groups(session, _project_scope_filter(user))


@router.get("/raw-data", response_model=list[RawDataExportRow])
async def list_raw_data(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[RawDataExportRow]:
	_require_manager_or_admin(user)
	return await _service_fetch_raw_data_rows(session, _project_scope_filter(user))


@router.post("/projects", response_model=ProjectListItem)
async def create_project(
	payload: CreateProjectRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> ProjectListItem:
	_require_manager_or_admin(user)
	account_id = _manager_account_id(user)
	if account_id is None:
		raise HTTPException(status_code=403, detail="Admin project creation is not supported from this route.")

	project = Project(
		account_id=account_id,
		created_by_user_id=user.id,
		name=payload.name.strip(),
		overview=payload.description.strip() if payload.description and payload.description.strip() else None,
		place_types=_normalize_text_list(payload.place_types),
		start_date=payload.start_date,
		end_date=payload.end_date,
		est_places=payload.estimated_places,
		auditor_description=_serialize_auditor_profile(
			payload.auditor_population_types,
			payload.auditor_inclusion_exclusion_criteria,
			payload.auditor_notes,
		),
	)
	session.add(project)
	await session.commit()

	return ProjectListItem(
		id=str(project.id),
		name=project.name,
		summary=project.description or "Project summary pending",
		organization=None,
		places=0,
		audits=0,
		status="Planning",
	)


@router.patch("/projects/{project_id}", response_model=ProjectListItem)
async def update_project(
	project_id: uuid.UUID,
	payload: UpdateProjectRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> ProjectListItem:
	_require_manager_or_admin(user)
	account_id = _manager_account_id(user)
	if account_id is None:
		raise HTTPException(status_code=403, detail="Admin project editing is not supported from this route.")

	project = await session.get(Project, project_id)
	if project is None:
		raise HTTPException(status_code=404, detail="Project not found.")
	if project.account_id != account_id:
		raise HTTPException(status_code=403, detail="Project is outside your manager scope.")

	project.name = payload.name.strip()
	project.description = payload.description.strip() if payload.description and payload.description.strip() else None
	project.place_types = _normalize_text_list(payload.place_types)
	project.start_date = payload.start_date
	project.end_date = payload.end_date
	project.est_places = payload.estimated_places
	project.auditor_description = _serialize_auditor_profile(
		payload.auditor_population_types,
		payload.auditor_inclusion_exclusion_criteria,
		payload.auditor_notes,
	)
	await session.commit()

	place_total = int(
		(
			await session.execute(
				select(func.count(func.distinct(ProjectPlace.place_id))).where(ProjectPlace.project_id == project.id)
			)
		).scalar_one()
	)
	audit_total = await _count_rows(session, Audit, Audit.project_id == project.id)
	account = await session.get(Account, project.account_id)

	return ProjectListItem(
		id=str(project.id),
		name=project.name,
		summary=project.description or "Project summary pending",
		organization=account.name if account is not None else None,
		places=place_total,
		audits=audit_total,
		status="Planning" if project.start_date is None else "Active",
	)


@router.post("/places", response_model=PlaceListItem)
async def create_place(
	payload: CreatePlaceRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> PlaceListItem:
	_require_manager_or_admin(user)
	account_id = _manager_account_id(user)
	if account_id is None:
		raise HTTPException(status_code=403, detail="Admin place creation is not supported from this route.")

	project = await session.get(Project, payload.project_id)
	if project is None:
		raise HTTPException(status_code=404, detail="Project not found.")
	if project.account_id != account_id:
		raise HTTPException(status_code=403, detail="Project is outside your manager scope.")

	place = Place(
		name=payload.name.strip(),
		address=payload.address.strip(),
		city=payload.city.strip() if payload.city and payload.city.strip() else None,
		province=payload.province.strip() if payload.province and payload.province.strip() else None,
		country=payload.country.strip() if payload.country and payload.country.strip() else None,
		postal_code=payload.postal_code.strip() if payload.postal_code and payload.postal_code.strip() else None,
		place_type=payload.place_type.strip() if payload.place_type and payload.place_type.strip() else None,
		start_date=payload.start_date,
		end_date=payload.end_date,
		est_auditors=payload.estimated_auditors,
		auditor_description=_serialize_auditor_profile(
			payload.auditor_population_types,
			payload.auditor_inclusion_exclusion_criteria,
			payload.auditor_notes,
		),
		lat=payload.lat,
		lng=payload.lng,
	)
	session.add(place)
	await session.flush()
	session.add(ProjectPlace(project_id=project.id, place_id=place.id))
	await session.commit()
	account = await session.get(Account, project.account_id)

	return PlaceListItem(
		id=str(place.id),
		name=place.name,
		project_id=str(project.id),
		project=project.name,
		organization=account.name if account is not None else None,
		address=place.address or "",
		postal_code=place.postal_code,
		audits=0,
		last_audit="Not yet",
		status="Needs review",
	)


@router.patch("/places/{place_id}", response_model=PlaceListItem)
async def update_place(
	place_id: uuid.UUID,
	payload: UpdatePlaceRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> PlaceListItem:
	_require_manager_or_admin(user)
	account_id = _manager_account_id(user)
	if account_id is None:
		raise HTTPException(status_code=403, detail="Admin place editing is not supported from this route.")

	place = await session.get(Place, place_id)
	if place is None:
		raise HTTPException(status_code=404, detail="Place not found.")

	current_link = (
		await session.execute(select(ProjectPlace).where(ProjectPlace.place_id == place.id))
	).scalar_one_or_none()
	if current_link is None:
		raise HTTPException(status_code=404, detail="Place project link not found.")

	current_project = await session.get(Project, current_link.project_id)
	target_project = await session.get(Project, payload.project_id)
	if current_project is None or target_project is None:
		raise HTTPException(status_code=404, detail="Project not found.")
	if current_project.account_id != account_id:
		raise HTTPException(status_code=403, detail="Place is outside your manager scope.")
	if target_project.account_id != account_id:
		raise HTTPException(status_code=403, detail="Target project is outside your manager scope.")

	place.name = payload.name.strip()
	place.address = payload.address.strip()
	place.city = payload.city.strip() if payload.city and payload.city.strip() else None
	place.province = payload.province.strip() if payload.province and payload.province.strip() else None
	place.country = payload.country.strip() if payload.country and payload.country.strip() else None
	place.postal_code = payload.postal_code.strip() if payload.postal_code and payload.postal_code.strip() else None
	place.place_type = payload.place_type.strip() if payload.place_type and payload.place_type.strip() else None
	place.start_date = payload.start_date
	place.end_date = payload.end_date
	place.est_auditors = payload.estimated_auditors
	place.auditor_description = _serialize_auditor_profile(
		payload.auditor_population_types,
		payload.auditor_inclusion_exclusion_criteria,
		payload.auditor_notes,
	)
	place.lat = payload.lat
	place.lng = payload.lng
	if current_link.project_id != target_project.id:
		current_link.project_id = target_project.id
	await session.commit()

	audit_total = await _count_rows(session, Audit, Audit.place_id == place.id)
	last_submitted_at = (
		await session.execute(select(func.max(Audit.submitted_at)).where(Audit.place_id == place.id))
	).scalar_one()
	assigned_auditors_stmt = (
		select(Auditor.auditor_code)
		.join(Assignment, Assignment.auditor_profile_id == Auditor.id)
		.where(
			Assignment.project_id == target_project.id,
			or_(Assignment.place_id.is_(None), Assignment.place_id == place.id),
		)
		.order_by(Auditor.auditor_code.asc())
	)
	if user.account_type != AccountType.ADMIN:
		assigned_auditors_stmt = assigned_auditors_stmt.where(
			Auditor.id.in_(_manager_invited_auditor_ids_subquery(user))
		)
	assigned_auditor_codes = (await session.execute(assigned_auditors_stmt)).scalars().all()
	account = await session.get(Account, target_project.account_id)

	return PlaceListItem(
		id=str(place.id),
		name=place.name,
		project_id=str(target_project.id),
		project=target_project.name,
		organization=account.name if account is not None else None,
		address=place.address,
		postal_code=place.postal_code,
		assigned_auditors=[_display_auditor_code(code) for code in assigned_auditor_codes],
		audits=audit_total,
		last_audit=_format_timestamp(last_submitted_at),
		status="Needs review" if audit_total == 0 else "Up to date",
	)


@router.post("/auditor-invites", response_model=AuditorInviteResponse)
async def create_auditor_invite(
	payload: CreateAuditorInviteRequest,
	request: FastAPIRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> AuditorInviteResponse:
	_require_manager_or_admin(user)
	account_id = _manager_account_id(user)
	if account_id is None:
		raise HTTPException(status_code=403, detail="Admin invites are not supported from this route.")

	email = _normalize_email(payload.email)
	if not email:
		raise HTTPException(status_code=400, detail="Email is required.")

	token = generate_email_verification_token()
	invite = AuditorInvite(
		account_id=account_id,
		invited_by_user_id=user.id,
		email=email,
		token_hash=hash_verification_token(token),
		expires_at=datetime.now(timezone.utc) + timedelta(days=7),
	)
	session.add(invite)
	await session.flush()

	invite_url = _build_invite_url(request=request, token=token)
	send_auditor_invite_email(to_email=email, invite_url=invite_url)
	await session.commit()

	return AuditorInviteResponse(
		id=str(invite.id),
		email=invite.email,
		status="Pending acceptance",
		expires_at=invite.expires_at,
		invite_url=invite_url,
	)


@router.post("/manager-invites", response_model=ManagerInviteResponse, status_code=201)
async def create_manager_invite(
	payload: CreateManagerInviteRequest,
	request: FastAPIRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> ManagerInviteResponse:
	account_id = await _require_primary_manager(session, user)

	email = _normalize_email(payload.email)
	full_name = _clean_name(payload.full_name)
	if not email:
		raise HTTPException(status_code=400, detail="Email is required.")
	if full_name is None:
		raise HTTPException(status_code=400, detail="Full name is required.")
	if email == _normalize_email(user.email):
		raise HTTPException(status_code=409, detail="Use your existing credentials for this account.")

	existing_user_result = await session.execute(select(User).where(User.email == email))
	existing_user = existing_user_result.scalar_one_or_none()
	if existing_user is not None:
		if existing_user.account_type != AccountType.MANAGER:
			raise HTTPException(
				status_code=409,
				detail="This email is already used by a non-manager account.",
			)
		if existing_user.account_id == account_id:
			raise HTTPException(
				status_code=409,
				detail="This manager already has account access.",
			)
		raise HTTPException(
			status_code=409,
			detail="This email is already linked to another manager account.",
		)

	existing_profile_result = await session.execute(select(ManagerProfile).where(ManagerProfile.email == email))
	existing_profile = existing_profile_result.scalar_one_or_none()
	if existing_profile is not None:
		if existing_profile.account_id != account_id:
			raise HTTPException(
				status_code=409,
				detail="This email is already linked to another manager account.",
			)
		if existing_profile.user_id is not None:
			raise HTTPException(
				status_code=409,
				detail="This manager already has account access.",
			)

	secondary_slots = await _count_secondary_manager_slots(session, account_id)
	if existing_profile is None and secondary_slots >= 5:
		raise HTTPException(
			status_code=409,
			detail="A primary manager can invite up to 5 additional managers.",
		)

	now = datetime.now(timezone.utc)
	existing_invite_result = await session.execute(
		select(ManagerInvite)
		.where(
			ManagerInvite.account_id == account_id,
			ManagerInvite.email == email,
			ManagerInvite.accepted_at.is_(None),
		)
		.order_by(ManagerInvite.created_at.desc())
		.limit(1)
	)
	existing_invite = existing_invite_result.scalar_one_or_none()
	if existing_invite is not None and now <= existing_invite.expires_at:
		raise HTTPException(
			status_code=409,
			detail="An active manager invite already exists for this email.",
		)

	account = await session.get(Account, account_id)
	if existing_profile is None:
		session.add(
			ManagerProfile(
				account_id=account_id,
				user_id=None,
				full_name=full_name,
				email=email,
				phone=None,
				position=None,
				profession_disciplines=[],
				organization=account.name if account is not None else None,
				is_primary=False,
			)
		)
	else:
		existing_profile.full_name = full_name

	token = generate_email_verification_token()
	invite = ManagerInvite(
		account_id=account_id,
		invited_by_user_id=user.id,
		email=email,
		token_hash=hash_verification_token(token),
		expires_at=now + timedelta(days=7),
	)
	session.add(invite)
	await session.flush()

	invited_by_name = user.name
	profile_result = await session.execute(
		select(ManagerProfile).where(
			ManagerProfile.user_id == user.id,
			ManagerProfile.account_id == account_id,
		)
	)
	profile = profile_result.scalar_one_or_none()
	if profile is not None and profile.full_name:
		invited_by_name = profile.full_name

	invite_url = _build_manager_invite_url(request=request, token=token)
	send_manager_invite_email(
		to_email=email,
		invite_url=invite_url,
		organization_name=account.name if account is not None else None,
		invited_by_name=invited_by_name,
	)
	await session.commit()
	await session.refresh(invite)

	return ManagerInviteResponse(
		id=str(invite.id),
		email=invite.email,
		status="PENDING",
		expires_at=invite.expires_at,
		invite_url=invite_url,
		created_at=invite.created_at,
		accepted_at=invite.accepted_at,
	)


@router.get("/manager-invites", response_model=list[ManagerInviteResponse])
async def list_manager_invites(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[ManagerInviteResponse]:
	account_id = await _require_primary_manager(session, user)
	result = await session.execute(
		select(ManagerInvite).where(ManagerInvite.account_id == account_id).order_by(ManagerInvite.created_at.desc())
	)
	return [_serialize_manager_invite(invite) for invite in result.scalars().all()]


@router.delete(
	"/manager-invites/{invite_id}",
	status_code=204,
	response_model=None,
	response_class=Response,
)
async def revoke_manager_invite(
	invite_id: uuid.UUID,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> None:
	account_id = await _require_primary_manager(session, user)
	result = await session.execute(
		select(ManagerInvite).where(
			ManagerInvite.id == invite_id,
			ManagerInvite.account_id == account_id,
		)
	)
	invite = result.scalar_one_or_none()
	if invite is None:
		raise HTTPException(status_code=404, detail="Invite not found.")
	if invite.accepted_at is not None:
		raise HTTPException(
			status_code=400,
			detail="Cannot revoke an invite that has already been accepted.",
		)
	placeholder_profile = (
		await session.execute(
			select(ManagerProfile).where(
				ManagerProfile.account_id == account_id,
				ManagerProfile.email == invite.email,
				ManagerProfile.user_id.is_(None),
				ManagerProfile.is_primary.is_(False),
			)
		)
	).scalar_one_or_none()
	if placeholder_profile is not None:
		await session.delete(placeholder_profile)
	await session.delete(invite)
	await session.commit()


@router.post("/manager-invites/{invite_id}/resend", response_model=ManagerInviteResponse)
async def resend_manager_invite(
	invite_id: uuid.UUID,
	request: FastAPIRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> ManagerInviteResponse:
	account_id = await _require_primary_manager(session, user)
	result = await session.execute(
		select(ManagerInvite).where(
			ManagerInvite.id == invite_id,
			ManagerInvite.account_id == account_id,
		)
	)
	invite = result.scalar_one_or_none()
	if invite is None:
		raise HTTPException(status_code=404, detail="Invite not found.")
	if invite.accepted_at is not None:
		raise HTTPException(
			status_code=400,
			detail="Cannot resend an invite that has already been accepted.",
		)

	token = generate_email_verification_token()
	invite.token_hash = hash_verification_token(token)
	invite.expires_at = datetime.now(timezone.utc) + timedelta(days=7)
	await session.flush()

	account = await session.get(Account, account_id)
	invited_by_name = user.name
	profile_result = await session.execute(
		select(ManagerProfile).where(
			ManagerProfile.user_id == user.id,
			ManagerProfile.account_id == account_id,
		)
	)
	profile = profile_result.scalar_one_or_none()
	if profile is not None and profile.full_name:
		invited_by_name = profile.full_name

	send_manager_invite_email(
		to_email=invite.email,
		invite_url=_build_manager_invite_url(request=request, token=token),
		organization_name=account.name if account is not None else None,
		invited_by_name=invited_by_name,
	)
	await session.commit()
	await session.refresh(invite)
	return _serialize_manager_invite(invite)


@router.post("/assignments", response_model=AssignmentResponse)
async def create_assignment(
	payload: CreateAssignmentRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> AssignmentResponse:
	_require_manager_or_admin(user)
	account_id = _manager_account_id(user)
	if account_id is None:
		raise HTTPException(status_code=403, detail="Admin assignments are not supported from this route.")

	project = await session.get(Project, payload.project_id)
	if project is None or project.account_id != account_id:
		raise HTTPException(status_code=404, detail="Project not found in your manager scope.")

	auditors = (
		(
			await session.execute(
				select(Auditor)
				.where(
					Auditor.id.in_(payload.auditor_ids),
					Auditor.account_id == account_id,
				)
				.distinct()
			)
		)
		.scalars()
		.all()
	)
	if len(auditors) != len(set(payload.auditor_ids)):
		raise HTTPException(
			status_code=404,
			detail="One or more auditors were not found in your account scope.",
		)

	places = (
		(
			await session.execute(
				select(Place)
				.join(ProjectPlace, ProjectPlace.place_id == Place.id)
				.where(ProjectPlace.project_id == project.id, Place.id.in_(payload.place_ids))
			)
		)
		.scalars()
		.all()
	)
	if len(places) != len(set(payload.place_ids)):
		raise HTTPException(status_code=404, detail="One or more places were not found in the selected project.")

	place_ids = {place.id for place in places}
	existing_assignments = (
		(
			await session.execute(
				select(Assignment).where(
					Assignment.project_id == project.id,
					Assignment.place_id.in_(place_ids),
					Assignment.auditor_profile_id.in_([auditor.id for auditor in auditors]),
				)
			)
		)
		.scalars()
		.all()
	)
	existing_keys = {(assignment.auditor_profile_id, assignment.place_id) for assignment in existing_assignments}

	created_assignments: list[Assignment] = []
	existing_count = 0
	for auditor in auditors:
		for place in places:
			key = (auditor.id, place.id)
			if key in existing_keys:
				existing_count += 1
				continue
			assignment = Assignment(auditor_profile_id=auditor.id, project_id=project.id, place_id=place.id)
			session.add(assignment)
			created_assignments.append(assignment)

	await session.commit()
	for assignment in created_assignments:
		await session.refresh(assignment)

	return AssignmentResponse(
		created_count=len(created_assignments),
		existing_count=existing_count,
		assignments=[
			AssignmentResultItem(
				id=str(assignment.id),
				auditor_id=str(assignment.auditor_id),
				place_id=str(assignment.place_id),
				project_id=str(assignment.project_id),
			)
			for assignment in [*existing_assignments, *created_assignments]
		],
	)


@router.delete("/assignments", response_model=DeleteAssignmentResponse)
async def delete_assignment(
	payload: DeleteAssignmentRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> DeleteAssignmentResponse:
	_require_manager_or_admin(user)
	account_id = _manager_account_id(user)
	if account_id is None:
		raise HTTPException(status_code=403, detail="Admin assignments are not supported from this route.")

	project = await session.get(Project, payload.project_id)
	if project is None or project.account_id != account_id:
		raise HTTPException(status_code=404, detail="Project not found in your manager scope.")

	auditor = await session.get(Auditor, payload.auditor_id)
	if auditor is None or auditor.account_id != account_id:
		raise HTTPException(status_code=404, detail="Auditor not found in your account scope.")

	stmt = select(Assignment).where(
		Assignment.project_id == project.id,
		Assignment.auditor_profile_id == auditor.id,
	)
	if payload.place_id is not None:
		place = await session.get(Place, payload.place_id)
		if place is None:
			raise HTTPException(status_code=404, detail="Place not found.")
		place_link = (
			await session.execute(
				select(ProjectPlace).where(
					ProjectPlace.project_id == project.id,
					ProjectPlace.place_id == payload.place_id,
				)
			)
		).scalar_one_or_none()
		if place_link is None:
			raise HTTPException(status_code=404, detail="Place not found in the selected project.")
		stmt = stmt.where(Assignment.place_id == payload.place_id)

	assignments = list((await session.execute(stmt)).scalars().all())
	if not assignments:
		raise HTTPException(status_code=404, detail="Assignment not found.")

	deleted_count = len(assignments)
	for assignment in assignments:
		await session.delete(assignment)
	await session.commit()

	return DeleteAssignmentResponse(deleted_count=deleted_count)


@router.get("/my-places", response_model=list[AuditorAssignedPlaceItem])
async def list_my_places(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[AuditorAssignedPlaceItem]:
	if user.account_type != AccountType.AUDITOR:
		raise HTTPException(status_code=403, detail="Auditor access is required.")

	auditor = await _get_current_auditor(session, user)
	audit_count = func.count(Audit.id)
	stmt = (
		select(Place, Project.name, audit_count)
		.join(ProjectPlace, ProjectPlace.place_id == Place.id)
		.join(Project, Project.id == ProjectPlace.project_id)
		.join(
			Assignment,
			and_(
				Assignment.project_id == Project.id,
				Assignment.auditor_profile_id == auditor.id,
				or_(Assignment.place_id.is_(None), Assignment.place_id == Place.id),
			),
		)
		.outerjoin(
			Audit,
			(Audit.project_id == Project.id) & (Audit.place_id == Place.id) & (Audit.auditor_profile_id == auditor.id),
		)
		.group_by(Place.id, Project.name)
		.order_by(Project.name.asc(), Place.name.asc())
	)
	rows = (await session.execute(stmt)).all()
	return [
		AuditorAssignedPlaceItem(
			id=str(place.id),
			name=place.name,
			project=project_name,
			address=place.address or "",
			audits=int(audits),
		)
		for place, project_name, audits in rows
	]

"""Dashboard REST API endpoints for manager/admin views."""

from __future__ import annotations

import uuid
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request as FastAPIRequest
from pydantic import BaseModel, Field
from sqlalchemy import Select, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.auth import (
    _build_invite_url,
    _manager_account_name,
    get_auth_session,
    get_current_user,
)
from app.auth_security import generate_email_verification_token, hash_verification_token
from app.email_service import send_auditor_invite_email
from app.models import (
    Account,
    AccountType,
    Assignment,
    Audit,
    AuditStatus,
    Auditor,
    AuditorInvite,
    Place,
    Project,
    ProjectPlace,
    User,
    YeeAuditSubmission,
)

router: APIRouter = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardMetricResponse(BaseModel):
    title: str
    value: str
    description: str
    trend: str


class AuditListItem(BaseModel):
    id: str
    place: str
    auditor: str
    date: str
    score: int
    status: str


class DashboardOverviewResponse(BaseModel):
    metrics: list[DashboardMetricResponse]
    recent_activity: list[str]
    latest_audits: list[AuditListItem]


class ProjectListItem(BaseModel):
    id: str
    name: str
    lead: str
    places: int
    audits: int
    status: str


class PlaceListItem(BaseModel):
    id: str
    name: str
    project: str
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
    assigned_places: int
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


class ApproveUserRequest(BaseModel):
    user_id: uuid.UUID
    account_id: uuid.UUID | None = None


class CreateProjectRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)


class CreatePlaceRequest(BaseModel):
    project_id: uuid.UUID
    name: str = Field(..., min_length=1, max_length=200)
    address: str = Field(..., min_length=1, max_length=500)
    notes: str | None = Field(default=None, max_length=2000)


class CreateAuditorInviteRequest(BaseModel):
    email: str = Field(..., max_length=320)


class AuditorInviteResponse(BaseModel):
    id: str
    email: str
    status: str
    expires_at: datetime
    invite_url: str


class CreateAssignmentRequest(BaseModel):
    auditor_id: uuid.UUID
    place_id: uuid.UUID


class AssignmentResponse(BaseModel):
    id: str
    auditor_id: str
    place_id: str


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


class PlaceComparisonAuditItem(BaseModel):
    audit_id: str
    auditor_id: str
    place_id: str
    place_name: str
    project_id: str
    project_name: str
    date: str
    total_raw_score: int
    total_weighted_score: int
    raw_domain_scores: dict[str, int]
    weighted_domain_scores: dict[str, int]


class PlaceComparisonGroup(BaseModel):
    place_id: str
    place_name: str
    project_id: str
    project_name: str
    audits: list[PlaceComparisonAuditItem]


class PlaceDetailResponse(BaseModel):
    id: str
    name: str
    address: str
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


class RawDataExportRow(BaseModel):
    audit_id: str
    auditor_generated_id: str
    place_id: str
    place_name: str
    project_id: str
    project_name: str
    date: str
    submitted_at: str
    start_time: str
    finish_time: str
    total_minutes: int
    visit_frequency: str
    season: str
    weather: str
    comments: str
    raw_access: int
    raw_activity_spaces: int
    raw_amenities: int
    raw_experience_of_space: int
    raw_aesthetics_and_care: int
    raw_use_and_usability: int
    weighted_access: int
    weighted_activity_spaces: int
    weighted_amenities: int
    weighted_experience_of_space: int
    weighted_aesthetics_and_care: int
    weighted_use_and_usability: int
    total_raw_score: int
    total_weighted_score: int
    responses: dict[str, str]


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


def _format_timestamp(value: datetime | None) -> str:
    if value is None:
        return "Not yet"
    return value.strftime("%b %d, %Y")


def _extract_score(scores_json: dict[str, object]) -> int:
    score = scores_json.get("total_score")
    return score if isinstance(score, int) else 0


REPORT_DOMAIN_ORDER = (
    "access",
    "activitySpaces",
    "amenities",
    "experienceOfSpace",
    "aestheticsAndCare",
    "useAndUsability",
)


def _empty_domain_scores() -> dict[str, int]:
    return {domain: 0 for domain in REPORT_DOMAIN_ORDER}


def _section_to_domain(section_name: str) -> str | None:
    normalized = section_name.lower()
    if "access" in normalized:
        return "access"
    if "activity spaces" in normalized:
        return "activitySpaces"
    if "amenities" in normalized:
        return "amenities"
    if "experience" in normalized:
        return "experienceOfSpace"
    if "aesthetics" in normalized:
        return "aestheticsAndCare"
    if "use & usability" in normalized:
        return "useAndUsability"
    return None


def _coerce_weight(value: object) -> int:
    if isinstance(value, int):
        return value if value in {1, 2, 3} else 0
    if isinstance(value, str) and value.isdigit():
        numeric = int(value)
        return numeric if numeric in {1, 2, 3} else 0
    return 0


def _extract_domain_weights(participant_info: dict[str, Any]) -> dict[str, int]:
    raw_weights = participant_info.get("domain_weights")
    if not isinstance(raw_weights, dict):
        return _empty_domain_scores()
    return {domain: _coerce_weight(raw_weights.get(domain)) for domain in REPORT_DOMAIN_ORDER}


def _build_submission_scores(
    section_scores: dict[str, Any],
    participant_info: dict[str, Any],
) -> tuple[dict[str, int], dict[str, int], int]:
    raw_domain_scores = _empty_domain_scores()
    for section_name, score in section_scores.items():
        domain = _section_to_domain(section_name)
        if domain is None or not isinstance(score, int):
            continue
        raw_domain_scores[domain] += score

    weights = _extract_domain_weights(participant_info)
    weighted_domain_scores = {
        domain: raw_domain_scores[domain] * weights[domain] for domain in REPORT_DOMAIN_ORDER
    }
    total_weighted_score = sum(weighted_domain_scores.values())
    return raw_domain_scores, weighted_domain_scores, total_weighted_score


def _flatten_responses(responses: dict[str, Any]) -> dict[str, str]:
    flat: dict[str, str] = {}
    for key, value in responses.items():
        if isinstance(value, dict):
            for nested_key, nested_value in value.items():
                flat[f"response_{key}__{nested_key}"] = str(nested_value)
        else:
            flat[f"response_{key}"] = str(value)
    return flat


async def _fetch_reporting_rows(
    session: AsyncSession,
    account_id: uuid.UUID | None,
) -> list[tuple[YeeAuditSubmission, Place, Project, str]]:
    stmt = (
        select(YeeAuditSubmission, Place, Project, Auditor.auditor_code)
        .join(Place, YeeAuditSubmission.place_id == Place.id)
        .join(ProjectPlace, ProjectPlace.place_id == Place.id)
        .join(Project, ProjectPlace.project_id == Project.id)
        .join(Auditor, YeeAuditSubmission.auditor_id == Auditor.id)
        .order_by(Project.name.asc(), Place.name.asc(), YeeAuditSubmission.submitted_at.desc())
    )
    if account_id is not None:
        stmt = stmt.where(Project.account_id == account_id)
    rows = (await session.execute(stmt)).all()
    return [tuple(row) for row in rows]


async def _fetch_place_comparison_groups(
    session: AsyncSession,
    account_id: uuid.UUID | None,
) -> list[PlaceComparisonGroup]:
    rows = await _fetch_reporting_rows(session, account_id)
    grouped: dict[str, dict[str, Any]] = defaultdict(dict)

    for submission, place, project, auditor_code in rows:
        group = grouped.setdefault(
            str(place.id),
            {
                "place_id": str(place.id),
                "place_name": place.name,
                "project_id": str(project.id),
                "project_name": project.name,
                "audits": [],
            },
        )
        raw_domain_scores, weighted_domain_scores, total_weighted_score = _build_submission_scores(
            submission.section_scores_json,
            submission.participant_info_json,
        )
        group["audits"].append(
            PlaceComparisonAuditItem(
                audit_id=str(submission.id),
                auditor_id=auditor_code,
                place_id=str(place.id),
                place_name=place.name,
                project_id=str(project.id),
                project_name=project.name,
                date=_format_timestamp(submission.submitted_at),
                total_raw_score=submission.total_score,
                total_weighted_score=total_weighted_score,
                raw_domain_scores=raw_domain_scores,
                weighted_domain_scores=weighted_domain_scores,
            )
        )

    return [
        PlaceComparisonGroup(
            place_id=group["place_id"],
            place_name=group["place_name"],
            project_id=group["project_id"],
            project_name=group["project_name"],
            audits=group["audits"],
        )
        for group in grouped.values()
        if group["audits"]
    ]


async def _fetch_raw_data_rows(
    session: AsyncSession,
    account_id: uuid.UUID | None,
) -> list[RawDataExportRow]:
    rows = await _fetch_reporting_rows(session, account_id)
    export_rows: list[RawDataExportRow] = []
    for submission, place, project, auditor_code in rows:
        participant_info = submission.participant_info_json
        raw_domain_scores, weighted_domain_scores, total_weighted_score = _build_submission_scores(
            submission.section_scores_json,
            participant_info,
        )
        raw_total_minutes = participant_info.get("total_minutes")
        total_minutes = (
            int(raw_total_minutes) if isinstance(raw_total_minutes, int | float | str) else 0
        )
        export_rows.append(
            RawDataExportRow(
                audit_id=str(submission.id),
                auditor_generated_id=auditor_code,
                place_id=str(place.id),
                place_name=place.name,
                project_id=str(project.id),
                project_name=project.name,
                date=str(
                    participant_info.get("audit_date") or submission.submitted_at.date().isoformat()
                ),
                submitted_at=submission.submitted_at.isoformat(),
                start_time=str(participant_info.get("start_time") or ""),
                finish_time=str(participant_info.get("finish_time") or ""),
                total_minutes=total_minutes,
                visit_frequency=str(participant_info.get("visit_frequency") or ""),
                season=str(participant_info.get("season") or ""),
                weather=str(participant_info.get("weather") or ""),
                comments=str(participant_info.get("comments") or ""),
                raw_access=raw_domain_scores["access"],
                raw_activity_spaces=raw_domain_scores["activitySpaces"],
                raw_amenities=raw_domain_scores["amenities"],
                raw_experience_of_space=raw_domain_scores["experienceOfSpace"],
                raw_aesthetics_and_care=raw_domain_scores["aestheticsAndCare"],
                raw_use_and_usability=raw_domain_scores["useAndUsability"],
                weighted_access=weighted_domain_scores["access"],
                weighted_activity_spaces=weighted_domain_scores["activitySpaces"],
                weighted_amenities=weighted_domain_scores["amenities"],
                weighted_experience_of_space=weighted_domain_scores["experienceOfSpace"],
                weighted_aesthetics_and_care=weighted_domain_scores["aestheticsAndCare"],
                weighted_use_and_usability=weighted_domain_scores["useAndUsability"],
                total_raw_score=submission.total_score,
                total_weighted_score=total_weighted_score,
                responses=_flatten_responses(submission.responses_json),
            )
        )
    return export_rows


async def _count_rows(
    session: AsyncSession,
    model: type[object],
    where_clause: ColumnElement[bool] | None = None,
) -> int:
    stmt = select(func.count()).select_from(model)
    if where_clause is not None:
        stmt = stmt.where(where_clause)
    return int((await session.execute(stmt)).scalar_one())


async def _fetch_latest_audits(
    session: AsyncSession, account_id: uuid.UUID | None = None
) -> list[AuditListItem]:
    stmt = (
        select(Audit, Place.name, Auditor.auditor_code)
        .join(Place, Audit.place_id == Place.id)
        .join(Project, Audit.project_id == Project.id)
        .join(Auditor, Audit.auditor_profile_id == Auditor.id)
        .order_by(Audit.submitted_at.desc().nullslast(), Audit.started_at.desc())
        .limit(6)
    )
    if account_id is not None:
        stmt = stmt.where(Project.account_id == account_id)
    rows = (await session.execute(stmt)).all()
    return [
        AuditListItem(
            id=str(audit.id),
            place=place_name,
            auditor=auditor_code,
            date=_format_timestamp(audit.submitted_at or audit.started_at),
            score=_extract_score(audit.scores_json),
            status="Submitted" if audit.status == AuditStatus.SUBMITTED else "Draft",
        )
        for audit, place_name, auditor_code in rows
    ]


async def _fetch_projects(
    session: AsyncSession, account_id: uuid.UUID | None = None
) -> list[ProjectListItem]:
    audit_count = func.count(Audit.id)
    place_count = func.count(func.distinct(Place.id))
    stmt: Select[tuple[Project, int, int]] = (
        select(Project, place_count, audit_count)
        .outerjoin(ProjectPlace, ProjectPlace.project_id == Project.id)
        .outerjoin(Place, Place.id == ProjectPlace.place_id)
        .outerjoin(
            Audit,
            and_(Audit.project_id == Project.id, Audit.place_id == ProjectPlace.place_id),
        )
        .group_by(Project.id)
        .order_by(Project.name.asc())
    )
    if account_id is not None:
        stmt = stmt.where(Project.account_id == account_id)
    rows = (await session.execute(stmt)).all()
    return [
        ProjectListItem(
            id=str(project.id),
            name=project.name,
            lead=project.description or "Project lead pending",
            places=int(places),
            audits=int(audits),
            status="Planning" if project.start_date is None else "Active",
        )
        for project, places, audits in rows
    ]


async def _fetch_places(
    session: AsyncSession, account_id: uuid.UUID | None = None
) -> list[PlaceListItem]:
    last_audit = func.max(Audit.submitted_at)
    audit_count = func.count(Audit.id)
    stmt = (
        select(Place, Project.name, audit_count, last_audit)
        .join(ProjectPlace, ProjectPlace.place_id == Place.id)
        .join(Project, ProjectPlace.project_id == Project.id)
        .outerjoin(Audit, and_(Audit.project_id == Project.id, Audit.place_id == Place.id))
        .group_by(Place.id, Project.name)
        .order_by(Project.name.asc(), Place.name.asc())
    )
    if account_id is not None:
        stmt = stmt.where(Project.account_id == account_id)
    rows = (await session.execute(stmt)).all()
    return [
        PlaceListItem(
            id=str(place.id),
            name=place.name,
            project=project_name,
            audits=int(audits),
            last_audit=_format_timestamp(last_submitted_at),
            status="Needs review" if int(audits) == 0 else "Up to date",
        )
        for place, project_name, audits, last_submitted_at in rows
    ]


async def _get_scoped_project(
    session: AsyncSession,
    account_id: uuid.UUID | None,
    project_id: uuid.UUID,
) -> tuple[Project, str]:
    stmt = (
        select(Project, Account.name)
        .join(Account, Project.account_id == Account.id)
        .where(Project.id == project_id)
    )
    if account_id is not None:
        stmt = stmt.where(Project.account_id == account_id)
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    project, account_name = row
    return project, account_name


async def _get_scoped_place(
    session: AsyncSession,
    account_id: uuid.UUID | None,
    place_id: uuid.UUID,
) -> tuple[Place, Project]:
    stmt = (
        select(Place, Project)
        .join(ProjectPlace, ProjectPlace.place_id == Place.id)
        .join(Project, ProjectPlace.project_id == Project.id)
        .where(Place.id == place_id)
    )
    if account_id is not None:
        stmt = stmt.where(Project.account_id == account_id)
    row = (await session.execute(stmt)).one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail="Place not found.")
    place, project = row
    return place, project


async def _fetch_project_detail(
    session: AsyncSession,
    account_id: uuid.UUID | None,
    project_id: uuid.UUID,
) -> ProjectDetailResponse:
    project, organization_name = await _get_scoped_project(session, account_id, project_id)

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
            address=place.address,
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

    latest_stmt = (
        select(Audit, Place.name, Auditor.auditor_code)
        .join(Place, Audit.place_id == Place.id)
        .join(Auditor, Audit.auditor_profile_id == Auditor.id)
        .where(Audit.project_id == project.id)
        .order_by(Audit.submitted_at.desc().nullslast(), Audit.started_at.desc())
        .limit(8)
    )
    latest_rows = (await session.execute(latest_stmt)).all()
    latest_audits = [
        AuditListItem(
            id=str(audit.id),
            place=place_name,
            auditor=auditor_code,
            date=_format_timestamp(audit.submitted_at or audit.started_at),
            score=_extract_score(audit.scores_json),
            status="Submitted" if audit.status == AuditStatus.SUBMITTED else "Draft",
        )
        for audit, place_name, auditor_code in latest_rows
    ]

    assigned_places = func.count(func.distinct(Assignment.place_id))
    completed_audits = func.count(func.distinct(Audit.id))
    auditor_stmt = (
        select(Auditor, User.name, assigned_places, completed_audits)
        .join(Assignment, Assignment.auditor_profile_id == Auditor.id)
        .join(Place, Assignment.place_id == Place.id)
        .outerjoin(User, Auditor.user_id == User.id)
        .outerjoin(
            Audit,
            (Audit.auditor_profile_id == Auditor.id)
            & (Audit.status == AuditStatus.SUBMITTED)
            & (Audit.project_id == project.id),
        )
        .where(Assignment.project_id == project.id)
        .group_by(Auditor.id, User.name)
        .order_by(User.name.asc().nullslast(), Auditor.auditor_code.asc())
    )
    auditor_rows = (await session.execute(auditor_stmt)).all()
    auditors = [
        ProjectAuditorItem(
            id=str(auditor.id),
            name=user_name or auditor.auditor_code,
            auditor_id=auditor.auditor_code,
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
    account_id: uuid.UUID | None,
    place_id: uuid.UUID,
) -> PlaceDetailResponse:
    place, project = await _get_scoped_place(session, account_id, place_id)

    comparisons = await _fetch_place_comparison_groups(session, account_id)
    comparison_group = next(
        (group for group in comparisons if group.place_id == str(place.id)), None
    )
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
        .where(Assignment.place_id == place.id)
        .group_by(Auditor.id, User.name)
        .order_by(User.name.asc().nullslast(), Auditor.auditor_code.asc())
    )
    auditor_rows = (await session.execute(auditor_stmt)).all()
    auditors = [
        PlaceAuditorItem(
            id=str(auditor.id),
            name=user_name or auditor.auditor_code,
            auditor_id=auditor.auditor_code,
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
                Audit.place_id == place.id, Audit.status == AuditStatus.SUBMITTED
            )
        )
    ).scalar_one()

    return PlaceDetailResponse(
        id=str(place.id),
        name=place.name,
        address=place.address,
        notes=place.notes or "No additional place notes have been added yet.",
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


async def _fetch_auditors(
    session: AsyncSession, account_id: uuid.UUID | None = None
) -> list[AuditorListItem]:
    assigned_places = func.count(func.distinct(Assignment.place_id))
    completed_audits = func.count(Audit.id)
    stmt = (
        select(Auditor, User.name, assigned_places, completed_audits)
        .outerjoin(User, Auditor.user_id == User.id)
        .outerjoin(Assignment, Assignment.auditor_profile_id == Auditor.id)
        .outerjoin(
            Audit,
            (Audit.auditor_profile_id == Auditor.id) & (Audit.status == AuditStatus.SUBMITTED),
        )
        .group_by(Auditor.id, User.name)
        .order_by(User.name.asc().nullslast(), Auditor.auditor_code.asc())
    )
    if account_id is not None:
        stmt = stmt.where(Auditor.account_id == account_id)
    rows = (await session.execute(stmt)).all()
    return [
        AuditorListItem(
            id=str(auditor.id),
            name=user_name or auditor.auditor_code,
            assigned_places=int(place_total),
            completed_audits=int(audit_total),
            status="Active" if auditor.user_id else "Invite pending",
        )
        for auditor, user_name, place_total, audit_total in rows
    ]


async def _fetch_users(session: AsyncSession) -> list[UserListItem]:
    account_alias = aliased(Account)
    stmt = (
        select(User, account_alias.name)
        .outerjoin(account_alias, account_alias.id == User.account_id)
        .order_by(User.email.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        UserListItem(
            id=str(user.id),
            name=user.name or user.email,
            email=user.email,
            role=user.account_type.value,
            account_id=str(user.account_id) if user.account_id is not None else None,
            organization=account_name or "Unassigned",
            status=_status_for_user(user),
            approved=user.approved,
            email_verified=user.email_verified,
            profile_completed=user.profile_completed,
        )
        for user, account_name in rows
    ]


def _normalize_email(email: str) -> str:
    return email.strip().lower()


async def _generate_unique_auditor_code(session: AsyncSession) -> str:
    while True:
        code = f"AUD-{uuid.uuid4().hex[:6].upper()}"
        existing = await session.execute(select(Auditor.id).where(Auditor.auditor_code == code))
        if existing.scalar_one_or_none() is None:
            return code


async def _get_current_auditor(session: AsyncSession, user: User) -> Auditor:
    result = await session.execute(select(Auditor).where(Auditor.user_id == user.id))
    auditor = result.scalar_one_or_none()
    if auditor is None:
        raise HTTPException(status_code=404, detail="Auditor profile not found.")
    return auditor


@router.get("/overview", response_model=DashboardOverviewResponse)
async def get_dashboard_overview(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_auth_session),
) -> DashboardOverviewResponse:
    """Return overview metrics and recent audit activity for dashboard landing pages."""

    _require_manager_or_admin(user)
    account_id = _manager_account_id(user)

    projects_count = (
        await _count_rows(session, Project, Project.account_id == account_id)
        if account_id
        else await _count_rows(session, Project)
    )
    places_count = (
        int(
            (
                await session.execute(
                    select(func.count(func.distinct(ProjectPlace.place_id)))
                    .join(Project, ProjectPlace.project_id == Project.id)
                    .where(Project.account_id == account_id)
                )
            ).scalar_one()
        )
        if account_id
        else await _count_rows(session, Place)
    )
    auditors_count = (
        await _count_rows(session, Auditor, Auditor.account_id == account_id)
        if account_id
        else await _count_rows(session, Auditor)
    )
    completed_audits = (
        await _count_rows(
            session,
            Audit,
            (Audit.project_id.in_(select(Project.id).where(Project.account_id == account_id)))
            & (Audit.status == AuditStatus.SUBMITTED),
        )
        if account_id
        else await _count_rows(session, Audit, Audit.status == AuditStatus.SUBMITTED)
    )

    latest_audits = await _fetch_latest_audits(session, account_id)
    recent_activity = [
        f"{audit.place} was submitted by {audit.auditor} on {audit.date}."
        for audit in latest_audits[:3]
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
    )


@router.get("/projects", response_model=list[ProjectListItem])
async def list_projects(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_auth_session),
) -> list[ProjectListItem]:
    _require_manager_or_admin(user)
    return await _fetch_projects(session, _manager_account_id(user))


@router.get("/projects/{project_id}", response_model=ProjectDetailResponse)
async def get_project_detail(
    project_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_auth_session),
) -> ProjectDetailResponse:
    _require_manager_or_admin(user)
    return await _fetch_project_detail(session, _manager_account_id(user), project_id)


@router.get("/places", response_model=list[PlaceListItem])
async def list_places(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_auth_session),
) -> list[PlaceListItem]:
    _require_manager_or_admin(user)
    return await _fetch_places(session, _manager_account_id(user))


@router.get("/places/{place_id}", response_model=PlaceDetailResponse)
async def get_place_detail(
    place_id: uuid.UUID,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_auth_session),
) -> PlaceDetailResponse:
    _require_manager_or_admin(user)
    return await _fetch_place_detail(session, _manager_account_id(user), place_id)


@router.get("/auditors", response_model=list[AuditorListItem])
async def list_auditors(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_auth_session),
) -> list[AuditorListItem]:
    _require_manager_or_admin(user)
    return await _fetch_auditors(session, _manager_account_id(user))


@router.get("/audits", response_model=list[AuditListItem])
async def list_audits(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_auth_session),
) -> list[AuditListItem]:
    _require_manager_or_admin(user)
    return await _fetch_latest_audits(session, _manager_account_id(user))


@router.get("/users", response_model=list[UserListItem])
async def list_users(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_auth_session),
) -> list[UserListItem]:
    _require_admin(user)
    return await _fetch_users(session)


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
            raise HTTPException(
                status_code=400,
                detail="An account is required to approve this auditor.",
            )
        target_account = await session.get(Account, account_id)
        if target_account is None:
            raise HTTPException(status_code=404, detail="Account not found.")
        target_user.account_id = target_account.id

        auditor_result = await session.execute(
            select(Auditor).where(Auditor.user_id == target_user.id)
        )
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
    elif target_user.account_type == AccountType.MANAGER and target_user.account_id is None:
        target_account = Account(
            name=_manager_account_name(target_user.name, target_user.email),
            email=target_user.email,
            password_hash=target_user.password_hash,
            account_type=AccountType.MANAGER,
        )
        session.add(target_account)
        await session.flush()
        target_user.account_id = target_account.id

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
        account_id=(str(target_user.account_id) if target_user.account_id is not None else None),
        organization=account_name,
        status=_status_for_user(target_user),
        approved=target_user.approved,
        email_verified=target_user.email_verified,
        profile_completed=target_user.profile_completed,
    )


@router.get("/reports/place-comparisons", response_model=list[PlaceComparisonGroup])
async def list_place_comparisons(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_auth_session),
) -> list[PlaceComparisonGroup]:
    _require_manager_or_admin(user)
    return await _fetch_place_comparison_groups(session, _manager_account_id(user))


@router.get("/raw-data", response_model=list[RawDataExportRow])
async def list_raw_data(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_auth_session),
) -> list[RawDataExportRow]:
    _require_manager_or_admin(user)
    return await _fetch_raw_data_rows(session, _manager_account_id(user))


@router.post("/projects", response_model=ProjectListItem)
async def create_project(
    payload: CreateProjectRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_auth_session),
) -> ProjectListItem:
    _require_manager_or_admin(user)
    account_id = _manager_account_id(user)
    if account_id is None:
        raise HTTPException(
            status_code=403,
            detail="Admin project creation is not supported from this route.",
        )

    project = Project(
        account_id=account_id,
        name=payload.name.strip(),
        overview=(
            payload.description.strip()
            if payload.description and payload.description.strip()
            else None
        ),
        place_types=[],
    )
    session.add(project)
    await session.commit()

    return ProjectListItem(
        id=str(project.id),
        name=project.name,
        lead="Project lead pending",
        places=0,
        audits=0,
        status="Planning",
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
        raise HTTPException(
            status_code=403,
            detail="Admin place creation is not supported from this route.",
        )

    project = await session.get(Project, payload.project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Project not found.")
    if project.account_id != account_id:
        raise HTTPException(status_code=403, detail="Project is outside your account scope.")

    place = Place(
        name=payload.name.strip(),
        city=payload.address.strip(),
        auditor_description=(
            payload.notes.strip() if payload.notes and payload.notes.strip() else None
        ),
    )
    session.add(place)
    await session.flush()
    session.add(ProjectPlace(project_id=project.id, place_id=place.id))
    await session.commit()

    return PlaceListItem(
        id=str(place.id),
        name=place.name,
        project=project.name,
        audits=0,
        last_audit="Not yet",
        status="Needs review",
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
        raise HTTPException(
            status_code=403, detail="Admin invites are not supported from this route."
        )

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


@router.post("/assignments", response_model=AssignmentResponse)
async def create_assignment(
    payload: CreateAssignmentRequest,
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_auth_session),
) -> AssignmentResponse:
    _require_manager_or_admin(user)
    account_id = _manager_account_id(user)
    if account_id is None:
        raise HTTPException(
            status_code=403,
            detail="Admin assignments are not supported from this route.",
        )

    auditor = await session.get(Auditor, payload.auditor_id)
    if auditor is None or auditor.account_id != account_id:
        raise HTTPException(status_code=404, detail="Auditor not found in your account.")

    place_stmt = (
        select(Place, Project)
        .join(ProjectPlace, ProjectPlace.place_id == Place.id)
        .join(Project, ProjectPlace.project_id == Project.id)
        .where(Place.id == payload.place_id, Project.account_id == account_id)
    )
    place_row = (await session.execute(place_stmt)).first()
    if place_row is None:
        raise HTTPException(status_code=404, detail="Place not found in your account.")
    place, project = place_row

    existing_stmt = select(Assignment).where(
        Assignment.auditor_profile_id == auditor.id,
        Assignment.project_id == project.id,
        Assignment.place_id == place.id,
    )
    existing = (await session.execute(existing_stmt)).scalar_one_or_none()
    if existing is not None:
        return AssignmentResponse(
            id=str(existing.id),
            auditor_id=str(existing.auditor_id),
            place_id=str(existing.place_id),
        )

    assignment = Assignment(auditor_profile_id=auditor.id, project_id=project.id, place_id=place.id)
    session.add(assignment)
    await session.commit()
    await session.refresh(assignment)

    return AssignmentResponse(
        id=str(assignment.id),
        auditor_id=str(assignment.auditor_id),
        place_id=str(assignment.place_id),
    )


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
        .join(Assignment, Assignment.place_id == Place.id)
        .join(Project, Assignment.project_id == Project.id)
        .outerjoin(
            Audit,
            (Audit.project_id == Project.id)
            & (Audit.place_id == Place.id)
            & (Audit.auditor_profile_id == auditor.id),
        )
        .where(Assignment.auditor_profile_id == auditor.id)
        .group_by(Place.id, Project.name)
        .order_by(Project.name.asc(), Place.name.asc())
    )
    rows = (await session.execute(stmt)).all()
    return [
        AuditorAssignedPlaceItem(
            id=str(place.id),
            name=place.name,
            project=project_name,
            address=place.address,
            audits=int(audits),
        )
        for place, project_name, audits in rows
    ]

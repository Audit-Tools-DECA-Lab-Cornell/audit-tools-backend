"""
Playspace dashboard query service.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.actors import CurrentUserContext, require_manager_user
from app.models import (
    Account,
    Audit,
    AuditorAssignment,
    AuditorProfile,
    AuditStatus,
    ManagerProfile,
    Place,
    Project,
)
from app.products.playspace.schemas import (
    AccountDetailResponse,
    AccountStatsResponse,
    AuditorSummaryResponse,
    ManagerProfileResponse,
    PlaceAuditHistoryItemResponse,
    PlaceActivityStatus,
    PlaceHistoryResponse,
    PlaceSummaryResponse,
    ProjectDetailResponse,
    ProjectStatsResponse,
    ProjectStatus,
    ProjectSummaryResponse,
    RecentActivityResponse,
)

PROJECT_NOT_FOUND_DETAIL = "Project not found."


def _derive_project_status(start_date: date | None, end_date: date | None) -> ProjectStatus:
    """Classify a project into a simple planned/active/completed state."""

    today = date.today()
    if start_date is not None and start_date > today:
        return "planned"
    if end_date is not None and end_date < today:
        return "completed"
    return "active"


def _derive_place_status(audits: list[Audit]) -> PlaceActivityStatus:
    """Derive a compact place status from related audit lifecycle states."""

    if any(audit.status == AuditStatus.IN_PROGRESS for audit in audits):
        return "in_progress"
    if any(audit.status == AuditStatus.SUBMITTED for audit in audits):
        return "submitted"
    return "not_started"


def _round_score(value: float | None) -> float | None:
    """Round a score to one decimal place when present."""

    if value is None:
        return None
    return round(value, 1)


def _average_submitted_score(audits: list[Audit]) -> float | None:
    """Return the mean summary score across submitted audits."""

    submitted_scores = [
        audit.summary_score
        for audit in audits
        if audit.status == AuditStatus.SUBMITTED and audit.summary_score is not None
    ]
    if not submitted_scores:
        return None

    return _round_score(sum(submitted_scores) / len(submitted_scores))


def _latest_activity_timestamp(audits: list[Audit]) -> datetime | None:
    """Return the latest visible audit activity timestamp for an auditor or place."""

    timestamps = [
        audit.submitted_at if audit.submitted_at is not None else audit.started_at
        for audit in audits
    ]
    if not timestamps:
        return None
    return max(timestamps)


def _collect_project_auditor_ids(project: Project) -> set[uuid.UUID]:
    """Collect unique auditor profile IDs assigned at project or place scope."""

    auditor_ids = {assignment.auditor_profile_id for assignment in project.assignments}
    for place in project.places:
        auditor_ids.update(assignment.auditor_profile_id for assignment in place.assignments)
    return auditor_ids


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
        """Load an account with the relationships needed for the manager dashboard."""

        stmt = (
            select(Account)
            .where(Account.id == account_id)
            .options(
                selectinload(Account.manager_profiles),
                selectinload(Account.projects)
                .selectinload(Project.places)
                .selectinload(Place.audits),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_project_model(self, project_id: uuid.UUID) -> Project | None:
        """Load a project with the relationships needed for project screens."""

        stmt = (
            select(Project)
            .where(Project.id == project_id)
            .options(
                selectinload(Project.assignments),
                selectinload(Project.places).selectinload(Place.audits),
                selectinload(Project.places).selectinload(Place.assignments),
            )
        )
        result = await self._session.execute(stmt)
        return result.scalar_one_or_none()

    async def _get_place_model(self, place_id: uuid.UUID) -> Place | None:
        """Load a place with project, audits, and auditor profile relationships."""

        stmt = (
            select(Place)
            .where(Place.id == place_id)
            .options(
                selectinload(Place.project),
                selectinload(Place.audits).selectinload(Audit.auditor_profile),
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
        place_id: uuid.UUID,
    ) -> Place:
        """Load a place and enforce manager access to its parent account."""

        place = await self._get_place_model(place_id)
        if place is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Place not found.",
            )
        self._ensure_manager_scope(actor, place.project.account_id)
        return place

    async def _get_account_auditor_summaries_db(
        self,
        account_id: uuid.UUID,
    ) -> list[AuditorSummaryResponse]:
        """Fetch manager-facing auditor summaries for a real account."""

        project_ids_result = await self._session.execute(
            select(Project.id).where(Project.account_id == account_id),
        )
        project_ids = list(project_ids_result.scalars().all())
        if not project_ids:
            return []

        place_ids_result = await self._session.execute(
            select(Place.id).where(Place.project_id.in_(project_ids)),
        )
        place_ids = list(place_ids_result.scalars().all())

        assignment_filters = []
        if project_ids:
            assignment_filters.append(AuditorAssignment.project_id.in_(project_ids))
        if place_ids:
            assignment_filters.append(AuditorAssignment.place_id.in_(place_ids))

        if not assignment_filters:
            return []

        assignments_stmt = (
            select(AuditorAssignment)
            .where(or_(*assignment_filters))
            .options(
                selectinload(AuditorAssignment.auditor_profile).selectinload(AuditorProfile.audits),
            )
        )
        assignments_result = await self._session.execute(assignments_stmt)
        assignments = assignments_result.scalars().all()

        summaries_by_auditor_id: dict[uuid.UUID, AuditorSummaryResponse] = {}
        assignment_counts: dict[uuid.UUID, int] = {}

        for assignment in assignments:
            profile = assignment.auditor_profile
            assignment_counts[profile.id] = assignment_counts.get(profile.id, 0) + 1
            if profile.id in summaries_by_auditor_id:
                continue

            completed_audits = sum(
                1 for audit in profile.audits if audit.status == AuditStatus.SUBMITTED
            )
            summaries_by_auditor_id[profile.id] = AuditorSummaryResponse(
                id=profile.id,
                account_id=profile.account_id,
                auditor_code=profile.auditor_code,
                full_name=profile.full_name,
                email=profile.email,
                age_range=profile.age_range,
                gender=profile.gender,
                country=profile.country,
                role=profile.role,
                assignments_count=0,
                completed_audits=completed_audits,
                last_active_at=_latest_activity_timestamp(profile.audits),
            )

        hydrated_summaries: list[AuditorSummaryResponse] = []
        for auditor_id, summary in summaries_by_auditor_id.items():
            hydrated_summaries.append(
                summary.model_copy(
                    update={"assignments_count": assignment_counts.get(auditor_id, 0)},
                ),
            )

        return sorted(hydrated_summaries, key=lambda summary: summary.auditor_code.lower())

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

        recent_activity: list[RecentActivityResponse] = []
        total_places = 0
        total_audits_completed = 0

        for project in account.projects:
            for place in project.places:
                total_places += 1
                for audit in place.audits:
                    if audit.status != AuditStatus.SUBMITTED or audit.submitted_at is None:
                        continue

                    total_audits_completed += 1
                    recent_activity.append(
                        RecentActivityResponse(
                            audit_id=audit.id,
                            audit_code=audit.audit_code,
                            project_id=project.id,
                            project_name=project.name,
                            place_id=place.id,
                            place_name=place.name,
                            completed_at=audit.submitted_at,
                            score=_round_score(audit.summary_score),
                        ),
                    )

        recent_activity.sort(key=lambda activity: activity.completed_at, reverse=True)
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
                total_projects=len(account.projects),
                total_places=total_places,
                total_auditors=len(auditors),
                total_audits_completed=total_audits_completed,
            ),
            recent_activity=recent_activity[:5],
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

        stmt = (
            select(Project)
            .where(Project.account_id == account_id)
            .order_by(Project.created_at.desc(), Project.name.asc())
            .options(
                selectinload(Project.assignments),
                selectinload(Project.places).selectinload(Place.audits),
                selectinload(Project.places).selectinload(Place.assignments),
            )
        )
        result = await self._session.execute(stmt)
        projects = result.scalars().all()

        project_summaries: list[ProjectSummaryResponse] = []
        for project in projects:
            auditor_ids = _collect_project_auditor_ids(project)

            submitted_audits = [
                audit
                for place in project.places
                for audit in place.audits
                if audit.status == AuditStatus.SUBMITTED
            ]

            project_summaries.append(
                ProjectSummaryResponse(
                    id=project.id,
                    account_id=project.account_id,
                    name=project.name,
                    overview=project.overview,
                    place_types=list(project.place_types),
                    start_date=project.start_date,
                    end_date=project.end_date,
                    status=_derive_project_status(project.start_date, project.end_date),
                    places_count=len(project.places),
                    auditors_count=len(auditor_ids),
                    audits_completed=len(submitted_audits),
                    average_score=_average_submitted_score(submitted_audits),
                ),
            )

        return project_summaries

    async def list_account_auditors(
        self,
        actor: CurrentUserContext,
        account_id: uuid.UUID,
    ) -> list[AuditorSummaryResponse]:
        """Return manager-facing auditor summaries for the requested account."""

        self._ensure_manager_scope(actor, account_id)
        return await self._get_account_auditor_summaries_db(account_id=account_id)

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
            for audit in place.audits
            if audit.status == AuditStatus.SUBMITTED
        ]
        in_progress_audits = sum(
            1
            for place in project.places
            for audit in place.audits
            if audit.status == AuditStatus.IN_PROGRESS
        )
        auditors_count = _collect_project_auditor_ids(project)

        places_with_audits = sum(1 for place in project.places if place.audits)
        return ProjectStatsResponse(
            project_id=project.id,
            places_count=len(project.places),
            places_with_audits=places_with_audits,
            audits_completed=len(submitted_audits),
            auditors_count=len(auditors_count),
            in_progress_audits=in_progress_audits,
            average_score=_average_submitted_score(submitted_audits),
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
                audit for audit in place.audits if audit.status == AuditStatus.SUBMITTED
            ]
            place_summaries.append(
                PlaceSummaryResponse(
                    id=place.id,
                    project_id=place.project_id,
                    name=place.name,
                    city=place.city,
                    province=place.province,
                    country=place.country,
                    place_type=place.place_type,
                    status=_derive_place_status(place.audits),
                    audits_completed=len(submitted_audits),
                    average_score=_average_submitted_score(submitted_audits),
                    last_audited_at=_latest_activity_timestamp(submitted_audits),
                ),
            )

        return place_summaries

    async def list_place_audits(
        self,
        *,
        actor: CurrentUserContext,
        place_id: uuid.UUID,
    ) -> list[PlaceAuditHistoryItemResponse]:
        """Return audit history rows for one place."""

        place = await self._require_place_for_actor(actor=actor, place_id=place_id)
        sorted_audits = sorted(
            place.audits,
            key=lambda audit: (
                audit.submitted_at if audit.submitted_at is not None else audit.started_at
            ),
            reverse=True,
        )
        history_rows: list[PlaceAuditHistoryItemResponse] = []
        for audit in sorted_audits:
            history_rows.append(
                PlaceAuditHistoryItemResponse(
                    audit_id=audit.id,
                    audit_code=audit.audit_code,
                    auditor_code=audit.auditor_profile.auditor_code,
                    status=audit.status.value,
                    started_at=audit.started_at,
                    submitted_at=audit.submitted_at,
                    summary_score=_round_score(audit.summary_score),
                )
            )
        return history_rows

    async def get_place_history(
        self,
        *,
        actor: CurrentUserContext,
        place_id: uuid.UUID,
    ) -> PlaceHistoryResponse:
        """Return aggregate history metrics plus audit rows for one place."""

        place = await self._require_place_for_actor(actor=actor, place_id=place_id)
        audits = place.audits
        submitted_audits = [audit for audit in audits if audit.status == AuditStatus.SUBMITTED]
        in_progress_audits = [
            audit
            for audit in audits
            if audit.status in {AuditStatus.IN_PROGRESS, AuditStatus.PAUSED}
        ]
        history_rows = await self.list_place_audits(actor=actor, place_id=place_id)

        latest_submitted_at = _latest_activity_timestamp(submitted_audits)
        return PlaceHistoryResponse(
            place_id=place.id,
            place_name=place.name,
            project_id=place.project_id,
            total_audits=len(audits),
            submitted_audits=len(submitted_audits),
            in_progress_audits=len(in_progress_audits),
            average_submitted_score=_average_submitted_score(submitted_audits),
            latest_submitted_at=latest_submitted_at,
            audits=history_rows,
        )

"""
Administrator read service for global Playspace oversight dashboards.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core.actors import CurrentUserContext, require_admin_user
from app.models import (
    Account,
    Audit,
    AuditorProfile,
    AuditStatus,
    Place,
    Project,
)
from app.products.playspace.instrument import (
    INSTRUMENT_KEY,
    INSTRUMENT_NAME,
    INSTRUMENT_VERSION,
)
from app.products.playspace.schemas.admin import (
    AdminAccountRowResponse,
    AdminAuditRowResponse,
    AdminAuditorRowResponse,
    AdminOverviewResponse,
    AdminPlaceRowResponse,
    AdminProjectRowResponse,
    AdminSystemResponse,
)
from app.products.playspace.services.privacy import mask_email


def _round_score(value: float | None) -> float | None:
    """Round a numeric score when present."""

    if value is None:
        return None
    return round(value, 1)


def _latest_activity(audits: list[Audit]) -> datetime | None:
    """Resolve latest submitted/started timestamp from audits."""

    timestamps = [
        audit.submitted_at if audit.submitted_at is not None else audit.started_at
        for audit in audits
    ]
    if not timestamps:
        return None
    return max(timestamps)


class PlayspaceAdminService:
    """Global read service for the full administrator dashboard."""

    def __init__(self, session: AsyncSession):
        self._session = session

    @staticmethod
    def _require_admin(actor: CurrentUserContext) -> None:
        """Guard admin-only endpoints."""

        require_admin_user(actor)

    async def get_overview(self, *, actor: CurrentUserContext) -> AdminOverviewResponse:
        """Return global overview counters."""

        self._require_admin(actor)
        accounts_result = await self._session.execute(select(Account))
        projects_result = await self._session.execute(select(Project))
        places_result = await self._session.execute(select(Place))
        auditors_result = await self._session.execute(select(AuditorProfile))
        audits_result = await self._session.execute(select(Audit))

        audits = audits_result.scalars().all()
        submitted_count = sum(1 for audit in audits if audit.status == AuditStatus.SUBMITTED)
        in_progress_count = sum(
            1 for audit in audits if audit.status in {AuditStatus.IN_PROGRESS, AuditStatus.PAUSED}
        )
        return AdminOverviewResponse(
            total_accounts=len(accounts_result.scalars().all()),
            total_projects=len(projects_result.scalars().all()),
            total_places=len(places_result.scalars().all()),
            total_auditors=len(auditors_result.scalars().all()),
            total_audits=len(audits),
            submitted_audits=submitted_count,
            in_progress_audits=in_progress_count,
        )

    async def list_accounts(self, *, actor: CurrentUserContext) -> list[AdminAccountRowResponse]:
        """Return global account rows."""

        self._require_admin(actor)
        result = await self._session.execute(
            select(Account)
            .options(selectinload(Account.projects).selectinload(Project.places))
            .order_by(Account.created_at.desc())
        )
        accounts = result.scalars().all()

        account_rows: list[AdminAccountRowResponse] = []
        for account in accounts:
            places_count = sum(len(project.places) for project in account.projects)
            auditors_count_result = await self._session.execute(
                select(AuditorProfile.id).where(AuditorProfile.account_id == account.id)
            )
            account_rows.append(
                AdminAccountRowResponse(
                    account_id=account.id,
                    name=account.name,
                    account_type=account.account_type,
                    email_masked=mask_email(account.email),
                    created_at=account.created_at,
                    projects_count=len(account.projects),
                    places_count=places_count,
                    auditors_count=len(auditors_count_result.scalars().all()),
                )
            )
        return account_rows

    async def list_projects(self, *, actor: CurrentUserContext) -> list[AdminProjectRowResponse]:
        """Return global project rows."""

        self._require_admin(actor)
        result = await self._session.execute(
            select(Project)
            .options(
                selectinload(Project.account),
                selectinload(Project.assignments),
                selectinload(Project.places).selectinload(Place.assignments),
                selectinload(Project.places).selectinload(Place.audits),
            )
            .order_by(Project.created_at.desc())
        )
        projects = result.scalars().all()

        rows: list[AdminProjectRowResponse] = []
        for project in projects:
            auditor_ids = {assignment.auditor_profile_id for assignment in project.assignments}
            submitted_audits: list[Audit] = []
            score_values: list[float] = []
            for place in project.places:
                for assignment in place.assignments:
                    auditor_ids.add(assignment.auditor_profile_id)
                for audit in place.audits:
                    if audit.status == AuditStatus.SUBMITTED:
                        submitted_audits.append(audit)
                        if audit.summary_score is not None:
                            score_values.append(float(audit.summary_score))

            rows.append(
                AdminProjectRowResponse(
                    project_id=project.id,
                    account_id=project.account_id,
                    account_name=project.account.name,
                    name=project.name,
                    start_date=project.start_date,
                    end_date=project.end_date,
                    places_count=len(project.places),
                    auditors_count=len(auditor_ids),
                    audits_completed=len(submitted_audits),
                    average_score=_round_score(sum(score_values) / len(score_values))
                    if score_values
                    else None,
                )
            )
        return rows

    async def list_places(self, *, actor: CurrentUserContext) -> list[AdminPlaceRowResponse]:
        """Return global place rows."""

        self._require_admin(actor)
        result = await self._session.execute(
            select(Place)
            .options(
                selectinload(Place.project).selectinload(Project.account),
                selectinload(Place.audits),
            )
            .order_by(Place.created_at.desc())
        )
        places = result.scalars().all()
        rows: list[AdminPlaceRowResponse] = []
        for place in places:
            submitted_audits = [
                audit for audit in place.audits if audit.status == AuditStatus.SUBMITTED
            ]
            score_values = [
                float(audit.summary_score)
                for audit in submitted_audits
                if audit.summary_score is not None
            ]
            rows.append(
                AdminPlaceRowResponse(
                    place_id=place.id,
                    project_id=place.project_id,
                    project_name=place.project.name,
                    account_id=place.project.account_id,
                    account_name=place.project.account.name,
                    name=place.name,
                    city=place.city,
                    province=place.province,
                    country=place.country,
                    audits_completed=len(submitted_audits),
                    average_score=_round_score(sum(score_values) / len(score_values))
                    if score_values
                    else None,
                    last_audited_at=_latest_activity(submitted_audits),
                )
            )
        return rows

    async def list_auditors(self, *, actor: CurrentUserContext) -> list[AdminAuditorRowResponse]:
        """Return global auditor rows."""

        self._require_admin(actor)
        result = await self._session.execute(
            select(AuditorProfile)
            .options(
                selectinload(AuditorProfile.assignments),
                selectinload(AuditorProfile.audits),
            )
            .order_by(AuditorProfile.created_at.desc())
        )
        profiles = result.scalars().all()
        rows: list[AdminAuditorRowResponse] = []
        for profile in profiles:
            completed_audits = [
                audit for audit in profile.audits if audit.status == AuditStatus.SUBMITTED
            ]
            rows.append(
                AdminAuditorRowResponse(
                    auditor_profile_id=profile.id,
                    account_id=profile.account_id,
                    auditor_code=profile.auditor_code,
                    email_masked=mask_email(profile.email),
                    assignments_count=len(profile.assignments),
                    completed_audits=len(completed_audits),
                    last_active_at=_latest_activity(profile.audits),
                )
            )
        return rows

    async def list_audits(self, *, actor: CurrentUserContext) -> list[AdminAuditRowResponse]:
        """Return global audit rows."""

        self._require_admin(actor)
        result = await self._session.execute(
            select(Audit)
            .options(
                selectinload(Audit.auditor_profile),
                selectinload(Audit.place).selectinload(Place.project).selectinload(Project.account),
            )
            .order_by(Audit.started_at.desc())
        )
        audits = result.scalars().all()
        rows: list[AdminAuditRowResponse] = []
        for audit in audits:
            rows.append(
                AdminAuditRowResponse(
                    audit_id=audit.id,
                    audit_code=audit.audit_code,
                    status=audit.status,
                    account_id=audit.place.project.account_id,
                    account_name=audit.place.project.account.name,
                    project_id=audit.place.project_id,
                    project_name=audit.place.project.name,
                    place_id=audit.place_id,
                    place_name=audit.place.name,
                    auditor_code=audit.auditor_profile.auditor_code,
                    started_at=audit.started_at,
                    submitted_at=audit.submitted_at,
                    summary_score=_round_score(audit.summary_score),
                )
            )
        return rows

    def get_system(self, *, actor: CurrentUserContext) -> AdminSystemResponse:
        """Return system metadata for admin status pages."""

        self._require_admin(actor)
        return AdminSystemResponse(
            instrument_key=INSTRUMENT_KEY,
            instrument_name=INSTRUMENT_NAME,
            instrument_version=INSTRUMENT_VERSION,
            generated_at=datetime.now(timezone.utc),
        )

"""
Playspace dashboard response schemas.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from app.models import AccountType
from app.products.playspace.schemas.base import (
    ApiModel,
    PlaceActivityStatus,
    ProjectStatus,
)

######################################################################################
################################## Dashboard Schemas #################################
######################################################################################


class ManagerProfileResponse(ApiModel):
    """Manager profile visible on manager-facing dashboard surfaces."""

    id: uuid.UUID
    account_id: uuid.UUID
    full_name: str
    email: str
    phone: str | None
    position: str | None
    organization: str | None
    is_primary: bool
    created_at: datetime


class AccountStatsResponse(ApiModel):
    """Top-level account KPIs for the manager dashboard."""

    total_projects: int
    total_places: int
    total_auditors: int
    total_audits_completed: int


class RecentActivityResponse(ApiModel):
    """Recent submitted-audit event displayed on account dashboards."""

    audit_id: uuid.UUID
    audit_code: str
    project_id: uuid.UUID
    project_name: str
    place_id: uuid.UUID
    place_name: str
    completed_at: datetime
    score: float | None


class AccountDetailResponse(ApiModel):
    """Account-level dashboard payload."""

    id: uuid.UUID
    name: str
    email: str
    account_type: AccountType
    created_at: datetime
    primary_manager: ManagerProfileResponse | None
    stats: AccountStatsResponse
    recent_activity: list[RecentActivityResponse]


class ProjectSummaryResponse(ApiModel):
    """Project row used on manager dashboard and project list screens."""

    id: uuid.UUID
    account_id: uuid.UUID
    name: str
    overview: str | None
    place_types: list[str]
    start_date: date | None
    end_date: date | None
    status: ProjectStatus
    places_count: int
    auditors_count: int
    audits_completed: int
    average_score: float | None


class ProjectDetailResponse(ApiModel):
    """Expanded project detail payload."""

    id: uuid.UUID
    account_id: uuid.UUID
    name: str
    overview: str | None
    place_types: list[str]
    start_date: date | None
    end_date: date | None
    est_places: int | None
    est_auditors: int | None
    auditor_description: str | None
    created_at: datetime


class ProjectStatsResponse(ApiModel):
    """Project-level summary stats for dashboard cards."""

    project_id: uuid.UUID
    places_count: int
    places_with_audits: int
    audits_completed: int
    auditors_count: int
    in_progress_audits: int
    average_score: float | None


class AuditorSummaryResponse(ApiModel):
    """Manager-facing auditor summary."""

    id: uuid.UUID
    account_id: uuid.UUID
    auditor_code: str
    full_name: str
    email: str | None
    age_range: str | None
    gender: str | None
    country: str | None
    role: str | None
    assignments_count: int
    completed_audits: int
    last_active_at: datetime | None


class PlaceSummaryResponse(ApiModel):
    """Project-scoped place row with dashboard metrics."""

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    city: str | None
    province: str | None
    country: str | None
    place_type: str | None
    status: PlaceActivityStatus
    audits_completed: int
    average_score: float | None
    last_audited_at: datetime | None

"""
Shared API response schemas for dashboard-oriented endpoints.

These models intentionally describe the shared account/project/place/auditor
surface that both YEE and Playspace can expose under product-specific route
prefixes.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models import AccountType

ProjectStatus = Literal["planned", "active", "completed"]
PlaceActivityStatus = Literal["not_started", "in_progress", "submitted"]
AuditorSignupRequestState = Literal["pending", "approved", "declined"]


class ApiModel(BaseModel):
    """Base model with immutable, attribute-friendly serialization defaults."""

    model_config = ConfigDict(from_attributes=True, frozen=True)


class RequestModel(BaseModel):
    """Base model for write payloads with strict extra-field handling."""

    model_config = ConfigDict(extra="forbid")


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


class CreateAuditorSignupRequestPayload(RequestModel):
    """Public payload used when an auditor requests access to an account."""

    manager_email: str = Field(..., min_length=3, max_length=320)
    full_name: str = Field(..., min_length=1, max_length=200)
    email: str = Field(..., min_length=3, max_length=320)
    note: str | None = Field(default=None, max_length=1000)


class ApproveAuditorSignupRequestPayload(RequestModel):
    """Manager payload used to approve a pending auditor request."""

    project_id: uuid.UUID | None = None
    place_id: uuid.UUID | None = None


class ValidateAuditorCodePayload(RequestModel):
    """Public payload used to validate an auditor code before sign-in."""

    auditor_code: str = Field(..., min_length=1, max_length=50)


class AuditorSignupRequestResponse(ApiModel):
    """Serialized auditor signup request shown on manager dashboards."""

    id: uuid.UUID
    account_id: uuid.UUID
    manager_email: str
    full_name: str
    email: str
    note: str | None
    status: AuditorSignupRequestState
    requested_at: datetime
    reviewed_at: datetime | None
    assigned_project_id: uuid.UUID | None
    assigned_place_id: uuid.UUID | None


class ApprovedAuditorResponse(ApiModel):
    """Auditor details returned after a manager approves a request."""

    auditor_account_id: uuid.UUID
    auditor_profile_id: uuid.UUID
    auditor_code: str
    full_name: str
    assigned_project_id: uuid.UUID | None
    assigned_project_name: str | None
    assigned_place_id: uuid.UUID | None
    assigned_place_name: str | None


class AuditorSignupApprovalResponse(ApiModel):
    """Approval result used to refresh the manager dashboard."""

    request: AuditorSignupRequestResponse
    approved_auditor: ApprovedAuditorResponse


class AuditorCodeLoginResponse(ApiModel):
    """Validated auditor code payload used by the dummy sign-in flow."""

    account_id: uuid.UUID
    auditor_profile_id: uuid.UUID
    auditor_code: str

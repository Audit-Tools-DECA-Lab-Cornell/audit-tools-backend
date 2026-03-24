"""
Manager/admin write-path schemas for Playspace web dashboards.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from pydantic import Field

from app.models import AccountType
from app.products.playspace.schemas.base import ApiModel, RequestModel


class AccountUpdateRequest(RequestModel):
    """Update mutable account fields."""

    name: str | None = None
    email: str | None = None


class AccountManagementResponse(ApiModel):
    """Account payload returned after manager/admin updates."""

    id: uuid.UUID
    name: str
    email_masked: str | None
    account_type: AccountType
    created_at: datetime


class ProjectCreateRequest(RequestModel):
    """Create a project within one account."""

    account_id: uuid.UUID | None = None
    name: str
    overview: str | None = None
    place_types: list[str] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    est_places: int | None = None
    est_auditors: int | None = None
    auditor_description: str | None = None


class ProjectUpdateRequest(RequestModel):
    """Update mutable project fields."""

    name: str | None = None
    overview: str | None = None
    place_types: list[str] | None = None
    start_date: date | None = None
    end_date: date | None = None
    est_places: int | None = None
    est_auditors: int | None = None
    auditor_description: str | None = None


class PlaceCreateRequest(RequestModel):
    """Create a place linked to one or more projects."""

    project_ids: list[uuid.UUID] = Field(default_factory=list)
    name: str
    city: str | None = None
    province: str | None = None
    country: str | None = None
    place_type: str | None = None
    lat: float | None = None
    lng: float | None = None
    start_date: date | None = None
    end_date: date | None = None
    est_auditors: int | None = None
    auditor_description: str | None = None


class PlaceUpdateRequest(RequestModel):
    """Update mutable place fields."""

    project_ids: list[uuid.UUID] | None = None
    name: str | None = None
    city: str | None = None
    province: str | None = None
    country: str | None = None
    place_type: str | None = None
    lat: float | None = None
    lng: float | None = None
    start_date: date | None = None
    end_date: date | None = None
    est_auditors: int | None = None
    auditor_description: str | None = None


class PlaceDetailResponse(ApiModel):
    """Detailed place payload for create/update manager flows."""

    id: uuid.UUID
    project_ids: list[uuid.UUID]
    project_names: list[str]
    name: str
    city: str | None
    province: str | None
    country: str | None
    place_type: str | None
    lat: float | None
    lng: float | None
    start_date: date | None
    end_date: date | None
    est_auditors: int | None
    auditor_description: str | None
    created_at: datetime


class AuditorProfileCreateRequest(RequestModel):
    """Create one auditor account and profile."""

    email: str
    full_name: str
    auditor_code: str
    age_range: str | None = None
    gender: str | None = None
    country: str | None = None
    role: str | None = None


class AuditorProfileUpdateRequest(RequestModel):
    """Update mutable auditor profile fields."""

    email: str | None = None
    full_name: str | None = None
    auditor_code: str | None = None
    age_range: str | None = None
    gender: str | None = None
    country: str | None = None
    role: str | None = None


class AuditorProfileDetailResponse(ApiModel):
    """Auditor profile payload used by manager/admin management screens."""

    id: uuid.UUID
    account_id: uuid.UUID
    auditor_code: str
    email_masked: str | None
    age_range: str | None
    gender: str | None
    country: str | None
    role: str | None
    created_at: datetime

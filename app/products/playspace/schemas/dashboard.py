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
	created_by_user_id: uuid.UUID | None
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


class ManagerPlacesSummaryResponse(ApiModel):
	"""Top-level manager place metrics used by the places dashboard."""

	total_places: int
	submitted_places: int
	in_progress_places: int
	average_score: float | None


class ManagerPlaceRowResponse(ApiModel):
	"""Manager-facing place row with joined project metadata."""

	id: uuid.UUID
	project_id: uuid.UUID
	project_name: str
	name: str
	city: str | None
	province: str | None
	country: str | None
	postal_code: str | None
	address: str | None
	place_type: str | None
	status: PlaceActivityStatus
	audits_completed: int
	average_score: float | None
	last_audited_at: datetime | None


class ManagerPlacesListResponse(ApiModel):
	"""Paginated manager place list plus account-wide summary metrics."""

	items: list[ManagerPlaceRowResponse]
	total_count: int
	page: int
	page_size: int
	total_pages: int
	summary: ManagerPlacesSummaryResponse


class ManagerAuditsSummaryResponse(ApiModel):
	"""Top-level manager audit metrics used by the audits dashboard."""

	total_audits: int
	submitted_audits: int
	in_progress_audits: int
	average_score: float | None


class ManagerAuditRowResponse(ApiModel):
	"""Manager-facing audit row with joined place and project labels."""

	audit_id: uuid.UUID
	audit_code: str
	status: str
	auditor_code: str
	project_id: uuid.UUID
	project_name: str
	place_id: uuid.UUID
	place_name: str
	started_at: datetime
	submitted_at: datetime | None
	summary_score: float | None


class ManagerAuditsListResponse(ApiModel):
	"""Paginated manager audit list plus account-wide summary metrics."""

	items: list[ManagerAuditRowResponse]
	total_count: int
	page: int
	page_size: int
	total_pages: int
	summary: ManagerAuditsSummaryResponse


class PlaceAuditHistoryItemResponse(ApiModel):
	"""One audit history row visible on manager/admin place detail screens."""

	audit_id: uuid.UUID
	audit_code: str
	project_id: uuid.UUID
	project_name: str
	auditor_code: str
	status: str
	started_at: datetime
	submitted_at: datetime | None
	summary_score: float | None


class PlaceHistoryResponse(ApiModel):
	"""Aggregated history for one place and its related audits."""

	place_id: uuid.UUID
	place_name: str
	address: str | None
	city: str | None
	province: str | None
	country: str | None
	postal_code: str | None
	lat: float | None
	lng: float | None
	project_id: uuid.UUID
	project_name: str
	total_audits: int
	submitted_audits: int
	in_progress_audits: int
	average_submitted_score: float | None
	latest_submitted_at: datetime | None
	audits: list[PlaceAuditHistoryItemResponse]


class PlaceSummaryResponse(ApiModel):
	"""Project-scoped place row with dashboard metrics."""

	id: uuid.UUID
	project_id: uuid.UUID
	name: str
	city: str | None
	province: str | None
	country: str | None
	postal_code: str | None
	address: str | None
	place_type: str | None
	status: PlaceActivityStatus
	audits_completed: int
	average_score: float | None
	last_audited_at: datetime | None

"""
Administrator dashboard schemas for Playspace global oversight.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

from app.models import AccountType, AuditStatus
from app.products.playspace.schemas.audit import ScorePairResponse
from app.products.playspace.schemas.base import ApiModel


class AdminOverviewResponse(ApiModel):
	"""Top-level overview metrics for the admin dashboard."""

	total_accounts: int
	total_projects: int
	total_places: int
	total_auditors: int
	total_audits: int
	submitted_audits: int
	in_progress_audits: int


class AdminAccountRowResponse(ApiModel):
	"""Global account row visible to administrators."""

	account_id: uuid.UUID
	name: str
	account_type: AccountType
	email_masked: str | None
	created_at: datetime
	projects_count: int
	places_count: int
	auditors_count: int


class AdminProjectRowResponse(ApiModel):
	"""Global project row visible to administrators."""

	project_id: uuid.UUID
	account_id: uuid.UUID
	account_name: str
	name: str
	start_date: date | None
	end_date: date | None
	places_count: int
	auditors_count: int
	audits_completed: int
	average_score: float | None
	average_scores: ScorePairResponse | None = None


class AdminPlaceRowResponse(ApiModel):
	"""Global place row visible to administrators."""

	place_id: uuid.UUID
	project_id: uuid.UUID
	project_name: str
	account_id: uuid.UUID
	account_name: str
	name: str
	address: str | None
	postal_code: str | None
	city: str | None
	province: str | None
	country: str | None
	audits_completed: int
	average_score: float | None
	last_audited_at: datetime | None
	place_audit_status: str = "not_started"
	place_survey_status: str = "not_started"
	place_audit_count: int = 0
	place_survey_count: int = 0
	audit_mean_scores: ScorePairResponse | None = None
	survey_mean_scores: ScorePairResponse | None = None
	overall_scores: ScorePairResponse | None = None


class AdminAuditorRowResponse(ApiModel):
	"""Global auditor row with privacy-safe identity fields."""

	auditor_profile_id: uuid.UUID
	account_id: uuid.UUID
	auditor_code: str
	email_masked: str | None
	assignments_count: int
	completed_audits: int
	last_active_at: datetime | None


class AdminAuditRowResponse(ApiModel):
	"""Global audit row visible to administrators."""

	audit_id: uuid.UUID
	audit_code: str
	status: AuditStatus
	account_id: uuid.UUID
	account_name: str
	project_id: uuid.UUID
	project_name: str
	place_id: uuid.UUID
	place_name: str
	auditor_code: str
	started_at: datetime
	submitted_at: datetime | None
	summary_score: float | None
	execution_mode: str | None = None
	score_pair: ScorePairResponse | None = None


class AdminSystemResponse(ApiModel):
	"""System-level metadata visible to administrators."""

	instrument_key: str
	instrument_name: str
	instrument_version: str
	generated_at: datetime
	instrument: dict[str, object]


# ── Bulk Export Schemas ──────────────────────────────────────────────────────
# These are richer than the paginated dashboard rows: they include additional
# fields (overview, place_type, lat/lng, split PV/U scores) and are designed
# for flat CSV/Excel serialization. Auditor PII is limited to auditor_code.


class AdminProjectExportRecord(ApiModel):
	"""Single project row for bulk admin data export."""

	project_id: uuid.UUID
	account_id: uuid.UUID
	account_name: str
	name: str
	overview: str | None
	start_date: date | None
	end_date: date | None
	place_types: list[str]
	places_count: int
	auditors_count: int
	audits_completed: int
	average_pv_score: float | None
	average_u_score: float | None


class AdminProjectsExportResponse(ApiModel):
	"""Wrapped export response for projects."""

	entity: Literal["projects"] = "projects"
	generated_at: datetime
	record_count: int
	records: list[AdminProjectExportRecord]


class AdminPlaceExportRecord(ApiModel):
	"""Single place row for bulk admin data export."""

	place_id: uuid.UUID
	project_id: uuid.UUID
	project_name: str
	account_id: uuid.UUID
	account_name: str
	name: str
	address: str | None
	city: str | None
	province: str | None
	country: str | None
	postal_code: str | None
	place_type: str | None
	lat: float | None
	lng: float | None
	place_audit_status: str
	place_survey_status: str
	place_audit_count: int
	place_survey_count: int
	audits_completed: int
	audit_mean_pv: float | None
	audit_mean_u: float | None
	survey_mean_pv: float | None
	survey_mean_u: float | None
	last_audited_at: datetime | None


class AdminPlacesExportResponse(ApiModel):
	"""Wrapped export response for places."""

	entity: Literal["places"] = "places"
	generated_at: datetime
	record_count: int
	records: list[AdminPlaceExportRecord]


class AdminAuditExportRecord(ApiModel):
	"""Single audit row for bulk admin data export. Only auditor_code is exposed."""

	audit_id: uuid.UUID
	audit_code: str
	status: AuditStatus
	execution_mode: str | None
	account_id: uuid.UUID
	account_name: str
	project_id: uuid.UUID
	project_name: str
	place_id: uuid.UUID
	place_name: str
	auditor_code: str
	started_at: datetime
	submitted_at: datetime | None
	summary_score: float | None
	audit_pv_score: float | None
	audit_u_score: float | None
	survey_pv_score: float | None
	survey_u_score: float | None


class AdminAuditsExportResponse(ApiModel):
	"""Wrapped export response for audits or reports (submitted-only audits)."""

	entity: str
	generated_at: datetime
	record_count: int
	records: list[AdminAuditExportRecord]

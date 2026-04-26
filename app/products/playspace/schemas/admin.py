"""
Administrator dashboard schemas for Playspace global oversight.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from app.models import AccountType, AuditStatus
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


class AdminSystemResponse(ApiModel):
	"""System-level metadata visible to administrators."""

	instrument_key: str
	instrument_name: str
	instrument_version: str
	generated_at: datetime
	instrument: dict[str, object]

"""
Manager/admin write-path schemas for Playspace web dashboards.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal

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
	postal_code: str | None = None
	address: str | None = None
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
	postal_code: str | None = None
	address: str | None = None
	place_type: str | None = None
	lat: float | None = None
	lng: float | None = None
	start_date: date | None = None
	end_date: date | None = None
	est_auditors: int | None = None
	auditor_description: str | None = None


class SavedPlaceReportEntry(ApiModel):
	"""One entry in a place's saved_place_reports list."""

	report_type: Literal["combined", "full_assessment"]
	audit_id: uuid.UUID | None = None
	survey_id: uuid.UUID | None = None
	submission_id: uuid.UUID | None = None
	created_at: datetime


class PlaceDetailResponse(ApiModel):
	"""Detailed place payload for create/update manager flows."""

	id: uuid.UUID
	project_ids: list[uuid.UUID]
	project_names: list[str]
	name: str
	city: str | None
	province: str | None
	country: str | None
	postal_code: str | None
	address: str | None
	place_type: str | None
	lat: float | None
	lng: float | None
	start_date: date | None
	end_date: date | None
	est_auditors: int | None
	auditor_description: str | None
	saved_place_reports: list[SavedPlaceReportEntry] = Field(default_factory=list)
	created_at: datetime


class AuditorProfileCreateRequest(RequestModel):
	"""Create one auditor User + profile under a manager's account.

	``account_id`` is only required when the caller is an admin - managers
	automatically use their own account.

	``auditor_code`` is optional. When omitted the backend auto-generates a
	code in the format ``AUD-{ORG}-{YY}-{NNNNNNNN}`` (word initials when the
	account name has no punctuation; otherwise all alphanumeric characters).
	"""

	account_id: uuid.UUID | None = None
	email: str
	full_name: str
	auditor_code: str | None = None
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
	account_id: uuid.UUID | None
	auditor_code: str
	email_masked: str | None
	age_range: str | None
	gender: str | None
	country: str | None
	role: str | None
	created_at: datetime
	temporary_password: str | None = None


######################################################################################
########################### Manager Invite Schemas ###################################
######################################################################################


class ManagerInviteCreateRequest(RequestModel):
	"""Payload to invite a secondary manager to the current account."""

	email: str


class ManagerInviteCreatedResponse(ApiModel):
	"""Response returned when a manager invite is first created."""

	id: uuid.UUID
	email: str
	expires_at: datetime
	invite_url: str
	status: str = "PENDING"


class ManagerInviteListItemResponse(ApiModel):
	"""One invite row returned by the list and resend endpoints.

	``status`` is server-derived:
	- ``ACCEPTED``  - invite has been accepted.
	- ``EXPIRED``   - invite has expired without being accepted.
	- ``PENDING``   - invite is still valid and awaiting acceptance.
	"""

	id: uuid.UUID
	email: str
	status: str
	created_at: datetime
	expires_at: datetime
	accepted_at: datetime | None


######################################################################################
########################### Instrument Management Schemas ############################
######################################################################################


class InstrumentVersionResponse(ApiModel):
	"""Full instrument record from the database."""

	id: uuid.UUID
	instrument_key: str
	instrument_version: str
	parent_instrument_id: uuid.UUID | None = None
	is_active: bool
	content: dict[str, object]
	created_at: datetime
	updated_at: datetime
	submission_count: int = 0
	can_delete: bool = True


class InstrumentCreateRequest(RequestModel):
	"""Payload to create a new instrument version."""

	instrument_key: str
	instrument_version: str
	parent_instrument_id: uuid.UUID | None = None
	content: dict[str, object]


class InstrumentActivateRequest(RequestModel):
	"""Payload to toggle activation status."""

	is_active: bool


######################################################################################
########################### Place Report Schemas #####################################
######################################################################################


class SavePlaceReportRequest(RequestModel):
	"""Save a place report combination to a place."""

	report_type: Literal["combined", "full_assessment"]
	audit_id: uuid.UUID | None = None
	survey_id: uuid.UUID | None = None
	submission_id: uuid.UUID | None = None

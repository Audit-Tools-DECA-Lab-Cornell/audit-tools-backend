"""
Self-service schemas for current-user Playspace endpoints.
"""

from __future__ import annotations

import uuid

from pydantic import Field

from app.products.playspace.schemas.base import ApiModel, RequestModel


class MyAccountResponse(ApiModel):
	"""Current user's identity as seen on the settings / profile screen.

	Fields are resolved from the user's **profile** (ManagerProfile or
	AuditorProfile), not from the Account record — the Account is an
	organisational workspace, not a person.
	"""

	account_id: uuid.UUID
	name: str
	email: str
	account_type: str
	organization: str | None = None


class MyAuditorProfileResponse(ApiModel):
	"""Current user's auditor profile details."""

	profile_id: uuid.UUID
	auditor_code: str
	full_name: str
	email: str | None
	phone: str | None
	age_range: str | None
	gender: str | None
	city: str | None
	province: str | None
	country: str | None
	role: str | None


class ChangePasswordRequest(RequestModel):
	"""Request to change the authenticated user's password."""

	current_password: str = Field(..., min_length=1, max_length=4096)
	new_password: str = Field(..., min_length=8, max_length=4096)


class AuditorProfileSelfUpdateRequest(RequestModel):
	"""Self-service update for mutable auditor profile fields.

	The ``auditor_code`` field is intentionally absent — it is immutable
	from the mobile app.
	"""

	full_name: str | None = None
	email: str | None = None
	phone: str | None = None
	gender: str | None = None
	age_range: str | None = None
	city: str | None = None
	province: str | None = None
	country: str | None = None
	role: str | None = None


class MyManagerProfileResponse(ApiModel):
	"""Current manager user's own profile details."""

	profile_id: uuid.UUID
	full_name: str
	email: str
	phone: str | None
	position: str | None
	organization: str | None
	is_primary: bool


class ManagerProfileSelfUpdateRequest(RequestModel):
	"""Self-service update for mutable manager profile fields.

	``is_primary`` and ``account_id`` are not editable here.
	"""

	full_name: str | None = None
	email: str | None = None
	phone: str | None = None
	position: str | None = None

"""
Self-service schemas for current-user Playspace endpoints.
"""

from __future__ import annotations

import uuid

from app.products.playspace.schemas.base import ApiModel


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
	age_range: str | None
	gender: str | None
	country: str | None
	role: str | None

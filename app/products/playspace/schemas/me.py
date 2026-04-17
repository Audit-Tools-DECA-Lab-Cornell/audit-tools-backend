"""
Self-service schemas for current-user Playspace endpoints.
"""

from __future__ import annotations

import uuid

from app.products.playspace.schemas.base import ApiModel


class MyAccountResponse(ApiModel):
	"""Current user's account details."""

	account_id: uuid.UUID
	name: str
	email: str
	account_type: str


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

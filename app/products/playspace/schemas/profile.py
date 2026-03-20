"""
Playspace profile schemas for self-service read-only endpoints.

Profile editing is deferred until the authentication system is in place and
the manager dashboard (web frontend) editing flows are designed alongside.
"""

from __future__ import annotations

import uuid

from app.products.playspace.schemas.base import ApiModel


class MyAccountResponse(ApiModel):
    """Current user's account details (read-only)."""

    account_id: uuid.UUID
    name: str
    email: str
    account_type: str


class MyAuditorProfileResponse(ApiModel):
    """Current user's auditor profile details (read-only)."""

    profile_id: uuid.UUID
    auditor_code: str
    full_name: str
    email: str | None
    age_range: str | None
    gender: str | None
    country: str | None
    role: str | None

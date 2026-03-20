"""
Dummy current-user resolution and authorization helpers.

These helpers intentionally avoid real authentication so Playspace routes can
be built now and later wired to a proper identity source.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum

from fastapi import HTTPException, Request, status

from app.core.demo_data import DEMO_ACCOUNT_ID


class CurrentUserRole(str, Enum):
    """Roles understood by backend route services."""

    MANAGER = "manager"
    AUDITOR = "auditor"


@dataclass(slots=True)
class CurrentUserContext:
    """Resolved caller identity used by backend services."""

    role: CurrentUserRole
    account_id: uuid.UUID | None
    auditor_code: str | None


def _parse_role(raw_value: str | None) -> CurrentUserRole | None:
    """Normalize a header/cookie role string into a current-user role."""

    if raw_value is None:
        return None

    normalized_value = raw_value.strip().lower()
    if normalized_value == CurrentUserRole.MANAGER.value:
        return CurrentUserRole.MANAGER
    if normalized_value == CurrentUserRole.AUDITOR.value:
        return CurrentUserRole.AUDITOR
    return None


def _parse_uuid(raw_value: str | None) -> uuid.UUID | None:
    """Safely parse a UUID-like string and ignore invalid values."""

    if raw_value is None:
        return None

    candidate = raw_value.strip()
    if not candidate:
        return None

    try:
        return uuid.UUID(candidate)
    except ValueError:
        return None


def resolve_current_user(request: Request, product: str = "playspace") -> CurrentUserContext:
    """
    Resolve a dummy current user from headers/cookies.

    Header overrides are useful for manual API testing while cookie fallbacks let
    the current web scaffold work without extra plumbing.

    ``product`` selects which cookie names are read (e.g. ``{product}_role``),
    so multiple products can coexist without cookie collisions.
    """

    header_role = _parse_role(request.headers.get("x-demo-role"))
    cookie_role = _parse_role(request.cookies.get(f"{product}_role"))
    resolved_role = header_role or cookie_role or CurrentUserRole.MANAGER

    header_account_id = _parse_uuid(request.headers.get("x-demo-account-id"))
    cookie_account_id = _parse_uuid(request.cookies.get(f"{product}_account_id"))

    if resolved_role == CurrentUserRole.MANAGER:
        resolved_account_id = header_account_id or cookie_account_id or DEMO_ACCOUNT_ID
    else:
        resolved_account_id = header_account_id or cookie_account_id

    auditor_code = request.headers.get("x-demo-auditor-code") or request.cookies.get(
        f"{product}_auditor_code",
    )

    return CurrentUserContext(
        role=resolved_role,
        account_id=resolved_account_id,
        auditor_code=auditor_code,
    )


def require_manager_user(current_user: CurrentUserContext) -> CurrentUserContext:
    """Reject non-manager users from manager-only endpoints."""

    if current_user.role != CurrentUserRole.MANAGER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager access is required for this endpoint.",
        )

    return current_user

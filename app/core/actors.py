"""
Dummy actor resolution and simple authorization helpers.

These helpers intentionally avoid real authentication so the shared dashboard
and route layers can be built now and later wired to a proper identity source.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum

from fastapi import HTTPException, Request, status

from app.core.demo_data import DEMO_ACCOUNT_ID


class ActorRole(str, Enum):
    """Roles understood by the shared dashboard services."""

    MANAGER = "manager"
    AUDITOR = "auditor"


@dataclass(slots=True)
class ActorContext:
    """Resolved caller identity used by shared services."""

    role: ActorRole
    account_id: uuid.UUID | None
    auditor_code: str | None


def _parse_role(raw_value: str | None) -> ActorRole | None:
    """Normalize a header/cookie role string into an actor role."""

    if raw_value is None:
        return None

    normalized_value = raw_value.strip().lower()
    if normalized_value == ActorRole.MANAGER.value:
        return ActorRole.MANAGER
    if normalized_value == ActorRole.AUDITOR.value:
        return ActorRole.AUDITOR
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


async def resolve_dummy_actor(request: Request) -> ActorContext:
    """
    Resolve a dummy actor from headers/cookies.

    Header overrides are useful for manual API testing while cookie fallbacks let
    the current web scaffold work without extra plumbing.
    """

    header_role = _parse_role(request.headers.get("x-demo-role"))
    cookie_role = _parse_role(
        request.cookies.get("playspace_role") or request.cookies.get("yee_role"),
    )
    resolved_role = header_role or cookie_role or ActorRole.MANAGER

    header_account_id = _parse_uuid(request.headers.get("x-demo-account-id"))
    cookie_account_id = _parse_uuid(
        request.cookies.get("playspace_account_id") or request.cookies.get("yee_account_id"),
    )

    if resolved_role == ActorRole.MANAGER:
        resolved_account_id = header_account_id or cookie_account_id or DEMO_ACCOUNT_ID
    else:
        resolved_account_id = header_account_id or cookie_account_id

    auditor_code = request.headers.get("x-demo-auditor-code") or request.cookies.get(
        "playspace_auditor_code",
    )

    return ActorContext(
        role=resolved_role,
        account_id=resolved_account_id,
        auditor_code=auditor_code,
    )


def ensure_manager_actor(actor: ActorContext) -> ActorContext:
    """Reject non-manager actors from manager-only shared endpoints."""

    if actor.role != ActorRole.MANAGER:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Manager access is required for this endpoint.",
        )

    return actor

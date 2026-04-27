"""
Current-user context and authorization guards for Playspace route modules.

Authentication is performed by the bearer-token dependency in
``app.products.playspace.routes.dependencies``. These helpers only model
the resolved identity context and enforce role requirements after auth.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from enum import Enum

from fastapi import HTTPException, status


class CurrentUserRole(str, Enum):
	"""Roles understood by backend route services."""

	ADMIN = "admin"
	MANAGER = "manager"
	AUDITOR = "auditor"


@dataclass(slots=True)
class CurrentUserContext:
	"""Resolved caller identity used by backend services."""

	role: CurrentUserRole
	account_id: uuid.UUID | None
	auditor_code: str | None
	user_id: uuid.UUID | None = None


def require_manager_user(current_user: CurrentUserContext) -> CurrentUserContext:
	"""Reject non-manager users from manager-only endpoints."""

	if current_user.role != CurrentUserRole.MANAGER:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="Manager access is required for this endpoint.",
		)

	return current_user


def require_admin_user(current_user: CurrentUserContext) -> CurrentUserContext:
	"""Reject non-admin users from admin-only endpoints."""

	if current_user.role != CurrentUserRole.ADMIN:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="Administrator access is required for this endpoint.",
		)
	return current_user


def require_manager_or_admin_user(
	current_user: CurrentUserContext,
) -> CurrentUserContext:
	"""Allow only manager or admin users."""

	if current_user.role not in {CurrentUserRole.MANAGER, CurrentUserRole.ADMIN}:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="Manager or administrator access is required for this endpoint.",
		)
	return current_user

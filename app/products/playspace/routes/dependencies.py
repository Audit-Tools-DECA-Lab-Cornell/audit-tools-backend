"""
Shared FastAPI dependencies for Playspace route modules.
"""

from __future__ import annotations

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import bearer_scheme, get_current_user
from app.core.actors import CurrentUserContext, CurrentUserRole
from app.database import get_async_session_playspace
from app.models import AccountType, Auditor
from app.products.playspace.services import (
	PlayspaceAdminService,
	PlayspaceAuditService,
	PlayspaceDashboardService,
	PlayspaceManagementService,
)

######################################################################################
############################### Route Dependencies ###################################
######################################################################################

SESSION_DEPENDENCY = Depends(get_async_session_playspace)


def _role_for_account_type(account_type: AccountType) -> CurrentUserRole:
	"""Map a persisted account type onto the Playspace route-role enum."""

	if account_type == AccountType.ADMIN:
		return CurrentUserRole.ADMIN
	if account_type == AccountType.MANAGER:
		return CurrentUserRole.MANAGER
	return CurrentUserRole.AUDITOR


async def _resolve_authenticated_playspace_user(
	credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
	session: AsyncSession = SESSION_DEPENDENCY,
) -> CurrentUserContext:
	"""Resolve Playspace actors from a signed bearer token."""

	user = await get_current_user(credentials=credentials, session=session)
	auditor_code: str | None = None
	if user.account_type == AccountType.AUDITOR:
		result = await session.execute(
			select(Auditor.auditor_code)
			.where((Auditor.user_id == user.id) | (Auditor.account_id == user.account_id))
			.limit(1)
		)
		auditor_code = result.scalar_one_or_none()

	return CurrentUserContext(
		role=_role_for_account_type(user.account_type),
		account_id=user.account_id,
		auditor_code=auditor_code,
	)


CURRENT_USER_DEPENDENCY = Depends(_resolve_authenticated_playspace_user)


def get_dashboard_service(
	session: AsyncSession = SESSION_DEPENDENCY,
) -> PlayspaceDashboardService:
	"""Return a Playspace dashboard service instance for this request."""

	return PlayspaceDashboardService(session=session)


def get_audit_service(
	session: AsyncSession = SESSION_DEPENDENCY,
) -> PlayspaceAuditService:
	"""Return a Playspace audit service instance for this request."""

	return PlayspaceAuditService(session=session)


def get_management_service(
	session: AsyncSession = SESSION_DEPENDENCY,
) -> PlayspaceManagementService:
	"""Return a Playspace manager/admin write service for this request."""

	return PlayspaceManagementService(session=session)


def get_admin_service(
	session: AsyncSession = SESSION_DEPENDENCY,
) -> PlayspaceAdminService:
	"""Return an admin-focused Playspace read service for this request."""

	return PlayspaceAdminService(session=session)


DASHBOARD_SERVICE_DEPENDENCY = Depends(get_dashboard_service)
AUDIT_SERVICE_DEPENDENCY = Depends(get_audit_service)
MANAGEMENT_SERVICE_DEPENDENCY = Depends(get_management_service)
ADMIN_SERVICE_DEPENDENCY = Depends(get_admin_service)

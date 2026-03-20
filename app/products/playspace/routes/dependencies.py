"""
Shared FastAPI dependencies for Playspace route modules.
"""

from __future__ import annotations

from functools import partial

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actors import resolve_current_user
from app.database import get_async_session_playspace
from app.products.playspace.services import PlayspaceAuditService, PlayspaceDashboardService

######################################################################################
############################### Route Dependencies ###################################
######################################################################################

_resolve_playspace_user = partial(resolve_current_user, product="playspace")
CURRENT_USER_DEPENDENCY = Depends(_resolve_playspace_user)
SESSION_DEPENDENCY = Depends(get_async_session_playspace)


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


DASHBOARD_SERVICE_DEPENDENCY = Depends(get_dashboard_service)
AUDIT_SERVICE_DEPENDENCY = Depends(get_audit_service)

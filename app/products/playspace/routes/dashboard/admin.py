"""
Administrator dashboard endpoints for global Playspace oversight.
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from app.core.actors import CurrentUserContext
from app.products.playspace.routes.dependencies import (
	ADMIN_SERVICE_DEPENDENCY,
	CURRENT_USER_DEPENDENCY,
)
from app.products.playspace.schemas import PaginatedResponse
from app.products.playspace.schemas.admin import (
	AdminAccountRowResponse,
	AdminAuditorRowResponse,
	AdminAuditRowResponse,
	AdminOverviewResponse,
	AdminPlaceRowResponse,
	AdminProjectRowResponse,
	AdminSystemResponse,
)
from app.products.playspace.services.admin import PlayspaceAdminService

router = APIRouter(tags=["playspace-admin-dashboard"], prefix="/admin")


@router.get("/overview")
async def get_admin_overview(
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceAdminService = ADMIN_SERVICE_DEPENDENCY,
) -> AdminOverviewResponse:
	"""Return global overview metrics."""

	return await service.get_overview(actor=current_user)


@router.get("/accounts")
async def list_admin_accounts(
	page: int = Query(default=1, ge=1),
	page_size: int = Query(default=10, ge=1, le=100),
	search: str | None = Query(default=None),
	sort: str | None = Query(default=None),
	account_types: list[str] | None = Query(default=None, alias="account_type"),
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceAdminService = ADMIN_SERVICE_DEPENDENCY,
) -> PaginatedResponse[AdminAccountRowResponse]:
	"""Return global account rows."""

	return await service.list_accounts(
		actor=current_user,
		page=page,
		page_size=page_size,
		search=search,
		sort=sort,
		account_types=account_types,
	)


@router.get("/projects")
async def list_admin_projects(
	page: int = Query(default=1, ge=1),
	page_size: int = Query(default=10, ge=1, le=100),
	search: str | None = Query(default=None),
	sort: str | None = Query(default=None),
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceAdminService = ADMIN_SERVICE_DEPENDENCY,
) -> PaginatedResponse[AdminProjectRowResponse]:
	"""Return global project rows."""

	return await service.list_projects(
		actor=current_user,
		page=page,
		page_size=page_size,
		search=search,
		sort=sort,
	)


@router.get("/places")
async def list_admin_places(
	page: int = Query(default=1, ge=1),
	page_size: int = Query(default=10, ge=1, le=100),
	search: str | None = Query(default=None),
	sort: str | None = Query(default=None),
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceAdminService = ADMIN_SERVICE_DEPENDENCY,
) -> PaginatedResponse[AdminPlaceRowResponse]:
	"""Return global place rows."""

	return await service.list_places(
		actor=current_user,
		page=page,
		page_size=page_size,
		search=search,
		sort=sort,
	)


@router.get("/auditors")
async def list_admin_auditors(
	page: int = Query(default=1, ge=1),
	page_size: int = Query(default=10, ge=1, le=100),
	search: str | None = Query(default=None),
	sort: str | None = Query(default=None),
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceAdminService = ADMIN_SERVICE_DEPENDENCY,
) -> PaginatedResponse[AdminAuditorRowResponse]:
	"""Return global auditor rows."""

	return await service.list_auditors(
		actor=current_user,
		page=page,
		page_size=page_size,
		search=search,
		sort=sort,
	)


@router.get("/audits")
async def list_admin_audits(
	page: int = Query(default=1, ge=1),
	page_size: int = Query(default=10, ge=1, le=100),
	search: str | None = Query(default=None),
	sort: str | None = Query(default=None),
	statuses: list[str] | None = Query(default=None, alias="status"),
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceAdminService = ADMIN_SERVICE_DEPENDENCY,
) -> PaginatedResponse[AdminAuditRowResponse]:
	"""Return global audit rows."""

	return await service.list_audits(
		actor=current_user,
		page=page,
		page_size=page_size,
		search=search,
		sort=sort,
		statuses=statuses,
	)


@router.get("/system")
async def get_admin_system(
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceAdminService = ADMIN_SERVICE_DEPENDENCY,
) -> AdminSystemResponse:
	"""Return system metadata for admin dashboards."""

	return await service.get_system(actor=current_user)

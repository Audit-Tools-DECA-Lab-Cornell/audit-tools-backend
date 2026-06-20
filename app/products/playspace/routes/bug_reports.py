"""
Internal bug-reporting and known-issues endpoints.

Any authenticated user may file a report and look up known-issue matches.
Reviewing reports and maintaining the known-issues library are admin-only.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.core.actors import CurrentUserContext, require_admin_user
from app.products.playspace.routes.dependencies import (
	BUG_REPORT_SERVICE_DEPENDENCY,
	CURRENT_USER_DEPENDENCY,
)
from app.products.playspace.schemas import PaginatedResponse
from app.products.playspace.schemas.bug_report import (
	BugReportCreateRequest,
	BugReportListItem,
	BugReportResponse,
	BugReportStatusUpdateRequest,
	KnownIssueCreateRequest,
	KnownIssueMatch,
	KnownIssueResponse,
	KnownIssueUpdateRequest,
	ScreenshotUploadParamsResponse,
)
from app.products.playspace.services import PlayspaceBugReportService

router = APIRouter(tags=["playspace-bug-reports"])


def _require_user_id(current_user: CurrentUserContext) -> uuid.UUID:
	"""Extract the caller's user id or raise 403."""

	if current_user.user_id is None:
		raise HTTPException(
			status_code=status.HTTP_403_FORBIDDEN,
			detail="Authenticated user identity is required to report an issue.",
		)
	return current_user.user_id


######################################################################################
############################## Reporter-facing endpoints #############################
######################################################################################


@router.post("/bug-reports", status_code=status.HTTP_201_CREATED)
async def create_bug_report(
	payload: BugReportCreateRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceBugReportService = BUG_REPORT_SERVICE_DEPENDENCY,
) -> BugReportResponse:
	"""File a new bug report from any client surface."""

	_require_user_id(current_user)
	return await service.create_bug_report(actor=current_user, payload=payload)


@router.get("/bug-reports/mine")
async def list_my_bug_reports(
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceBugReportService = BUG_REPORT_SERVICE_DEPENDENCY,
) -> list[BugReportResponse]:
	"""Return the caller's own bug reports."""

	user_id = _require_user_id(current_user)
	return await service.list_my_bug_reports(user_id=user_id)


@router.get("/bug-reports/screenshot-upload-params")
async def get_screenshot_upload_params(
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceBugReportService = BUG_REPORT_SERVICE_DEPENDENCY,
) -> ScreenshotUploadParamsResponse:
	"""Return signed Cloudinary params for a screenshot upload (auth required)."""

	_require_user_id(current_user)
	params = service.build_screenshot_upload_params()
	if params is None:
		raise HTTPException(
			status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
			detail="Screenshot upload is not configured.",
		)
	return params


@router.get("/known-issues/match")
async def match_known_issues(
	q: str | None = Query(default=None),
	surface: str | None = Query(default=None),
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceBugReportService = BUG_REPORT_SERVICE_DEPENDENCY,
) -> list[KnownIssueMatch]:
	"""Return published known issues matching the reporter's query (deflection)."""

	_require_user_id(current_user)
	return await service.match_known_issues(query=q, surface=surface)


######################################################################################
################################ Admin-only endpoints ################################
######################################################################################


@router.get("/admin/bug-reports")
async def list_bug_reports(
	page: int = Query(default=1, ge=1),
	page_size: int = Query(default=20, ge=1, le=200),
	search: str | None = Query(default=None),
	statuses: list[str] | None = Query(default=None, alias="status"),
	surfaces: list[str] | None = Query(default=None, alias="surface"),
	severities: list[str] | None = Query(default=None, alias="severity"),
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceBugReportService = BUG_REPORT_SERVICE_DEPENDENCY,
) -> PaginatedResponse[BugReportListItem]:
	"""Return a paginated, filtered view of every account's bug reports."""

	require_admin_user(current_user)
	return await service.list_bug_reports_for_admin(
		page=page,
		page_size=page_size,
		search=search,
		statuses=statuses,
		surfaces=surfaces,
		severities=severities,
	)


@router.patch("/admin/bug-reports/{report_id}")
async def update_bug_report(
	report_id: uuid.UUID,
	payload: BugReportStatusUpdateRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceBugReportService = BUG_REPORT_SERVICE_DEPENDENCY,
) -> BugReportResponse:
	"""Apply a triage update (status and/or known-issue link) to a report."""

	require_admin_user(current_user)
	return await service.update_bug_report(report_id=report_id, payload=payload)


@router.get("/admin/known-issues")
async def list_known_issues(
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceBugReportService = BUG_REPORT_SERVICE_DEPENDENCY,
) -> list[KnownIssueResponse]:
	"""Return the full known-issues library for maintenance."""

	require_admin_user(current_user)
	return await service.list_known_issues()


@router.post("/admin/known-issues", status_code=status.HTTP_201_CREATED)
async def create_known_issue(
	payload: KnownIssueCreateRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceBugReportService = BUG_REPORT_SERVICE_DEPENDENCY,
) -> KnownIssueResponse:
	"""Create a known issue."""

	require_admin_user(current_user)
	return await service.create_known_issue(actor=current_user, payload=payload)


@router.patch("/admin/known-issues/{issue_id}")
async def update_known_issue(
	issue_id: uuid.UUID,
	payload: KnownIssueUpdateRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceBugReportService = BUG_REPORT_SERVICE_DEPENDENCY,
) -> KnownIssueResponse:
	"""Update a known issue."""

	require_admin_user(current_user)
	return await service.update_known_issue(issue_id=issue_id, payload=payload)


@router.delete("/admin/known-issues/{issue_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_known_issue(
	issue_id: uuid.UUID,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	service: PlayspaceBugReportService = BUG_REPORT_SERVICE_DEPENDENCY,
) -> Response:
	"""Delete a known issue."""

	require_admin_user(current_user)
	await service.delete_known_issue(issue_id=issue_id)
	return Response(status_code=status.HTTP_204_NO_CONTENT)

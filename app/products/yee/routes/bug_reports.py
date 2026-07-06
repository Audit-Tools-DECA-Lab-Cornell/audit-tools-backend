"""YEE internal bug-reporting endpoints (client-facing slice).

Any authenticated YEE user may file a report, request signed screenshot-upload
params, and look up known-issue matches. Reviewing reports and maintaining the
known-issues library are admin-only and will arrive with the YEE admin dashboard.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_auth_session, get_current_user
from app.models import User
from app.products.yee.schemas.bug_report import (
	BugReportCreateRequest,
	BugReportResponse,
	KnownIssueMatch,
	ScreenshotUploadParamsResponse,
)
from app.products.yee.services.bug_reports import YeeBugReportService

router = APIRouter(tags=["yee-bug-reports"])


@router.post("/bug-reports", status_code=status.HTTP_201_CREATED)
async def create_bug_report(
	payload: BugReportCreateRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> BugReportResponse:
	"""File a new bug report from any YEE client surface."""

	service = YeeBugReportService(session=session)
	return await service.create_bug_report(user=user, payload=payload)


@router.get("/bug-reports/screenshot-upload-params")
async def get_screenshot_upload_params(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> ScreenshotUploadParamsResponse:
	"""Return signed Cloudinary params for a screenshot upload (auth required)."""

	service = YeeBugReportService(session=session)
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
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[KnownIssueMatch]:
	"""Return published known issues matching the reporter's query (deflection)."""

	service = YeeBugReportService(session=session)
	return await service.match_known_issues(query=q, surface=surface)

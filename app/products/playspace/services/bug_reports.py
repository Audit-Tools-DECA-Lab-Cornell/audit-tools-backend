"""
Business logic for the internal bug-reporting and known-issues workflow.

Visibility model:
* Bug reports are private to the reporter's organization (``account_id``); only
  the reporter (``/mine``) and administrators (``/admin/*``) read them.
* Known issues are a single platform-wide library, published entries of which are
  matched for any reporter before they submit. Maintenance is admin-only.
"""

from __future__ import annotations

import hashlib
import math
import os
import time
import uuid

from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actors import CurrentUserContext, CurrentUserRole
from app.models import (
	AuditorAssignment,
	AuditorProfile,
	BugReport,
	BugReportStatus,
	KnownIssue,
	Place,
	PlayspaceSubmission,
	Project,
	ProjectPlace,
	User,
)
from app.products.playspace.schemas.base import PaginatedResponse
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

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 200
MAX_KNOWN_ISSUE_MATCHES = 10
SCREENSHOT_UPLOAD_FOLDER = "bug-reports"


def _total_pages(total_count: int, page_size: int) -> int:
	"""Return a stable page count for paginated responses."""

	if total_count <= 0:
		return 1
	return max(1, math.ceil(total_count / page_size))


def _build_cloudinary_signature(*, folder: str, timestamp: int, api_secret: str) -> str:
	"""Compute a Cloudinary signed-upload signature.

	Cloudinary signs the alphabetically-sorted upload params (excluding ``file``,
	``api_key``, and ``resource_type``) joined as ``key=value&...`` with the API
	secret appended, hashed with SHA-1. Only ``folder`` and ``timestamp`` are
	enforced here, so the client must send exactly those signed params.
	"""

	to_sign = f"folder={folder}&timestamp={timestamp}"
	return hashlib.sha1(f"{to_sign}{api_secret}".encode()).hexdigest()


class PlayspaceBugReportService:
	"""Read/write operations for bug reports and the known-issues library."""

	def __init__(self, *, session: AsyncSession) -> None:
		self._session = session

	def build_screenshot_upload_params(self) -> ScreenshotUploadParamsResponse | None:
		"""Return signed Cloudinary upload params, or ``None`` if not configured.

		The Cloudinary API secret is read from the environment and used only to
		compute the signature - it is never returned to the client. Returns
		``None`` when Cloudinary credentials are absent so the caller can report
		that screenshot upload is unavailable rather than failing.
		"""

		cloud_name = os.getenv("CLOUDINARY_CLOUD_NAME")
		api_key = os.getenv("CLOUDINARY_API_KEY")
		api_secret = os.getenv("CLOUDINARY_API_SECRET")
		if not (cloud_name and api_key and api_secret):
			return None

		timestamp = int(time.time())
		signature = _build_cloudinary_signature(
			folder=SCREENSHOT_UPLOAD_FOLDER,
			timestamp=timestamp,
			api_secret=api_secret,
		)
		return ScreenshotUploadParamsResponse(
			cloud_name=cloud_name,
			api_key=api_key,
			timestamp=timestamp,
			signature=signature,
			folder=SCREENSHOT_UPLOAD_FOLDER,
		)

	######################################################################################
	#################################### Bug Reports #####################################
	######################################################################################

	async def create_bug_report(
		self,
		*,
		actor: CurrentUserContext,
		payload: BugReportCreateRequest,
	) -> BugReportResponse:
		"""Persist a new bug report scoped to the reporter's account.

		Entity references are only kept as foreign keys after verifying they
		belong to the reporter's account; unverified ids are preserved loosely
		inside ``context`` so triage keeps the breadcrumb without trusting the
		client-supplied relationship.
		"""

		context: dict[str, object] = dict(payload.context.model_dump(exclude_none=True))

		project_id = await self._verify_project(payload.project_id, actor, context)
		place_id = await self._verify_place(payload.place_id, actor, context)
		playspace_submission_id = await self._verify_submission(payload.playspace_submission_id, actor, context)

		reporter_email = await self._resolve_reporter_email(actor.user_id)

		report = BugReport(
			account_id=actor.account_id,
			reporter_user_id=actor.user_id,
			reporter_email=reporter_email,
			reporter_role=actor.role.value,
			surface=payload.surface,
			title=payload.title.strip(),
			description=payload.description.strip(),
			severity=payload.severity,
			status=BugReportStatus.NEW,
			project_id=project_id,
			place_id=place_id,
			playspace_submission_id=playspace_submission_id,
			context=context,
			screenshot_url=payload.screenshot_url,
			screenshot_public_id=payload.screenshot_public_id,
		)
		self._session.add(report)
		await self._session.commit()
		await self._session.refresh(report)
		return BugReportResponse.model_validate(report)

	async def list_my_bug_reports(self, *, user_id: uuid.UUID) -> list[BugReportResponse]:
		"""Return the caller's own bug reports, newest first."""

		result = await self._session.execute(
			select(BugReport).where(BugReport.reporter_user_id == user_id).order_by(BugReport.created_at.desc())
		)
		return [BugReportResponse.model_validate(row) for row in result.scalars().all()]

	async def list_bug_reports_for_admin(
		self,
		*,
		page: int = 1,
		page_size: int = DEFAULT_PAGE_SIZE,
		search: str | None = None,
		statuses: list[str] | None = None,
		surfaces: list[str] | None = None,
		severities: list[str] | None = None,
	) -> PaginatedResponse[BugReportListItem]:
		"""Return a paginated, filtered view of every account's bug reports."""

		safe_page_size = max(1, min(page_size, MAX_PAGE_SIZE))
		offset = max(page - 1, 0) * safe_page_size

		filters = []
		if search:
			term = f"%{search.strip()}%"
			filters.append(or_(BugReport.title.ilike(term), BugReport.description.ilike(term)))
		if statuses:
			filters.append(BugReport.status.in_(statuses))
		if surfaces:
			filters.append(BugReport.surface.in_(surfaces))
		if severities:
			filters.append(BugReport.severity.in_(severities))

		count_stmt = select(func.count(BugReport.id))
		rows_stmt = select(BugReport).order_by(BugReport.created_at.desc()).limit(safe_page_size).offset(offset)
		for condition in filters:
			count_stmt = count_stmt.where(condition)
			rows_stmt = rows_stmt.where(condition)

		total_count = int((await self._session.execute(count_stmt)).scalar_one() or 0)
		rows = (await self._session.execute(rows_stmt)).scalars().all()

		return PaginatedResponse[BugReportListItem](
			items=[BugReportListItem.model_validate(row) for row in rows],
			total_count=total_count,
			page=page,
			page_size=safe_page_size,
			total_pages=_total_pages(total_count, safe_page_size),
		)

	async def update_bug_report(
		self,
		*,
		report_id: uuid.UUID,
		payload: BugReportStatusUpdateRequest,
	) -> BugReportResponse:
		"""Apply an admin triage update (status and/or known-issue link)."""

		report = await self._session.get(BugReport, report_id)
		if report is None:
			raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bug report not found.")

		if payload.status is not None:
			report.status = payload.status
		if payload.linked_known_issue_id is not None:
			linked = await self._session.get(KnownIssue, payload.linked_known_issue_id)
			if linked is None:
				raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Known issue not found.")
			report.linked_known_issue_id = payload.linked_known_issue_id

		await self._session.commit()
		await self._session.refresh(report)
		return BugReportResponse.model_validate(report)

	######################################################################################
	#################################### Known Issues ####################################
	######################################################################################

	async def match_known_issues(
		self,
		*,
		query: str | None,
		surface: str | None = None,
	) -> list[KnownIssueMatch]:
		"""Return published known issues matching the reporter's free-text query."""

		stmt = select(KnownIssue).where(KnownIssue.is_published.is_(True))
		if query and query.strip():
			term = f"%{query.strip()}%"
			stmt = stmt.where(or_(KnownIssue.title.ilike(term), KnownIssue.symptoms.ilike(term)))
		if surface:
			stmt = stmt.where(KnownIssue.surfaces.contains([surface]))
		stmt = stmt.order_by(KnownIssue.title.asc()).limit(MAX_KNOWN_ISSUE_MATCHES)

		rows = (await self._session.execute(stmt)).scalars().all()
		return [KnownIssueMatch.model_validate(row) for row in rows]

	async def list_known_issues(self) -> list[KnownIssueResponse]:
		"""Return every known issue for the admin maintenance view."""

		result = await self._session.execute(select(KnownIssue).order_by(KnownIssue.updated_at.desc()))
		return [KnownIssueResponse.model_validate(row) for row in result.scalars().all()]

	async def create_known_issue(
		self,
		*,
		actor: CurrentUserContext,
		payload: KnownIssueCreateRequest,
	) -> KnownIssueResponse:
		"""Create a known issue (admin only)."""

		issue = KnownIssue(
			title=payload.title.strip(),
			symptoms=payload.symptoms.strip(),
			workaround=payload.workaround,
			status=payload.status,
			tags=payload.tags,
			surfaces=payload.surfaces,
			is_published=payload.is_published,
			created_by_user_id=actor.user_id,
		)
		self._session.add(issue)
		await self._session.commit()
		await self._session.refresh(issue)
		return KnownIssueResponse.model_validate(issue)

	async def update_known_issue(
		self,
		*,
		issue_id: uuid.UUID,
		payload: KnownIssueUpdateRequest,
	) -> KnownIssueResponse:
		"""Update a known issue (admin only). Only provided fields change."""

		issue = await self._session.get(KnownIssue, issue_id)
		if issue is None:
			raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Known issue not found.")

		fields = payload.model_dump(exclude_unset=True)
		for key, value in fields.items():
			setattr(issue, key, value)

		await self._session.commit()
		await self._session.refresh(issue)
		return KnownIssueResponse.model_validate(issue)

	async def delete_known_issue(self, *, issue_id: uuid.UUID) -> None:
		"""Delete a known issue (admin only)."""

		issue = await self._session.get(KnownIssue, issue_id)
		if issue is None:
			raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Known issue not found.")
		await self._session.delete(issue)
		await self._session.commit()

	######################################################################################
	###################################### Helpers #######################################
	######################################################################################

	async def _verify_project(
		self,
		project_id: uuid.UUID | None,
		actor: CurrentUserContext,
		context: dict[str, object],
	) -> uuid.UUID | None:
		"""Return ``project_id`` only if the reporter has access to it.

		Access is role-aware: admins may reference any project; managers their own
		account's projects; auditors projects they are assigned to. A reference the
		reporter cannot vouch for is dropped from the FK and kept loosely in
		``context`` so triage retains the breadcrumb without a trusted link.
		"""

		if project_id is None:
			return None
		project = await self._session.get(Project, project_id)
		if project is None:
			return None

		if actor.role == CurrentUserRole.ADMIN:
			return project_id
		if actor.role == CurrentUserRole.MANAGER:
			if project.account_id == actor.account_id:
				return project_id
		elif await self._auditor_has_assignment(actor, project_id=project_id):
			return project_id

		context["unverified_project_id"] = str(project_id)
		return None

	async def _verify_place(
		self,
		place_id: uuid.UUID | None,
		actor: CurrentUserContext,
		context: dict[str, object],
	) -> uuid.UUID | None:
		"""Return ``place_id`` only if the reporter has access to it."""

		if place_id is None:
			return None
		place = await self._session.get(Place, place_id)
		if place is None:
			return None

		if actor.role == CurrentUserRole.ADMIN:
			return place_id
		if actor.role == CurrentUserRole.MANAGER:
			# A place is reachable by a manager if any of their account's projects
			# is linked to it; the simplest sufficient check is an assignment or
			# audit under the account, but managers own places via project links.
			if await self._manager_can_reach_place(actor, place_id):
				return place_id
		elif await self._auditor_has_assignment(actor, place_id=place_id):
			return place_id

		context["unverified_place_id"] = str(place_id)
		return None

	async def _verify_submission(
		self,
		submission_id: uuid.UUID | None,
		actor: CurrentUserContext,
		context: dict[str, object],
	) -> uuid.UUID | None:
		"""Return ``submission_id`` only if the reporter has access to it.

		The Playspace audit a reporter is in is a ``PlayspaceSubmission``; its
		ownership is the submission's auditor (for auditors) or its project's
		account (for managers).
		"""

		if submission_id is None:
			return None
		submission = await self._session.get(PlayspaceSubmission, submission_id)
		if submission is None:
			return None

		if actor.role == CurrentUserRole.ADMIN:
			return submission_id
		if actor.role == CurrentUserRole.MANAGER:
			project = await self._session.get(Project, submission.project_id)
			if project is not None and project.account_id == actor.account_id:
				return submission_id
		else:
			profile_id = await self._auditor_profile_id(actor.user_id)
			if profile_id is not None and submission.auditor_profile_id == profile_id:
				return submission_id

		context["unverified_playspace_submission_id"] = str(submission_id)
		return None

	async def _auditor_profile_id(self, user_id: uuid.UUID | None) -> uuid.UUID | None:
		"""Resolve the auditor profile id for the calling user, if any."""

		if user_id is None:
			return None
		result = await self._session.execute(select(AuditorProfile.id).where(AuditorProfile.user_id == user_id))
		return result.scalar_one_or_none()

	async def _auditor_has_assignment(
		self,
		actor: CurrentUserContext,
		*,
		project_id: uuid.UUID | None = None,
		place_id: uuid.UUID | None = None,
	) -> bool:
		"""Return whether the calling auditor is assigned to the given project/place."""

		profile_id = await self._auditor_profile_id(actor.user_id)
		if profile_id is None:
			return False
		stmt = select(func.count(AuditorAssignment.id)).where(AuditorAssignment.auditor_profile_id == profile_id)
		if project_id is not None:
			stmt = stmt.where(AuditorAssignment.project_id == project_id)
		if place_id is not None:
			stmt = stmt.where(AuditorAssignment.place_id == place_id)
		count = int((await self._session.execute(stmt)).scalar_one() or 0)
		return count > 0

	async def _manager_can_reach_place(self, actor: CurrentUserContext, place_id: uuid.UUID) -> bool:
		"""Return whether the place is linked to one of the manager's account projects."""

		stmt = (
			select(func.count(ProjectPlace.place_id))
			.join(Project, Project.id == ProjectPlace.project_id)
			.where(ProjectPlace.place_id == place_id, Project.account_id == actor.account_id)
		)
		count = int((await self._session.execute(stmt)).scalar_one() or 0)
		return count > 0

	async def _resolve_reporter_email(self, user_id: uuid.UUID | None) -> str | None:
		"""Look up the reporter's email for follow-up, snapshotting it on the report."""

		if user_id is None:
			return None
		result = await self._session.execute(select(User.email).where(User.id == user_id))
		return result.scalar_one_or_none()

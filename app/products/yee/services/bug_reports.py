"""Business logic for the YEE internal bug-reporting and known-issues workflow.

Mirrors ``app/products/playspace/services/bug_reports.py`` for the client-facing
slice (file a report, sign a screenshot upload, match known issues) but operates
on the YEE database and resolves the actor from the raw ``User`` model:

* Role comes from ``user.account_type`` (ADMIN / MANAGER / AUDITOR).
* The audit a reporter is in is a ``YeeAuditSubmission`` (verified by its
  ``auditor_id`` for auditors, or the manager's account for managers).

Known issues are a single platform-wide library; published entries are matched
for any reporter before they submit. Maintenance is admin-only and lives with the
(not-yet-built) YEE admin surface.
"""

from __future__ import annotations

import hashlib
import os
import time
import uuid

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
	AccountType,
	Assignment,
	Auditor,
	BugReport,
	BugReportStatus,
	KnownIssue,
	Place,
	Project,
	ProjectPlace,
	User,
	YeeAuditSubmission,
)
from app.products.yee.schemas.bug_report import (
	BugReportCreateRequest,
	BugReportResponse,
	KnownIssueMatch,
	ScreenshotUploadParamsResponse,
)

MAX_KNOWN_ISSUE_MATCHES = 10
SCREENSHOT_UPLOAD_FOLDER = "bug-reports"


def _build_cloudinary_signature(*, folder: str, timestamp: int, api_secret: str) -> str:
	"""Compute a Cloudinary signed-upload signature.

	Cloudinary signs the alphabetically-sorted upload params (excluding ``file``,
	``api_key``, and ``resource_type``) joined as ``key=value&...`` with the API
	secret appended, hashed with SHA-1. Only ``folder`` and ``timestamp`` are
	enforced here, so the client must send exactly those signed params.
	"""

	to_sign = f"folder={folder}&timestamp={timestamp}"
	return hashlib.sha1(f"{to_sign}{api_secret}".encode()).hexdigest()


class YeeBugReportService:
	"""Read/write operations for YEE bug reports and the known-issues library."""

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
		user: User,
		payload: BugReportCreateRequest,
	) -> BugReportResponse:
		"""Persist a new bug report scoped to the reporter's account.

		Entity references are only kept as foreign keys after verifying they
		belong to the reporter; unverified ids are preserved loosely inside
		``context`` so triage keeps the breadcrumb without trusting the
		client-supplied relationship.
		"""

		context: dict[str, object] = dict(payload.context.model_dump(exclude_none=True))

		project_id = await self._verify_project(payload.project_id, user, context)
		place_id = await self._verify_place(payload.place_id, user, context)
		yee_submission_id = await self._verify_submission(payload.yee_submission_id, user, context)

		report = BugReport(
			account_id=user.account_id,
			reporter_user_id=user.id,
			reporter_email=user.email,
			reporter_role=user.account_type.value.lower(),
			surface=payload.surface,
			title=payload.title.strip(),
			description=payload.description.strip(),
			severity=payload.severity,
			status=BugReportStatus.NEW,
			project_id=project_id,
			place_id=place_id,
			yee_submission_id=yee_submission_id,
			context=context,
			screenshot_url=payload.screenshot_url,
			screenshot_public_id=payload.screenshot_public_id,
		)
		self._session.add(report)
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
			# Match on individual words rather than the raw phrase: a reporter's
			# query like "submit freeze" should find an issue titled "Submit button
			# freeze on mobile", which no single contiguous-substring match would.
			# Each word must appear in the title or symptoms (AND across words, OR
			# across the two fields) so more words narrow the results.
			for word in query.split():
				term = f"%{word}%"
				stmt = stmt.where(or_(KnownIssue.title.ilike(term), KnownIssue.symptoms.ilike(term)))
		if surface:
			stmt = stmt.where(KnownIssue.surfaces.contains([surface]))
		stmt = stmt.order_by(KnownIssue.title.asc()).limit(MAX_KNOWN_ISSUE_MATCHES)

		rows = (await self._session.execute(stmt)).scalars().all()
		return [KnownIssueMatch.model_validate(row) for row in rows]

	######################################################################################
	###################################### Helpers #######################################
	######################################################################################

	async def _verify_project(
		self,
		project_id: uuid.UUID | None,
		user: User,
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

		if user.account_type == AccountType.ADMIN:
			return project_id
		if user.account_type == AccountType.MANAGER:
			if user.account_id is not None and project.account_id == user.account_id:
				return project_id
		if await self._auditor_has_assignment(user, project_id=project_id):
			return project_id

		context["unverified_project_id"] = str(project_id)
		return None

	async def _verify_place(
		self,
		place_id: uuid.UUID | None,
		user: User,
		context: dict[str, object],
	) -> uuid.UUID | None:
		"""Return ``place_id`` only if the reporter has access to it."""

		if place_id is None:
			return None
		place = await self._session.get(Place, place_id)
		if place is None:
			return None

		if user.account_type == AccountType.ADMIN:
			return place_id
		if user.account_type == AccountType.MANAGER:
			if await self._manager_can_reach_place(user, place_id):
				return place_id
		if await self._auditor_has_assignment(user, place_id=place_id):
			return place_id

		context["unverified_place_id"] = str(place_id)
		return None

	async def _verify_submission(
		self,
		submission_id: uuid.UUID | None,
		user: User,
		context: dict[str, object],
	) -> uuid.UUID | None:
		"""Return ``submission_id`` only if the reporter has access to it.

		The YEE audit a reporter is in is a ``YeeAuditSubmission``; its ownership is
		the submission's auditor (for auditors) or its place's account project (for
		managers). Admins may reference any submission.
		"""

		if submission_id is None:
			return None
		submission = await self._session.get(YeeAuditSubmission, submission_id)
		if submission is None:
			return None

		if user.account_type == AccountType.ADMIN:
			return submission_id
		if user.account_type == AccountType.MANAGER:
			if user.account_id is not None and await self._manager_can_reach_place(user, submission.place_id):
				return submission_id
		profile_id = await self._auditor_profile_id(user.id)
		if profile_id is not None and submission.auditor_id == profile_id:
			return submission_id

		context["unverified_yee_submission_id"] = str(submission_id)
		return None

	async def _auditor_profile_id(self, user_id: uuid.UUID | None) -> uuid.UUID | None:
		"""Resolve the auditor profile id for the calling user, if any."""

		if user_id is None:
			return None
		result = await self._session.execute(select(Auditor.id).where(Auditor.user_id == user_id))
		return result.scalar_one_or_none()

	async def _auditor_has_assignment(
		self,
		user: User,
		*,
		project_id: uuid.UUID | None = None,
		place_id: uuid.UUID | None = None,
	) -> bool:
		"""Return whether the calling auditor is assigned to the given project/place."""

		profile_id = await self._auditor_profile_id(user.id)
		if profile_id is None:
			return False
		stmt = select(func.count(Assignment.id)).where(Assignment.auditor_profile_id == profile_id)
		if project_id is not None:
			stmt = stmt.where(Assignment.project_id == project_id)
		if place_id is not None:
			stmt = stmt.where(Assignment.place_id == place_id)
		count = int((await self._session.execute(stmt)).scalar_one() or 0)
		return count > 0

	async def _manager_can_reach_place(self, user: User, place_id: uuid.UUID) -> bool:
		"""Return whether the place is linked to one of the manager's account projects.

		By product invariant a place belongs to exactly one project and a project to
		exactly one account (no shared places / cross-account sharing - see
		SCHEMA.md section 1), so this is equivalent to "the place is in my account."
		"""

		if user.account_id is None:
			return False
		stmt = (
			select(func.count(ProjectPlace.place_id))
			.join(Project, Project.id == ProjectPlace.project_id)
			.where(ProjectPlace.place_id == place_id, Project.account_id == user.account_id)
		)
		count = int((await self._session.execute(stmt)).scalar_one() or 0)
		return count > 0

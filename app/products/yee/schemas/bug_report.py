"""Request/response schemas for the YEE internal bug-reporting workflow.

Mirrors ``app/products/playspace/schemas/bug_report.py`` for the three
client-facing endpoints (create report, screenshot upload params, known-issue
match). The audit reference is ``yee_submission_id`` (a ``yee_audit_submissions``
row) rather than Playspace's ``playspace_submission_id``. Admin/triage schemas
are intentionally omitted until the YEE admin dashboard is built.

The model-layer enums are reused directly as the canonical value sets so the API
contract never drifts from the database.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models import (
	BugReportSeverity,
	BugReportStatus,
	BugReportSurface,
	KnownIssueStatus,
)

JsonDict = dict[str, object]


class ApiModel(BaseModel):
	"""Immutable response model built from ORM attributes."""

	model_config = ConfigDict(from_attributes=True, frozen=True)


class RequestModel(BaseModel):
	"""Strict request model: unknown fields are rejected."""

	model_config = ConfigDict(extra="forbid")


######################################################################################
################################ Diagnostic Context ##################################
######################################################################################


class BugReportContext(RequestModel):
	"""Privacy-filtered diagnostic context captured automatically by clients.

	This is a strict allow-list (``extra="forbid"``): only the fields named here
	are accepted, so clients cannot smuggle audit answers, tokens, or other
	sensitive content into a report. Entity ids are identifiers only - never the
	content behind them.
	"""

	app_version: str | None = None
	build: str | None = None
	route: str | None = None
	screen: str | None = None
	route_params: dict[str, str] | None = None
	platform: str | None = None
	os_version: str | None = None
	device_model: str | None = None
	browser: str | None = None
	user_agent: str | None = None
	viewport_width: int | None = None
	viewport_height: int | None = None
	locale: str | None = None
	network_online: bool | None = None
	network_type: str | None = None
	sync_phase: str | None = None
	project_id: str | None = None
	place_id: str | None = None
	yee_submission_id: str | None = None
	section_id: str | None = None
	question_id: str | None = None
	client_timestamp: str | None = None


######################################################################################
#################################### Bug Reports #####################################
######################################################################################


class BugReportCreateRequest(RequestModel):
	"""Payload a client sends to file a new bug report."""

	surface: BugReportSurface
	title: str = Field(min_length=1, max_length=200)
	description: str = Field(min_length=1, max_length=5000)
	severity: BugReportSeverity
	project_id: uuid.UUID | None = None
	place_id: uuid.UUID | None = None
	yee_submission_id: uuid.UUID | None = None
	context: BugReportContext = Field(default_factory=BugReportContext)
	screenshot_url: str | None = Field(default=None, max_length=2000)
	screenshot_public_id: str | None = Field(default=None, max_length=255)


class BugReportResponse(ApiModel):
	"""Full bug report as returned to its reporter."""

	id: uuid.UUID
	account_id: uuid.UUID | None
	reporter_user_id: uuid.UUID | None
	reporter_email: str | None
	reporter_role: str | None
	surface: BugReportSurface
	title: str
	description: str
	severity: BugReportSeverity
	status: BugReportStatus
	linked_known_issue_id: uuid.UUID | None
	project_id: uuid.UUID | None
	place_id: uuid.UUID | None
	yee_submission_id: uuid.UUID | None
	context: JsonDict
	screenshot_url: str | None
	screenshot_public_id: str | None
	created_at: datetime
	updated_at: datetime


class ScreenshotUploadParamsResponse(ApiModel):
	"""Signed parameters a client uses to upload a screenshot to Cloudinary.

	The signature is computed server-side with the Cloudinary API secret (which
	never leaves the backend), so clients perform an authenticated signed upload
	without an unsigned upload preset.
	"""

	cloud_name: str
	api_key: str
	timestamp: int
	signature: str
	folder: str


######################################################################################
#################################### Known Issues ####################################
######################################################################################


class KnownIssueMatch(ApiModel):
	"""A published known issue surfaced to a reporter before they submit."""

	id: uuid.UUID
	title: str
	symptoms: str
	workaround: str | None
	status: KnownIssueStatus
	tags: list[str]
	surfaces: list[str]

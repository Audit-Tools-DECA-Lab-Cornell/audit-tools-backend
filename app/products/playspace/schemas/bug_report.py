"""
Request/response schemas for the internal bug-reporting workflow.

The model-layer enums (``BugReportSurface``/``BugReportSeverity``/
``BugReportStatus``/``KnownIssueStatus``) are reused directly as the canonical
value sets so the API contract never drifts from the database.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from app.models import (
	BugReportSeverity,
	BugReportStatus,
	BugReportSurface,
	KnownIssueStatus,
)
from app.products.playspace.schemas.base import ApiModel, JsonDict, RequestModel

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
	playspace_submission_id: str | None = None
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
	playspace_submission_id: uuid.UUID | None = None
	context: BugReportContext = Field(default_factory=BugReportContext)
	screenshot_url: str | None = Field(default=None, max_length=2000)
	screenshot_public_id: str | None = Field(default=None, max_length=255)


class BugReportResponse(ApiModel):
	"""Full bug report as returned to its reporter and to administrators."""

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
	playspace_submission_id: uuid.UUID | None
	context: JsonDict
	screenshot_url: str | None
	screenshot_public_id: str | None
	created_at: datetime
	updated_at: datetime


class BugReportListItem(ApiModel):
	"""Trimmed bug report row for the admin review table."""

	id: uuid.UUID
	account_id: uuid.UUID | None
	reporter_email: str | None
	reporter_role: str | None
	surface: BugReportSurface
	title: str
	severity: BugReportSeverity
	status: BugReportStatus
	linked_known_issue_id: uuid.UUID | None
	project_id: uuid.UUID | None
	place_id: uuid.UUID | None
	playspace_submission_id: uuid.UUID | None
	screenshot_url: str | None
	created_at: datetime
	updated_at: datetime


class BugReportStatusUpdateRequest(RequestModel):
	"""Admin triage update for a bug report."""

	status: BugReportStatus | None = None
	linked_known_issue_id: uuid.UUID | None = None


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


class KnownIssueResponse(ApiModel):
	"""Full known issue as managed by administrators."""

	id: uuid.UUID
	title: str
	symptoms: str
	workaround: str | None
	status: KnownIssueStatus
	tags: list[str]
	surfaces: list[str]
	is_published: bool
	created_at: datetime
	updated_at: datetime


class KnownIssueCreateRequest(RequestModel):
	"""Admin payload to create a known issue."""

	title: str = Field(min_length=1, max_length=200)
	symptoms: str = Field(min_length=1, max_length=5000)
	workaround: str | None = Field(default=None, max_length=5000)
	status: KnownIssueStatus = KnownIssueStatus.OPEN
	tags: list[str] = Field(default_factory=list)
	surfaces: list[str] = Field(default_factory=list)
	is_published: bool = False


class KnownIssueUpdateRequest(RequestModel):
	"""Admin payload to update a known issue. All fields optional."""

	title: str | None = Field(default=None, min_length=1, max_length=200)
	symptoms: str | None = Field(default=None, min_length=1, max_length=5000)
	workaround: str | None = Field(default=None, max_length=5000)
	status: KnownIssueStatus | None = None
	tags: list[str] | None = None
	surfaces: list[str] | None = None
	is_published: bool | None = None

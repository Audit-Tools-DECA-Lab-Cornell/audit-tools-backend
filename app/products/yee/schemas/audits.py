"""YEE audit request/response schemas.

Submit, draft, score, audit-state, and list/detail models for the YEE
auditor-facing audit lifecycle.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class SubmitYeeAuditRequest(BaseModel):
	"""
	YEE audit submission payload.

	`responses` format:
	- Single-choice item: {"QID22": "3"}
	- Matrix-like item: {"QID1#2": {"1": "3", "2": "2"}}
	"""

	place_id: uuid.UUID
	participant_info: dict[str, Any] = Field(default_factory=dict)
	responses: dict[str, Any] = Field(default_factory=dict)
	# Optional client-generated key. A queued offline submit replays with the
	# same key after an ambiguous network failure; the server then returns the
	# already-stored submission instead of a 409, so no completed audit is lost.
	idempotency_key: str | None = Field(default=None, max_length=64)


class SaveYeeDraftRequest(BaseModel):
	participant_info: dict[str, Any] = Field(default_factory=dict)
	responses: dict[str, Any] = Field(default_factory=dict)


class ScoreResult(BaseModel):
	total_score: int
	section_scores: dict[str, int]
	category_scores: dict[str, int]
	matched_scored_answers: int


class YeeAuditSubmissionResponse(BaseModel):
	id: uuid.UUID
	place_id: uuid.UUID
	place_name: str | None = None
	auditor_id: uuid.UUID
	auditor_generated_id: str | None = None
	submitted_at: datetime
	participant_info: dict[str, Any]
	responses: dict[str, Any]
	score: ScoreResult


class YeeAuditStateResponse(BaseModel):
	audit_id: uuid.UUID | None = None
	submission_id: uuid.UUID | None = None
	place_id: uuid.UUID
	place_name: str
	auditor_generated_id: str
	status: str
	submitted_at: datetime | None = None
	participant_info: dict[str, Any] = Field(default_factory=dict)
	responses: dict[str, Any] = Field(default_factory=dict)
	score: ScoreResult | None = None


class MyYeeAuditItem(BaseModel):
	id: uuid.UUID
	place_id: uuid.UUID
	place_name: str
	submitted_at: datetime
	total_score: int

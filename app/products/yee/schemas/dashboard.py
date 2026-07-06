"""YEE-only dashboard request/response schemas.

YEE manager/admin reporting and audit edit/re-submit contracts. The shared
top-level `app/dashboard_router.py` imports these for its route signatures.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.products.yee.schemas.audits import CanonicalScoreSnapshot, flatten_canonical_score


class DashboardScoreResult(BaseModel):
	total_score: int
	section_scores: dict[str, int]
	category_scores: dict[str, int]
	matched_scored_answers: int
	canonical_score: CanonicalScoreSnapshot
	total_raw_score: int = 0
	total_raw_maximum: int = 0
	raw_domain_scores: dict[str, int] = Field(default_factory=dict)
	raw_domain_maximums: dict[str, int] = Field(default_factory=dict)
	total_weighted_score: float = 0.0
	total_weighted_maximum: float = 0.0
	weighted_domain_scores: dict[str, float] = Field(default_factory=dict)
	weighted_domain_maximums: dict[str, float] = Field(default_factory=dict)
	selected_weights: dict[str, int] = Field(default_factory=dict)
	normalized_weights: dict[str, float] = Field(default_factory=dict)
	priority_gaps: dict[str, float] = Field(default_factory=dict)

	@model_validator(mode="before")
	@classmethod
	def fill_flattened_score_fields(cls, data: Any) -> Any:
		if not isinstance(data, dict):
			return data
		canonical_score = data.get("canonical_score")
		if canonical_score is None:
			return data
		return {**flatten_canonical_score(canonical_score), **data}


class ManagerAuditEditState(BaseModel):
	audit_id: str
	submission_id: str | None = None
	place_id: str
	place_name: str | None = None
	auditor_id: str
	auditor_generated_id: str | None = None
	submitted_at: str | None = None
	participant_info: dict[str, Any] = Field(default_factory=dict)
	responses: dict[str, Any] = Field(default_factory=dict)
	score: DashboardScoreResult


class ManagerAuditEditRequest(BaseModel):
	submission_id: str | None = None
	participant_info: dict[str, Any] = Field(default_factory=dict)
	responses: dict[str, Any] = Field(default_factory=dict)
	resubmit: bool = False


class PlaceComparisonAuditItem(BaseModel):
	audit_id: str
	auditor_id: str
	place_id: str
	place_name: str
	project_id: str
	project_name: str
	date: str
	total_raw_score: int
	total_raw_maximum: int
	total_weighted_score: float
	total_weighted_maximum: float
	domain_weights: dict[str, int]
	raw_domain_scores: dict[str, int]
	raw_domain_maximums: dict[str, int]
	weighted_domain_scores: dict[str, float]
	weighted_domain_maximums: dict[str, float]
	canonical_score: CanonicalScoreSnapshot


class PlaceComparisonGroup(BaseModel):
	place_id: str
	place_name: str
	project_id: str
	project_name: str
	audits: list[PlaceComparisonAuditItem]


class RawDataExportRow(BaseModel):
	audit_id: str
	auditor_generated_id: str
	organization: str
	place_id: str
	place_name: str
	project_id: str
	project_name: str
	date: str
	submitted_at: str
	start_time: str
	finish_time: str
	total_minutes: int
	visit_frequency: str
	season: str
	weather: str
	comments: str
	raw_access: int
	raw_activity_spaces: int
	raw_amenities: int
	raw_experience_of_space: int
	raw_aesthetics_and_care: int
	raw_use_and_usability: int
	weighted_access: float
	weighted_activity_spaces: float
	weighted_amenities: float
	weighted_experience_of_space: float
	weighted_aesthetics_and_care: float
	weighted_use_and_usability: float
	total_raw_score: int
	total_raw_maximum: int
	total_weighted_score: float
	total_weighted_maximum: float
	domain_weights: dict[str, int]
	responses: dict[str, str]
	canonical_score: CanonicalScoreSnapshot

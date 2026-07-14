"""YEE audit request/response schemas.

Submit, draft, score, audit-state, and list/detail models for the YEE
auditor-facing audit lifecycle.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, model_validator

RAW_DOMAIN_MAXIMUMS = {
	"access": 14,
	"activitySpaces": 26,
	"amenities": 23,
	"experienceOfSpace": 20,
	"aestheticsAndCare": 24,
	"useAndUsability": 18,
}


def _round_2(value: float) -> float:
	return round(value + 1e-9, 2)


def _canonical_dict(value: Any) -> dict[str, Any] | None:
	if isinstance(value, CanonicalScoreSnapshot):
		return value.model_dump(mode="python")
	if isinstance(value, dict):
		return value
	return None


def flatten_canonical_score(value: Any) -> dict[str, Any]:
	canonical_score = _canonical_dict(value)
	if canonical_score is None:
		return {}

	raw = canonical_score.get("raw")
	weighted = canonical_score.get("weighted")
	meta = canonical_score.get("meta")
	if not isinstance(raw, dict) or not isinstance(weighted, dict) or not isinstance(meta, dict):
		return {}

	domain_scores = raw.get("domain_scores")
	domain_order = meta.get("domain_order")
	domain_item_counts = meta.get("domain_item_counts")
	domain_max_average_scores = meta.get("domain_max_average_scores")
	weighted_domain_scores = weighted.get("weighted_domain_scores")
	normalized_weights = weighted.get("normalized_domain_weights")
	if (
		not isinstance(domain_scores, dict)
		or not isinstance(domain_order, list)
		or not isinstance(domain_item_counts, dict)
		or not isinstance(domain_max_average_scores, dict)
		or not isinstance(weighted_domain_scores, dict)
		or not isinstance(normalized_weights, dict)
	):
		return {}

	raw_domain_maximums = {str(domain): RAW_DOMAIN_MAXIMUMS.get(str(domain), 0) for domain in domain_order}
	weighted_domain_maximums = {
		str(domain): _round_2(
			float(domain_max_average_scores.get(domain, 0.0)) * float(normalized_weights.get(domain, 0.0))
		)
		for domain in domain_order
	}
	return {
		"total_raw_score": int(raw.get("total_score", 0)),
		"total_raw_maximum": sum(raw_domain_maximums.values()),
		"raw_domain_scores": {str(key): int(value) for key, value in domain_scores.items()},
		"raw_domain_maximums": raw_domain_maximums,
		"total_weighted_score": float(weighted.get("total_weighted_score", 0.0)),
		"total_weighted_maximum": _round_2(sum(weighted_domain_maximums.values())),
		"weighted_domain_scores": {str(key): float(value) for key, value in weighted_domain_scores.items()},
		"weighted_domain_maximums": weighted_domain_maximums,
		"selected_weights": {
			str(key): int(value) for key, value in dict(weighted.get("raw_domain_weights", {})).items()
		},
		"normalized_weights": {str(key): float(value) for key, value in normalized_weights.items()},
		"priority_gaps": {str(key): float(value) for key, value in dict(weighted.get("priority_gaps", {})).items()},
	}


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


class RawScoreSnapshot(BaseModel):
	total_score: int
	domain_scores: dict[str, int]
	section_scores: dict[str, int]
	category_scores: dict[str, int]
	item_scores: dict[str, int]
	matched_scored_answers: int


class WeightedScoreSnapshot(BaseModel):
	raw_domain_weights: dict[str, int]
	normalized_domain_weights: dict[str, float]
	domain_average_scores: dict[str, float]
	weighted_domain_scores: dict[str, float]
	total_weighted_score: float
	priority_gaps: dict[str, float]


class ScoreMetaSnapshot(BaseModel):
	domain_order: list[str]
	domain_item_counts: dict[str, int]
	domain_max_average_scores: dict[str, float]


class CanonicalScoreSnapshot(BaseModel):
	scoring_version: str
	raw: RawScoreSnapshot
	weighted: WeightedScoreSnapshot
	meta: ScoreMetaSnapshot


class ScoreResult(BaseModel):
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
	participant_id: str | None = None

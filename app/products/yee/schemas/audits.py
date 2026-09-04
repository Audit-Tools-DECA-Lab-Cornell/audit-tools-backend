"""YEE audit request/response schemas.

Submit, draft, score, audit-state, and list/detail models for the YEE
auditor-facing audit lifecycle.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from math import isfinite
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


def _round_2(value: float) -> float:
	return round(value + 1e-9, 2)


def _snapshot_domain_order(meta: dict[str, Any]) -> list[str] | None:
	domain_order = meta.get("domain_order")
	if not isinstance(domain_order, list) or not all(isinstance(domain, str) for domain in domain_order):
		return None
	return domain_order


def _derive_legacy_raw_domain_maximums(
	meta: dict[str, Any],
	domain_order: list[str],
) -> dict[str, int] | None:
	"""Recover integer maxima using only metadata frozen in an old snapshot."""

	item_counts = meta.get("domain_item_counts")
	max_average_scores = meta.get("domain_max_average_scores")
	if not isinstance(item_counts, dict) or not isinstance(max_average_scores, dict):
		return None

	maximums: dict[str, int] = {}
	for domain in domain_order:
		item_count = item_counts.get(domain)
		max_average = max_average_scores.get(domain)
		if (
			isinstance(item_count, bool)
			or not isinstance(item_count, int)
			or item_count < 0
			or isinstance(max_average, bool)
			or not isinstance(max_average, (int, float))
			or not isfinite(float(max_average))
			or float(max_average) < 0
		):
			return None
		# Contract item maxima are integers. The historical snapshot retained
		# their item count and two-decimal average, so nearest-integer recovery
		# restores values such as 2.33 * 6 -> 14 and 1.88 * 8 -> 15.
		maximums[domain] = max(0, int(round(float(max_average) * item_count)))
	return maximums


def _validated_stored_raw_maximums(
	value: Any,
	meta: dict[str, Any],
	domain_order: list[str],
) -> dict[str, int] | None:
	if not isinstance(value, dict) or set(value) != set(domain_order):
		return None
	item_counts = meta.get("domain_item_counts")
	max_average_scores = meta.get("domain_max_average_scores")
	if not isinstance(item_counts, dict) or not isinstance(max_average_scores, dict):
		return None

	validated: dict[str, int] = {}
	for domain in domain_order:
		maximum = value.get(domain)
		item_count = item_counts.get(domain)
		max_average = max_average_scores.get(domain)
		if (
			isinstance(maximum, bool)
			or not isinstance(maximum, int)
			or maximum < 0
			or isinstance(item_count, bool)
			or not isinstance(item_count, int)
			or item_count < 0
			or isinstance(max_average, bool)
			or not isinstance(max_average, (int, float))
			or not isfinite(float(max_average))
			or (
				(maximum != 0 or float(max_average) != 0.0)
				if item_count == 0
				else _round_2(maximum / item_count) != float(max_average)
			)
		):
			return None
		validated[domain] = maximum
	return validated


def _stored_weighted_maximums_are_valid(
	value: Any,
	derived_maximums: dict[str, float],
	domain_order: list[str],
) -> bool:
	if not isinstance(value, dict) or set(value) != set(domain_order):
		return False
	for domain in domain_order:
		maximum = value.get(domain)
		if (
			isinstance(maximum, bool)
			or not isinstance(maximum, (int, float))
			or not isfinite(float(maximum))
			or float(maximum) < 0
			or float(maximum) != derived_maximums[domain]
		):
			return False
	return True


def _stored_raw_total_is_valid(value: Any, derived_total: int) -> bool:
	return not isinstance(value, bool) and isinstance(value, int) and value >= 0 and value == derived_total


def _stored_weighted_total_is_valid(value: Any, derived_total: float) -> bool:
	return (
		not isinstance(value, bool)
		and isinstance(value, (int, float))
		and isfinite(float(value))
		and float(value) >= 0
		and float(value) == derived_total
	)


def _derive_weighted_maximums(
	raw_domain_maximums: dict[str, int],
	weighted: dict[str, Any],
	meta: dict[str, Any],
	domain_order: list[str],
) -> tuple[dict[str, float], float] | None:
	"""Use frozen raw weights and item counts, never an active instrument."""

	item_counts = meta.get("domain_item_counts")
	raw_weights = weighted.get("raw_domain_weights")
	if not isinstance(item_counts, dict) or not isinstance(raw_weights, dict):
		return None

	weights: dict[str, int] = {}
	for domain in domain_order:
		item_count = item_counts.get(domain)
		raw_weight = raw_weights.get(domain)
		if (
			isinstance(item_count, bool)
			or not isinstance(item_count, int)
			or item_count < 0
			or isinstance(raw_weight, bool)
			or not isinstance(raw_weight, int)
			or raw_weight < 0
		):
			return None
		weights[domain] = raw_weight

	total_weight = sum(weights.values())
	if total_weight <= 0:
		return ({domain: 0.0 for domain in domain_order}, 0.0)

	exact_maximums = {
		domain: (
			(raw_domain_maximums[domain] / item_counts[domain]) * (weights[domain] / total_weight)
			if item_counts[domain]
			else 0.0
		)
		for domain in domain_order
	}
	return (
		{domain: _round_2(exact_maximums[domain]) for domain in domain_order},
		_round_2(sum(exact_maximums.values())),
	)


def _backfill_canonical_maxima(data: Any) -> Any:
	"""Validate or rebuild maxima using only the frozen canonical snapshot."""

	if not isinstance(data, dict):
		return data
	raw = data.get("raw")
	weighted = data.get("weighted")
	meta = data.get("meta")
	if not isinstance(raw, dict) or not isinstance(weighted, dict) or not isinstance(meta, dict):
		return data

	domain_order = _snapshot_domain_order(meta)
	if domain_order is None:
		return data
	derived_raw_domain_maximums = _derive_legacy_raw_domain_maximums(meta, domain_order)
	if derived_raw_domain_maximums is None:
		return data

	raw_copy = dict(raw)
	stored_raw_domain_maximums = _validated_stored_raw_maximums(raw_copy.get("domain_maximums"), meta, domain_order)
	if stored_raw_domain_maximums is None:
		raw_copy["domain_maximums"] = derived_raw_domain_maximums
		effective_raw_domain_maximums = derived_raw_domain_maximums
	else:
		effective_raw_domain_maximums = stored_raw_domain_maximums
	effective_total_raw_maximum = sum(effective_raw_domain_maximums.values())
	if not _stored_raw_total_is_valid(raw_copy.get("total_maximum"), effective_total_raw_maximum):
		raw_copy["total_maximum"] = effective_total_raw_maximum

	weighted_maximums = _derive_weighted_maximums(
		effective_raw_domain_maximums,
		weighted,
		meta,
		domain_order,
	)
	if weighted_maximums is None:
		return data
	derived_weighted_domain_maximums, derived_total_weighted_maximum = weighted_maximums

	weighted_copy = dict(weighted)
	if not _stored_weighted_maximums_are_valid(
		weighted_copy.get("domain_maximums"),
		derived_weighted_domain_maximums,
		domain_order,
	):
		weighted_copy["domain_maximums"] = derived_weighted_domain_maximums
	if not _stored_weighted_total_is_valid(
		weighted_copy.get("total_maximum"),
		derived_total_weighted_maximum,
	):
		weighted_copy["total_maximum"] = derived_total_weighted_maximum

	return {**data, "raw": raw_copy, "weighted": weighted_copy}


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
	canonical_score = _backfill_canonical_maxima(canonical_score)
	if not isinstance(canonical_score, dict):
		return {}

	raw = canonical_score.get("raw")
	weighted = canonical_score.get("weighted")
	meta = canonical_score.get("meta")
	if not isinstance(raw, dict) or not isinstance(weighted, dict) or not isinstance(meta, dict):
		return {}

	domain_scores = raw.get("domain_scores")
	domain_order = meta.get("domain_order")
	raw_domain_maximums = raw.get("domain_maximums")
	weighted_domain_scores = weighted.get("weighted_domain_scores")
	weighted_domain_maximums = weighted.get("domain_maximums")
	normalized_weights = weighted.get("normalized_domain_weights")
	if (
		not isinstance(domain_scores, dict)
		or not isinstance(domain_order, list)
		or not isinstance(raw_domain_maximums, dict)
		or not isinstance(weighted_domain_scores, dict)
		or not isinstance(weighted_domain_maximums, dict)
		or not isinstance(normalized_weights, dict)
	):
		return {}

	return {
		"total_raw_score": int(raw.get("total_score", 0)),
		"total_raw_maximum": int(raw.get("total_maximum", 0)),
		"raw_domain_scores": {str(key): int(value) for key, value in domain_scores.items()},
		"raw_domain_maximums": {str(key): int(value) for key, value in raw_domain_maximums.items()},
		"total_weighted_score": float(weighted.get("total_weighted_score", 0.0)),
		"total_weighted_maximum": float(weighted.get("total_maximum", 0.0)),
		"weighted_domain_scores": {str(key): float(value) for key, value in weighted_domain_scores.items()},
		"weighted_domain_maximums": {str(key): float(value) for key, value in weighted_domain_maximums.items()},
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
	instrument_key: str | None = Field(default=None, max_length=80)
	instrument_version: str | None = Field(default=None, max_length=50)

	@model_validator(mode="after")
	def validate_instrument_stamp(self) -> SubmitYeeAuditRequest:
		if (self.instrument_key is None) != (self.instrument_version is None):
			raise ValueError("instrument_key and instrument_version must be provided together")
		return self


class SaveYeeDraftRequest(BaseModel):
	participant_info: dict[str, Any] = Field(default_factory=dict)
	responses: dict[str, Any] = Field(default_factory=dict)
	instrument_key: str | None = Field(default=None, max_length=80)
	instrument_version: str | None = Field(default=None, max_length=50)

	@model_validator(mode="after")
	def validate_instrument_stamp(self) -> SaveYeeDraftRequest:
		if (self.instrument_key is None) != (self.instrument_version is None):
			raise ValueError("instrument_key and instrument_version must be provided together")
		return self


class RawScoreSnapshot(BaseModel):
	total_score: int
	total_maximum: int
	domain_scores: dict[str, int]
	domain_maximums: dict[str, int]
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
	domain_maximums: dict[str, float]
	total_maximum: float
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

	@model_validator(mode="before")
	@classmethod
	def fill_snapshot_maxima(cls, data: Any) -> Any:
		return _backfill_canonical_maxima(data)


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
		return {**data, **flatten_canonical_score(canonical_score)}


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
	instrument_key: str | None = None
	instrument_version: str | None = None


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
	instrument_key: str | None = None
	instrument_version: str | None = None


class MyYeeAuditItem(BaseModel):
	id: uuid.UUID
	place_id: uuid.UUID
	place_name: str
	submitted_at: datetime
	total_score: int
	total_raw_maximum: int | None = None
	total_weighted_maximum: float | None = None
	participant_id: str | None = None
	instrument_key: str | None = None
	instrument_version: str | None = None


class IncompleteAuditResponsesDetail(BaseModel):
	"""Body of a `422` rejecting a final submit for missing required answers.

	Question ids only. This travels into client logs and error surfaces, so it
	deliberately carries no question text and no response values — the client
	looks the wording up in its own cached instrument.
	"""

	code: Literal["incomplete_audit_responses"] = "incomplete_audit_responses"
	message: str
	missing_primary_question_ids: list[str] = Field(default_factory=list)
	missing_follow_up_question_ids: list[str] = Field(default_factory=list)

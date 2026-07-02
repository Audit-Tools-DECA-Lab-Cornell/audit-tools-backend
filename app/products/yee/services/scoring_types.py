from __future__ import annotations

from typing import TypeAlias, TypedDict

JsonScalar: TypeAlias = str | int | float | bool | None
JsonValue: TypeAlias = JsonScalar | list["JsonValue"] | dict[str, "JsonValue"]
JsonMap: TypeAlias = dict[str, JsonValue]
ResponseMap: TypeAlias = dict[str, JsonValue]


class RawScoreSnapshot(TypedDict):
	total_score: int
	domain_scores: dict[str, int]
	section_scores: dict[str, int]
	category_scores: dict[str, int]
	item_scores: dict[str, int]
	matched_scored_answers: int


class WeightedScoreSnapshot(TypedDict):
	raw_domain_weights: dict[str, int]
	normalized_domain_weights: dict[str, float]
	domain_average_scores: dict[str, float]
	weighted_domain_scores: dict[str, float]
	total_weighted_score: float
	priority_gaps: dict[str, float]


class ScoreMetaSnapshot(TypedDict):
	domain_order: list[str]
	domain_item_counts: dict[str, int]
	domain_max_average_scores: dict[str, float]


class CanonicalScoreSnapshot(TypedDict):
	scoring_version: str
	raw: RawScoreSnapshot
	weighted: WeightedScoreSnapshot
	meta: ScoreMetaSnapshot


class LegacyScoreResult(TypedDict):
	total_score: int
	section_scores: dict[str, int]
	category_scores: dict[str, int]
	matched_scored_answers: int
	canonical_score: CanonicalScoreSnapshot

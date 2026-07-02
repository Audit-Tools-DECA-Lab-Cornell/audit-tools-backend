from __future__ import annotations

from collections.abc import Mapping
from typing import assert_never

from app.products.yee.services.scoring_spec import (
	CATEGORY_BY_DOMAIN,
	CONDITION_SCORES,
	DOMAIN_ORDER,
	ITEM_SPECS,
	SCORING_VERSION,
	SECTION_BY_DOMAIN,
	AnswerScore,
	PairedItemSpec,
	PresenceItemSpec,
)
from app.products.yee.services.scoring_types import (
	CanonicalScoreSnapshot,
	JsonValue,
	LegacyScoreResult,
	RawScoreSnapshot,
	ScoreMetaSnapshot,
	WeightedScoreSnapshot,
)


def _round_2(value: float) -> float:
	return round(value + 1e-9, 2)


def _empty_int_domains() -> dict[str, int]:
	return {domain: 0 for domain in DOMAIN_ORDER}


def _empty_float_domains() -> dict[str, float]:
	return {domain: 0.0 for domain in DOMAIN_ORDER}


def _answer_id(value: JsonValue | None) -> str | None:
	match value:
		case None:
			return None
		case bool():
			return None
		case str() as text:
			trimmed = text.strip()
			return trimmed if trimmed else None
		case int() as number:
			return str(number)
		case _:
			return None


def _matrix_answer(responses: Mapping[str, JsonValue], item_id: str, choice_id: str) -> str | None:
	raw_item = responses.get(item_id)
	match raw_item:
		case dict() as answer_map:
			return _answer_id(answer_map.get(choice_id))
		case _:
			return _answer_id(raw_item)


def _score_answer(answer_scores: tuple[AnswerScore, ...], answer_id: str | None) -> int | None:
	if answer_id is None:
		return None
	for answer_score in answer_scores:
		if answer_score.answer_id == answer_id:
			return answer_score.score
	return None


def _score_paired_item(spec: PairedItemSpec, responses: Mapping[str, JsonValue]) -> tuple[int, bool]:
	presence_answer = _matrix_answer(responses, spec.presence_item_id, spec.choice_id)
	condition_answer = _matrix_answer(responses, spec.condition_item_id, spec.choice_id)
	presence_score = _score_answer((AnswerScore("1", 1), AnswerScore("2", 0)), presence_answer)
	condition_score = _score_answer(CONDITION_SCORES, condition_answer)
	if presence_score is None:
		return 0, False
	if condition_score is None:
		return 0, True
	return presence_score * condition_score, True


def _score_presence_item(spec: PresenceItemSpec, responses: Mapping[str, JsonValue]) -> tuple[int, bool]:
	answer = _matrix_answer(responses, spec.item_id, spec.choice_id)
	score = _score_answer(spec.answer_scores, answer)
	if score is None:
		return 0, False
	return score, True


def _domain_item_counts() -> dict[str, int]:
	counts = _empty_int_domains()
	for spec in ITEM_SPECS:
		counts[spec.domain] += 1
	return counts


def _domain_max_scores() -> dict[str, int]:
	max_scores = _empty_int_domains()
	for spec in ITEM_SPECS:
		max_scores[spec.domain] += spec.max_score
	return max_scores


def _coerce_weight(value: JsonValue | None) -> int:
	match value:
		case bool() | None:
			return 0
		case int() as number:
			return number if number in {1, 2, 3} else 0
		case str() as text:
			if not text.isdigit():
				return 0
			number = int(text)
			return number if number in {1, 2, 3} else 0
		case _:
			return 0


def extract_domain_weights(participant_info: Mapping[str, JsonValue]) -> dict[str, int]:
	raw_weights = participant_info.get("domain_weights")
	match raw_weights:
		case dict() as weight_map:
			return {domain: _coerce_weight(weight_map.get(domain)) for domain in DOMAIN_ORDER}
		case _:
			return _empty_int_domains()


def build_weighted_score_snapshot(
	raw_domain_scores: Mapping[str, int],
	participant_info: Mapping[str, JsonValue],
) -> WeightedScoreSnapshot:
	weights = extract_domain_weights(participant_info)
	total_weight_sum = sum(weights.values())
	item_counts = _domain_item_counts()
	max_scores = _domain_max_scores()
	exact_domain_averages = {domain: raw_domain_scores.get(domain, 0) / item_counts[domain] for domain in DOMAIN_ORDER}
	domain_averages = {domain: _round_2(exact_domain_averages[domain]) for domain in DOMAIN_ORDER}
	if total_weight_sum <= 0:
		return {
			"raw_domain_weights": weights,
			"normalized_domain_weights": _empty_float_domains(),
			"domain_average_scores": domain_averages,
			"weighted_domain_scores": _empty_float_domains(),
			"total_weighted_score": 0.0,
			"priority_gaps": _empty_float_domains(),
		}
	exact_normalized_weights = {domain: weights[domain] / total_weight_sum for domain in DOMAIN_ORDER}
	normalized_weights = {domain: _round_2(exact_normalized_weights[domain]) for domain in DOMAIN_ORDER}
	weighted_domain_scores = {
		domain: _round_2(exact_domain_averages[domain] * exact_normalized_weights[domain]) for domain in DOMAIN_ORDER
	}
	priority_gaps = {
		domain: _round_2(
			((max_scores[domain] / item_counts[domain]) - exact_domain_averages[domain])
			* exact_normalized_weights[domain]
		)
		for domain in DOMAIN_ORDER
	}
	return {
		"raw_domain_weights": weights,
		"normalized_domain_weights": normalized_weights,
		"domain_average_scores": domain_averages,
		"weighted_domain_scores": weighted_domain_scores,
		"total_weighted_score": _round_2(sum(weighted_domain_scores.values())),
		"priority_gaps": priority_gaps,
	}


def build_canonical_score_snapshot(
	responses: Mapping[str, JsonValue],
	participant_info: Mapping[str, JsonValue] | None = None,
) -> CanonicalScoreSnapshot:
	raw_domain_scores = _empty_int_domains()
	item_scores: dict[str, int] = {}
	matched_scored_answers = 0
	for spec in ITEM_SPECS:
		match spec:
			case PairedItemSpec():
				score, matched = _score_paired_item(spec, responses)
			case PresenceItemSpec():
				score, matched = _score_presence_item(spec, responses)
			case unreachable:
				assert_never(unreachable)
		item_scores[spec.key] = score
		raw_domain_scores[spec.domain] += score
		if matched:
			matched_scored_answers += 1
	section_scores = {SECTION_BY_DOMAIN[domain]: raw_domain_scores[domain] for domain in DOMAIN_ORDER}
	category_scores = {"Score": sum(raw_domain_scores.values())}
	category_scores.update({CATEGORY_BY_DOMAIN[domain]: raw_domain_scores[domain] for domain in DOMAIN_ORDER})
	raw: RawScoreSnapshot = {
		"total_score": category_scores["Score"],
		"domain_scores": raw_domain_scores,
		"section_scores": section_scores,
		"category_scores": category_scores,
		"item_scores": item_scores,
		"matched_scored_answers": matched_scored_answers,
	}
	participant = participant_info or {}
	weighted = build_weighted_score_snapshot(raw_domain_scores, participant)
	item_counts = _domain_item_counts()
	max_scores = _domain_max_scores()
	meta: ScoreMetaSnapshot = {
		"domain_order": list(DOMAIN_ORDER),
		"domain_item_counts": item_counts,
		"domain_max_average_scores": {
			domain: _round_2(max_scores[domain] / item_counts[domain]) for domain in DOMAIN_ORDER
		},
	}
	snapshot: CanonicalScoreSnapshot = {
		"scoring_version": SCORING_VERSION,
		"raw": raw,
		"weighted": weighted,
		"meta": meta,
	}
	return snapshot


def build_legacy_score_result(snapshot: CanonicalScoreSnapshot) -> LegacyScoreResult:
	return {
		"total_score": snapshot["raw"]["total_score"],
		"section_scores": snapshot["raw"]["section_scores"],
		"category_scores": snapshot["raw"]["category_scores"],
		"matched_scored_answers": snapshot["raw"]["matched_scored_answers"],
		"canonical_score": snapshot,
	}


def score_yee_responses_with_participant_info(
	responses: Mapping[str, JsonValue],
	participant_info: Mapping[str, JsonValue] | None = None,
) -> LegacyScoreResult:
	return build_legacy_score_result(build_canonical_score_snapshot(responses, participant_info))

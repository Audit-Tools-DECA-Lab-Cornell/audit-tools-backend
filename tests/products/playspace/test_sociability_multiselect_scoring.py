from __future__ import annotations

from typing import Any, cast

import pytest

from app.products.playspace.schemas.instrument import ExecutionMode
from app.products.playspace.scoring import _is_question_complete, score_audit
from app.products.playspace.scoring_metadata import (
	ScoringQuestion,
	ScoringScale,
	ScoringScaleOption,
	ScoringSection,
)
from tests.products.playspace import _instrument_builders as builders


def _provision_scale() -> ScoringScale:
	return ScoringScale(
		key="provision",
		options=[
			ScoringScaleOption(
				key="no",
				addition_value=0,
				boost_value=1,
				allows_follow_up_scales=False,
			),
			ScoringScaleOption(
				key="some",
				addition_value=1,
				boost_value=1,
				allows_follow_up_scales=True,
			),
		],
	)


def _multiple_sociability_scale() -> ScoringScale:
	return ScoringScale(
		key="sociability",
		selection_mode="multiple",
		options=[
			ScoringScaleOption(
				key=key,
				addition_value=1,
				boost_value=1,
				allows_follow_up_scales=False,
			)
			for key in ("play_alone", "small_group", "large_group")
		],
	)


def _legacy_sociability_scale() -> ScoringScale:
	return ScoringScale(
		key="sociability",
		selection_mode="single",
		options=[
			ScoringScaleOption(
				key="no",
				addition_value=0,
				boost_value=1,
				allows_follow_up_scales=False,
			),
			ScoringScaleOption(
				key="yes_a_pair",
				addition_value=1,
				boost_value=2,
				allows_follow_up_scales=False,
			),
			ScoringScaleOption(
				key="yes_more_than_two_children",
				addition_value=2,
				boost_value=3,
				allows_follow_up_scales=False,
			),
		],
	)


def _question(
	question_key: str,
	*,
	mode: str = "audit",
	domains: list[str] | None = None,
	multiple: bool = True,
) -> ScoringQuestion:
	return ScoringQuestion(
		question_key=question_key,
		mode=mode,
		constructs=["play_value"],
		domains=domains or ["Domain A"],
		question_type="scaled",
		required=True,
		display_if=None,
		scales=[
			_provision_scale(),
			_multiple_sociability_scale() if multiple else _legacy_sociability_scale(),
		],
	)


def _score(
	monkeypatch: pytest.MonkeyPatch,
	sections: list[ScoringSection],
	responses: dict[str, dict[str, object]],
) -> dict[str, Any]:
	monkeypatch.setattr("app.products.playspace.scoring.get_scoring_sections", lambda: sections)
	return cast(
		dict[str, Any],
		score_audit(
			responses_json={
				"meta": {"execution_mode": ExecutionMode.BOTH.value},
				"sections": {
					section.section_key: {
						"responses": {
							question.question_key: responses[question.question_key]
							for question in section.questions
							if question.question_key in responses
						}
					}
					for section in sections
				},
			},
			include_maximums=True,
		),
	)


def _score_instrument_answer(
	content: dict[str, Any],
	*,
	execution_mode: str,
	answers: dict[str, object],
) -> dict[str, Any]:
	"""Score one answer to the builder's first scaled question through the instrument scoring path."""

	return cast(
		dict[str, Any],
		score_audit(
			responses_json={
				"meta": {"execution_mode": execution_mode},
				"sections": {builders.SECTION_KEY: {"responses": {builders.SCALED_QUESTION_KEY: answers}}},
			},
			include_maximums=True,
			instrument=builders.parse(content),
		),
	)


@pytest.mark.parametrize(
	("answer_key", "expected_total"),
	[("no", 0.0), ("yes_a_pair", 0.0), ("yes_more_than_two_children", 1.0)],
)
def test_single_select_sociability_scores_authored_zero_one_two_as_zero_zero_one(
	answer_key: str,
	expected_total: float,
) -> None:
	content = builders.minimal_content(sociability="single")
	scores = _score_instrument_answer(
		content,
		execution_mode="audit",
		answers={"provision": "a_lot", "sociability": answer_key},
	)

	assert scores["overall"]["sociability_total"] == expected_total
	assert scores["overall"]["sociability_total_max"] == 1.0
	assert scores["overall"]["sociability_breakdown"] is None


def test_provision_unsure_variants_preserve_multiselect_maximum() -> None:
	content = builders.minimal_content(sociability="multiple")
	builders.scale(content, builders.SCALED_QUESTION_KEY, "provision")["options"].append(
		builders.scale_option("unsure", "Unsure", 0, 1, is_unsure=True)
	)
	scores = _score_instrument_answer(content, execution_mode="both", answers={"provision": "unsure"})

	assert scores["overall"]["sociability_total_max"] == 0.0
	assert scores["unsure_variants"]["unsure_as_zero"]["overall"]["sociability_total"] == 0.0
	assert scores["unsure_variants"]["unsure_as_zero"]["overall"]["sociability_total_max"] == 3.0
	zero_breakdown = scores["unsure_variants"]["unsure_as_zero"]["overall"]["sociability_breakdown"]
	assert zero_breakdown["play_alone"] == {"total": 0.0, "max": 1.0}
	assert zero_breakdown["small_group"] == {"total": 0.0, "max": 1.0}
	assert zero_breakdown["large_group"] == {"total": 0.0, "max": 1.0}
	assert zero_breakdown["eligible_question_count"] == 1
	assert scores["unsure_variants"]["unsure_as_max"]["overall"]["sociability_total"] == 3.0
	assert scores["unsure_variants"]["unsure_as_max"]["overall"]["sociability_total_max"] == 3.0


@pytest.mark.parametrize(
	("selected_keys", "expected_total"),
	[
		(["play_alone"], 1.0),
		(["play_alone", "small_group"], 2.0),
		(["play_alone", "small_group", "large_group"], 3.0),
	],
)
def test_multiple_sociability_scores_each_selected_opportunity_once(
	monkeypatch: pytest.MonkeyPatch,
	selected_keys: list[str],
	expected_total: float,
) -> None:
	section = ScoringSection(section_key="section_a", questions=[_question("q_a")])
	scores = _score(
		monkeypatch,
		[section],
		{"q_a": {"provision": "some", "sociability": selected_keys}},
	)

	overall = scores["overall"]
	assert overall["sociability_total"] == expected_total
	assert overall["sociability_total_max"] == 3.0
	assert overall["sociability_breakdown"] == {
		"model": "multi_select_v1",
		"play_alone": {"total": float("play_alone" in selected_keys), "max": 1.0},
		"small_group": {"total": float("small_group" in selected_keys), "max": 1.0},
		"large_group": {"total": float("large_group" in selected_keys), "max": 1.0},
		"captured_question_count": 1,
		"eligible_question_count": 1,
	}


@pytest.mark.parametrize(
	("answer", "message"),
	[
		([], "non-empty"),
		(["play_alone", "play_alone"], "duplicate"),
		(["unknown"], "unknown"),
		(["play_alone", 3], "strings"),
		("play_alone", "list"),
	],
)
def test_multiple_sociability_rejects_invalid_explicit_answers(
	monkeypatch: pytest.MonkeyPatch,
	answer: object,
	message: str,
) -> None:
	section = ScoringSection(section_key="section_a", questions=[_question("q_a")])

	with pytest.raises(ValueError, match=message):
		_score(monkeypatch, [section], {"q_a": {"provision": "some", "sociability": answer}})


def test_multiple_sociability_missing_answer_is_eligible_but_not_captured(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	question = _question("q_a")
	section = ScoringSection(section_key="section_a", questions=[question])
	scores = _score(monkeypatch, [section], {"q_a": {"provision": "some"}})

	assert _is_question_complete(question=question, section_answers={"q_a": {"provision": "some"}}) is False
	assert scores["overall"]["sociability_total"] == 0.0
	assert scores["overall"]["sociability_total_max"] == 3.0
	assert scores["overall"]["sociability_breakdown"]["captured_question_count"] == 0
	assert scores["overall"]["sociability_breakdown"]["eligible_question_count"] == 1


def test_multiple_sociability_completion_requires_a_non_empty_valid_list() -> None:
	question = _question("q_a")

	assert (
		_is_question_complete(
			question=question,
			section_answers={"q_a": {"provision": "some", "sociability": ["small_group"]}},
		)
		is True
	)
	for invalid_answer in ([], ["unknown"], ["play_alone", "play_alone"], "play_alone"):
		assert (
			_is_question_complete(
				question=question,
				section_answers={"q_a": {"provision": "some", "sociability": invalid_answer}},
			)
			is False
		)


def test_provision_gating_excludes_multiple_sociability_score_and_max(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	section = ScoringSection(section_key="section_a", questions=[_question("q_a")])
	scores = _score(monkeypatch, [section], {"q_a": {"provision": "no"}})

	assert scores["overall"]["sociability_total"] == 0.0
	assert scores["overall"]["sociability_total_max"] == 0.0
	assert scores["overall"]["sociability_breakdown"]["eligible_question_count"] == 0
	assert scores["overall"]["sociability_breakdown"]["play_alone"] == {"total": 0.0, "max": 0.0}


def test_multiple_sociability_breakdown_aggregates_sections_domains_and_partitions(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	section_a = ScoringSection(
		section_key="section_a",
		questions=[
			_question("q_audit", mode="audit", domains=["Domain A"]),
			_question("q_both", mode="both", domains=["Domain A"]),
		],
	)
	section_b = ScoringSection(
		section_key="section_b",
		questions=[_question("q_survey", mode="survey", domains=["Domain B"])],
	)
	scores = _score(
		monkeypatch,
		[section_a, section_b],
		{
			"q_audit": {"provision": "some", "sociability": ["play_alone"]},
			"q_both": {"provision": "some", "sociability": ["small_group", "large_group"]},
			"q_survey": {"provision": "some", "sociability": ["large_group"]},
		},
	)

	assert scores["overall"]["sociability_breakdown"]["captured_question_count"] == 3
	assert scores["by_section"]["section_a"]["sociability_breakdown"]["small_group"]["total"] == 1.0
	assert scores["by_domain"]["Domain A"]["sociability_breakdown"]["eligible_question_count"] == 2
	assert scores["audit"]["sociability_breakdown"]["captured_question_count"] == 2
	assert scores["survey"]["sociability_breakdown"]["captured_question_count"] == 2
	assert scores["survey"]["sociability_breakdown"]["large_group"]["total"] == 2.0


def test_mixed_legacy_and_multiple_sociability_never_infers_legacy_breakdown_zeroes(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	section = ScoringSection(
		section_key="section_a",
		questions=[_question("q_legacy", multiple=False), _question("q_multiple")],
	)
	scores = _score(
		monkeypatch,
		[section],
		{
			"q_legacy": {"provision": "some", "sociability": "yes_more_than_two_children"},
			"q_multiple": {"provision": "some", "sociability": ["play_alone"]},
		},
	)

	breakdown = scores["overall"]["sociability_breakdown"]
	assert scores["overall"]["sociability_total"] == 2.0
	assert scores["overall"]["sociability_total_max"] == 4.0
	assert breakdown["captured_question_count"] == 1
	assert breakdown["eligible_question_count"] == 1
	assert breakdown["play_alone"] == {"total": 1.0, "max": 1.0}

"""Regression tests for backend scoring/progress question visibility rules."""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.products.playspace.schemas.instrument import ExecutionMode, InstrumentScaleOptionResponse
from app.products.playspace.scoring import build_audit_progress, score_audit
from app.products.playspace.scoring_metadata import (
	ScoringChoiceOption,
	ScoringDisplayCondition,
	ScoringQuestion,
	ScoringScale,
	ScoringScaleOption,
	ScoringSection,
)
from tests.products.playspace import _instrument_builders as builders


def _build_custom_section() -> ScoringSection:
	"""Create a tiny section with a scaled parent and optional checklist child."""

	return ScoringSection(
		section_key="section_demo",
		questions=[
			ScoringQuestion(
				question_key="q_parent",
				mode="audit",
				constructs=["usability"],
				domains=["Demo"],
				question_type="scaled",
				required=True,
				display_if=None,
				options=[],
				scales=[
					ScoringScale(
						key="provision",
						options=[
							ScoringScaleOption(
								key="no",
								addition_value=0.0,
								boost_value=0.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="some",
								addition_value=1.0,
								boost_value=1.0,
								allows_follow_up_scales=True,
							),
						],
					)
				],
			),
			ScoringQuestion(
				question_key="q_child_checklist",
				mode="audit",
				constructs=[],
				domains=["Demo"],
				question_type="checklist",
				required=False,
				display_if=ScoringDisplayCondition(
					question_key="q_parent",
					response_key="provision",
					any_of_option_keys=["some"],
				),
				options=[
					ScoringChoiceOption(key="cups", label="Cups"),
					ScoringChoiceOption(key="buckets", label="Buckets"),
				],
				scales=[],
			),
		],
	)


def _build_construct_scoring_section() -> ScoringSection:
	"""Create one scaled section that exercises totals, max totals, and both constructs."""

	return ScoringSection(
		section_key="section_constructs",
		questions=[
			ScoringQuestion(
				question_key="q_construct",
				mode="audit",
				constructs=["play_value", "usability"],
				domains=["Construct Demo"],
				question_type="scaled",
				required=True,
				display_if=None,
				options=[],
				scales=[
					ScoringScale(
						key="provision",
						options=[
							ScoringScaleOption(
								key="no",
								addition_value=0.0,
								boost_value=0.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="some",
								addition_value=1.0,
								boost_value=1.0,
								allows_follow_up_scales=True,
							),
							ScoringScaleOption(
								key="a_lot",
								addition_value=2.0,
								boost_value=2.0,
								allows_follow_up_scales=True,
							),
						],
					),
					ScoringScale(
						key="variety",
						options=[
							ScoringScaleOption(
								key="not_applicable",
								addition_value=0.0,
								boost_value=1.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="some_variety",
								addition_value=2.0,
								boost_value=2.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="a_lot_of_variety",
								addition_value=3.0,
								boost_value=3.0,
								allows_follow_up_scales=False,
							),
						],
					),
					ScoringScale(
						key="challenge",
						options=[
							ScoringScaleOption(
								key="not_applicable",
								addition_value=0.0,
								boost_value=1.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="some_challenge",
								addition_value=2.0,
								boost_value=2.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="a_lot_of_challenge",
								addition_value=3.0,
								boost_value=3.0,
								allows_follow_up_scales=False,
							),
						],
					),
					ScoringScale(
						key="sociability",
						options=[
							ScoringScaleOption(
								key="none",
								addition_value=1.0,
								boost_value=1.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="pairs",
								addition_value=2.0,
								boost_value=2.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="groups",
								addition_value=3.0,
								boost_value=3.0,
								allows_follow_up_scales=False,
							),
						],
					),
				],
			)
		],
	)


def _build_partition_scoring_section() -> ScoringSection:
	"""Create one section that covers audit, survey, and both-mode partitioning."""

	provision_scale = ScoringScale(
		key="provision",
		options=[
			ScoringScaleOption(
				key="no",
				addition_value=0.0,
				boost_value=0.0,
				allows_follow_up_scales=False,
			),
			ScoringScaleOption(
				key="some",
				addition_value=1.0,
				boost_value=1.0,
				allows_follow_up_scales=False,
			),
		],
	)
	return ScoringSection(
		section_key="section_partitions",
		questions=[
			ScoringQuestion(
				question_key="q_audit",
				mode="audit",
				constructs=["play_value"],
				domains=["Partition Demo"],
				question_type="scaled",
				required=True,
				display_if=None,
				options=[],
				scales=[provision_scale],
			),
			ScoringQuestion(
				question_key="q_survey",
				mode="survey",
				constructs=["play_value"],
				domains=["Partition Demo"],
				question_type="scaled",
				required=True,
				display_if=None,
				options=[],
				scales=[provision_scale],
			),
			ScoringQuestion(
				question_key="q_both",
				mode="both",
				constructs=["play_value"],
				domains=["Partition Demo"],
				question_type="scaled",
				required=True,
				display_if=None,
				options=[],
				scales=[provision_scale],
			),
		],
	)


def test_build_audit_progress_ignores_optional_checklist_follow_up_questions(
	monkeypatch,
) -> None:
	"""Optional checklist follow-ups should not block section completion or submission."""

	custom_sections = [_build_custom_section()]
	monkeypatch.setattr(
		"app.products.playspace.scoring.get_scoring_sections",
		lambda: custom_sections,
	)

	progress = build_audit_progress(
		responses_json={
			"meta": {"execution_mode": ExecutionMode.AUDIT.value},
			"pre_audit": {
				"place_size": "medium",
				"current_users_0_5": "none",
				"current_users_6_12": "some",
				"current_users_13_17": "none",
				"current_users_18_plus": "none",
				"playspace_busyness": "some",
				"season": "summer",
				"weather_conditions": ["sunshine"],
				"wind_conditions": "calm",
			},
			"sections": {
				"section_demo": {
					"responses": {
						"q_parent": {
							"provision": "some",
						}
					}
				}
			},
		}
	)

	assert progress.visible_section_count == 1
	assert progress.total_visible_questions == 1
	assert progress.answered_visible_questions == 1
	assert progress.ready_to_submit is True
	assert len(progress.sections) == 1
	assert progress.sections[0].visible_question_count == 1
	assert progress.sections[0].answered_question_count == 1
	assert progress.sections[0].is_complete is True


def test_score_audit_ignores_non_scored_checklist_questions(monkeypatch) -> None:
	"""Checklist follow-ups should not contribute to aggregate score totals."""

	custom_sections = [_build_custom_section()]
	monkeypatch.setattr(
		"app.products.playspace.scoring.get_scoring_sections",
		lambda: custom_sections,
	)

	scores = score_audit(
		responses_json={
			"meta": {"execution_mode": ExecutionMode.AUDIT.value},
			"pre_audit": {
				"place_size": "medium",
				"current_users_0_5": "none",
				"current_users_6_12": "some",
				"current_users_13_17": "none",
				"current_users_18_plus": "none",
				"playspace_busyness": "some",
				"season": "summer",
				"weather_conditions": ["sunshine"],
				"wind_conditions": "calm",
			},
			"sections": {
				"section_demo": {
					"responses": {
						"q_parent": {
							"provision": "some",
						},
						"q_child_checklist": {
							"selected_option_keys": ["cups"],
						},
					}
				}
			},
		},
		include_maximums=True,
	)

	overall = scores.get("overall")
	assert isinstance(overall, dict)
	assert overall["provision_total"] == 1.0
	assert overall["usability_total"] == 1.0


def test_score_audit_tracks_maximum_totals_for_scales_and_constructs(
	monkeypatch,
) -> None:
	"""Scoring should expose raw totals and max-possible totals for the same question."""

	custom_sections = [_build_construct_scoring_section()]
	monkeypatch.setattr(
		"app.products.playspace.scoring.get_scoring_sections",
		lambda: custom_sections,
	)

	scores = score_audit(
		responses_json={
			"meta": {"execution_mode": ExecutionMode.AUDIT.value},
			"pre_audit": {
				"place_size": "medium",
				"current_users_0_5": "none",
				"current_users_6_12": "some",
				"current_users_13_17": "none",
				"current_users_18_plus": "none",
				"playspace_busyness": "some",
				"season": "summer",
				"weather_conditions": ["sunshine"],
				"wind_conditions": "calm",
			},
			"sections": {
				"section_constructs": {
					"responses": {
						"q_construct": {
							"provision": "some",
							"variety": "some_variety",
							"challenge": "a_lot_of_challenge",
							"sociability": "pairs",
						}
					}
				}
			},
		},
		include_maximums=True,
	)

	overall = scores.get("overall")
	assert isinstance(overall, dict)

	assert overall["provision_total"] == 1.0
	assert overall["provision_total_max"] == 2.0
	assert overall["variety_total"] == 1.0
	assert overall["variety_total_max"] == 2.0
	assert overall["challenge_total"] == 2.0
	assert overall["challenge_total_max"] == 2.0
	assert overall["sociability_total"] == 1.0
	assert overall["sociability_total_max"] == 2.0
	assert overall["play_value_total"] == 6.0
	assert overall["play_value_total_max"] == 18.0
	assert overall["usability_total"] == 6.0
	assert overall["usability_total_max"] == 18.0


def test_score_audit_builds_audit_and_survey_partitions_for_both_mode(monkeypatch) -> None:
	"""A `both` execution should emit separate audit and survey partitions by question mode."""

	custom_sections = [_build_partition_scoring_section()]
	monkeypatch.setattr(
		"app.products.playspace.scoring.get_scoring_sections",
		lambda: custom_sections,
	)

	scores = score_audit(
		responses_json={
			"meta": {"execution_mode": ExecutionMode.BOTH.value},
			"pre_audit": {
				"place_size": "medium",
				"current_users_0_5": "none",
				"current_users_6_12": "some",
				"current_users_13_17": "none",
				"current_users_18_plus": "none",
				"playspace_busyness": "some",
				"season": "summer",
				"weather_conditions": ["sunshine"],
				"wind_conditions": "calm",
			},
			"sections": {
				"section_partitions": {
					"responses": {
						"q_audit": {"provision": "some"},
						"q_survey": {"provision": "some"},
						"q_both": {"provision": "some"},
					}
				}
			},
		},
		include_maximums=True,
	)

	overall = scores.get("overall")
	audit_partition = scores.get("audit")
	survey_partition = scores.get("survey")
	assert isinstance(overall, dict)
	assert isinstance(audit_partition, dict)
	assert isinstance(survey_partition, dict)
	assert overall["play_value_total"] == 3.0
	assert audit_partition["play_value_total"] == 2.0
	assert survey_partition["play_value_total"] == 2.0


def test_score_audit_allows_both_questions_to_feed_survey_partition_in_audit_mode(monkeypatch) -> None:
	"""Audit-mode submissions should still feed the survey partition for `both` questions."""

	custom_sections = [_build_partition_scoring_section()]
	monkeypatch.setattr(
		"app.products.playspace.scoring.get_scoring_sections",
		lambda: custom_sections,
	)

	scores = score_audit(
		responses_json={
			"meta": {"execution_mode": ExecutionMode.AUDIT.value},
			"pre_audit": {
				"place_size": "medium",
				"current_users_0_5": "none",
				"current_users_6_12": "some",
				"current_users_13_17": "none",
				"current_users_18_plus": "none",
				"playspace_busyness": "some",
				"season": "summer",
				"weather_conditions": ["sunshine"],
				"wind_conditions": "calm",
			},
			"sections": {
				"section_partitions": {
					"responses": {
						"q_audit": {"provision": "some"},
						"q_both": {"provision": "some"},
					}
				}
			},
		},
		include_maximums=True,
	)

	audit_partition = scores.get("audit")
	survey_partition = scores.get("survey")
	assert isinstance(audit_partition, dict)
	assert isinstance(survey_partition, dict)
	assert audit_partition["play_value_total"] == 2.0
	assert survey_partition["play_value_total"] == 1.0


def _build_unsure_scoring_section() -> ScoringSection:
	"""Create one section with N/A and Unsure options on every scale."""

	return ScoringSection(
		section_key="section_unsure",
		questions=[
			ScoringQuestion(
				question_key="q_unsure",
				mode="audit",
				constructs=["play_value", "usability"],
				domains=["Unsure Demo"],
				question_type="scaled",
				required=True,
				display_if=None,
				options=[],
				scales=[
					ScoringScale(
						key="provision",
						options=[
							ScoringScaleOption(
								key="no",
								addition_value=0.0,
								boost_value=0.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="some",
								addition_value=1.0,
								boost_value=1.0,
								allows_follow_up_scales=True,
							),
							ScoringScaleOption(
								key="a_lot",
								addition_value=2.0,
								boost_value=2.0,
								allows_follow_up_scales=True,
							),
							ScoringScaleOption(
								key="not_applicable",
								addition_value=0.0,
								boost_value=1.0,
								allows_follow_up_scales=False,
								is_not_applicable=True,
							),
							ScoringScaleOption(
								key="unsure",
								addition_value=0.0,
								boost_value=1.0,
								allows_follow_up_scales=False,
								is_unsure=True,
							),
						],
					),
					ScoringScale(
						key="variety",
						options=[
							ScoringScaleOption(
								key="not_applicable",
								addition_value=0.0,
								boost_value=1.0,
								allows_follow_up_scales=False,
								is_not_applicable=True,
							),
							ScoringScaleOption(
								key="some_variety",
								addition_value=2.0,
								boost_value=2.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="a_lot_of_variety",
								addition_value=3.0,
								boost_value=3.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="unsure",
								addition_value=0.0,
								boost_value=1.0,
								allows_follow_up_scales=False,
								is_unsure=True,
							),
						],
					),
					ScoringScale(
						key="challenge",
						options=[
							ScoringScaleOption(
								key="not_applicable",
								addition_value=0.0,
								boost_value=1.0,
								allows_follow_up_scales=False,
								is_not_applicable=True,
							),
							ScoringScaleOption(
								key="some_challenge",
								addition_value=2.0,
								boost_value=2.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="a_lot_of_challenge",
								addition_value=3.0,
								boost_value=3.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="unsure",
								addition_value=0.0,
								boost_value=1.0,
								allows_follow_up_scales=False,
								is_unsure=True,
							),
						],
					),
					ScoringScale(
						key="sociability",
						options=[
							ScoringScaleOption(
								key="none",
								addition_value=1.0,
								boost_value=1.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="pairs",
								addition_value=2.0,
								boost_value=2.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="groups",
								addition_value=3.0,
								boost_value=3.0,
								allows_follow_up_scales=False,
							),
							ScoringScaleOption(
								key="not_applicable",
								addition_value=0.0,
								boost_value=1.0,
								allows_follow_up_scales=False,
								is_not_applicable=True,
							),
							ScoringScaleOption(
								key="unsure",
								addition_value=0.0,
								boost_value=1.0,
								allows_follow_up_scales=False,
								is_unsure=True,
							),
						],
					),
				],
			)
		],
	)


def _score_unsure_fixture(monkeypatch, responses: dict[str, str]) -> dict[str, object]:
	custom_sections = [_build_unsure_scoring_section()]
	monkeypatch.setattr(
		"app.products.playspace.scoring.get_scoring_sections",
		lambda: custom_sections,
	)
	return score_audit(
		responses_json={
			"meta": {"execution_mode": ExecutionMode.AUDIT.value},
			"sections": {"section_unsure": {"responses": {"q_unsure": responses}}},
		},
		include_maximums=True,
	)


def test_score_audit_excludes_entire_question_when_provision_is_not_applicable(monkeypatch) -> None:
	"""Provision N/A should remove the full question from totals and denominators."""

	scores = _score_unsure_fixture(
		monkeypatch,
		{
			"provision": "not_applicable",
			"variety": "unsure",
			"challenge": "some_challenge",
			"sociability": "unsure",
		},
	)

	overall = scores.get("overall")
	assert isinstance(overall, dict)
	assert all(value == 0.0 for key, value in overall.items() if key.endswith("_total") or key.endswith("_max"))
	assert scores.get("unsure_answer_count") == 0
	assert scores.get("unsure_variants") is None


def test_score_audit_excludes_follow_up_not_applicable_denominator_only(monkeypatch) -> None:
	"""Follow-up N/A should only remove that scale's denominator and multiplier max."""

	scores = _score_unsure_fixture(
		monkeypatch,
		{
			"provision": "some",
			"variety": "not_applicable",
			"challenge": "some_challenge",
			"sociability": "pairs",
		},
	)

	overall = scores.get("overall")
	assert isinstance(overall, dict)
	assert overall["provision_total"] == 1.0
	assert overall["provision_total_max"] == 2.0
	assert overall["variety_total"] == 0.0
	assert overall["variety_total_max"] == 0.0
	assert overall["challenge_total"] == 1.0
	assert overall["challenge_total_max"] == 2.0
	assert overall["sociability_total"] == 1.0
	assert overall["sociability_total_max"] == 2.0
	assert overall["play_value_total"] == 2.0
	assert overall["play_value_total_max"] == 6.0
	assert overall["usability_total"] == 2.0
	assert overall["usability_total_max"] == 6.0


def test_score_audit_emits_unsure_variants_for_follow_up_answers(monkeypatch) -> None:
	"""Unsure answers should expose excluded, zero, and max interpretations side by side."""

	scores = _score_unsure_fixture(
		monkeypatch,
		{
			"provision": "some",
			"variety": "unsure",
			"challenge": "some_challenge",
			"sociability": "unsure",
		},
	)

	canonical = scores.get("overall")
	assert isinstance(canonical, dict)
	assert scores.get("unsure_answer_count") == 2
	assert canonical["variety_total"] == 0.0
	assert canonical["variety_total_max"] == 0.0
	assert canonical["sociability_total"] == 0.0
	assert canonical["sociability_total_max"] == 0.0
	assert canonical["play_value_total"] == 2.0
	assert canonical["play_value_total_max"] == 6.0

	variants = scores.get("unsure_variants")
	assert isinstance(variants, dict)
	zero = variants["unsure_as_zero"]["overall"]
	maximum = variants["unsure_as_max"]["overall"]
	assert zero["variety_total"] == 0.0
	assert zero["variety_total_max"] == 2.0
	assert zero["sociability_total"] == 0.0
	assert zero["sociability_total_max"] == 2.0
	assert zero["play_value_total"] == 2.0
	assert zero["play_value_total_max"] == 18.0
	assert maximum["variety_total"] == 2.0
	assert maximum["variety_total_max"] == 2.0
	assert maximum["sociability_total"] == 2.0
	assert maximum["sociability_total_max"] == 2.0
	assert maximum["play_value_total"] == 6.0
	assert maximum["play_value_total_max"] == 18.0


def test_score_audit_handles_provision_unsure_variants_and_ignores_hidden_followups(monkeypatch) -> None:
	"""Provision Unsure should hide stale follow-ups and drive all three interpretations."""

	scores = _score_unsure_fixture(
		monkeypatch,
		{
			"provision": "unsure",
			"variety": "unsure",
			"challenge": "unsure",
			"sociability": "unsure",
		},
	)

	canonical = scores.get("overall")
	assert isinstance(canonical, dict)
	assert all(value == 0.0 for key, value in canonical.items() if key.endswith("_total") or key.endswith("_max"))
	assert scores.get("unsure_answer_count") == 1

	variants = scores.get("unsure_variants")
	assert isinstance(variants, dict)
	zero = variants["unsure_as_zero"]["overall"]
	maximum = variants["unsure_as_max"]["overall"]
	assert zero["provision_total"] == 0.0
	assert zero["provision_total_max"] == 2.0
	assert zero["play_value_total"] == 0.0
	assert zero["play_value_total_max"] == 18.0
	assert maximum["provision_total"] == 2.0
	assert maximum["variety_total"] == 2.0
	assert maximum["challenge_total"] == 2.0
	assert maximum["sociability_total"] == 2.0
	assert maximum["play_value_total"] == 18.0
	assert maximum["play_value_total_max"] == 18.0


def test_instrument_scale_option_defaults_unsure_flag_for_legacy_json() -> None:
	"""Legacy instrument options should parse without an explicit is_unsure field."""

	option = InstrumentScaleOptionResponse(
		key="some",
		label="Some",
		addition_value=1.0,
		boost_value=1.0,
		allows_follow_up_scales=True,
	)

	assert option.is_not_applicable is False
	assert option.is_unsure is False


def _variety_scale() -> dict[str, Any]:
	"""A three-answer Variety scale whose boost multiplies the construct score."""

	return {
		"key": "variety",
		"title": "Variety",
		"prompt": "How much variety?",
		"selection_mode": "single",
		"options": [
			builders.scale_option("no_variety", "No variety", 0, 1),
			builders.scale_option("some_variety", "Some variety", 1, 2),
			builders.scale_option("a_lot_of_variety", "A lot of variety", 2, 3),
		],
	}


def _score_builder_answer(
	content: dict[str, Any],
	*,
	execution_mode: ExecutionMode,
	answers: dict[str, object],
) -> dict[str, Any]:
	"""Score one answer to the builder's first scaled question and return the overall bucket."""

	scores = score_audit(
		responses_json={
			"meta": {"execution_mode": execution_mode.value},
			"sections": {builders.SECTION_KEY: {"responses": {builders.SCALED_QUESTION_KEY: answers}}},
		},
		include_maximums=True,
		instrument=builders.parse(content),
	)
	overall = scores.get("overall")
	assert isinstance(overall, dict)
	return overall


@pytest.mark.parametrize(
	("construct", "other_construct"),
	[("usability", "play_value"), ("play_value", "usability")],
)
def test_score_audit_counts_single_construct_question_only_toward_that_construct(
	construct: str,
	other_construct: str,
) -> None:
	"""A question with one construct feeds that construct's total and maximum and leaves the other at zero."""

	content = builders.minimal_content()
	builders.question(content, builders.SCALED_QUESTION_KEY)["constructs"] = [construct]

	overall = _score_builder_answer(content, execution_mode=ExecutionMode.AUDIT, answers={"provision": "a_lot"})

	assert overall[f"{construct}_total"] == 2.0
	assert overall[f"{construct}_total_max"] == 2.0
	assert overall[f"{other_construct}_total"] == 0.0
	assert overall[f"{other_construct}_total_max"] == 0.0


def test_score_audit_excludes_question_when_instrument_marks_provision_answer_not_applicable() -> None:
	"""A Provision answer the instrument flags as not applicable removes the question from every maximum."""

	content = builders.minimal_content()
	builders.question(content, builders.SCALED_QUESTION_KEY)["constructs"] = ["play_value", "usability"]
	builders.scale(content, builders.SCALED_QUESTION_KEY, "provision")["options"].append(
		builders.scale_option("not_applicable", "Not applicable", 0, 1, is_not_applicable=True)
	)

	overall = _score_builder_answer(
		content,
		execution_mode=ExecutionMode.AUDIT,
		answers={"provision": "not_applicable"},
	)

	assert overall["provision_total_max"] == 0.0
	assert overall["play_value_total_max"] == 0.0
	assert overall["usability_total_max"] == 0.0


def test_score_audit_multiplies_provision_by_variety_boost_without_absent_scale_maxima() -> None:
	"""Variety's boost multiplies Provision; scales the question lacks add nothing to their maxima."""

	content = builders.minimal_content()
	builders.question(content, builders.SCALED_QUESTION_KEY)["scales"].append(_variety_scale())

	overall = _score_builder_answer(
		content,
		execution_mode=ExecutionMode.AUDIT,
		answers={"provision": "a_lot", "variety": "a_lot_of_variety"},
	)

	# A lot (2) x the A lot of variety boost (3).
	assert overall["play_value_total"] == 6.0
	assert overall["play_value_total_max"] == 6.0
	assert overall["challenge_total_max"] == 0.0
	assert overall["sociability_total_max"] == 0.0


def test_score_audit_scores_provision_answer_without_follow_ups_at_its_addition_value() -> None:
	"""A Provision answer that unlocks no follow-ups scores its addition value; its own boost multiplies nothing."""

	content = builders.minimal_content()
	builders.question(content, builders.SCALED_QUESTION_KEY)["mode"] = ExecutionMode.SURVEY.value
	builders.scale(content, builders.SCALED_QUESTION_KEY, "provision")["options"] = [
		builders.scale_option("never", "Never", 0, 1),
		builders.scale_option("sometimes", "Sometimes", 1, 2),
		builders.scale_option("always", "Always", 2, 3),
	]

	overall = _score_builder_answer(content, execution_mode=ExecutionMode.SURVEY, answers={"provision": "always"})

	# Always adds 2; its boost of 3 has no follow-up scale to multiply.
	assert overall["play_value_total"] == 2.0
	assert overall["play_value_total_max"] == 2.0


def test_build_audit_progress_lists_sections_in_instrument_order() -> None:
	"""Progress reports sections in the order the instrument defines them."""

	content = builders.minimal_content()
	# The appended section's key sorts before the first one, so key order and
	# instrument order disagree.
	second_section = copy.deepcopy(content["en"]["sections"][0])
	second_section["section_key"] = "section_0_test"
	for current in second_section["questions"]:
		current["section_key"] = "section_0_test"
		current["question_key"] = current["question_key"].replace("q_1_", "q_0_")
	content["en"]["sections"].append(second_section)

	progress = build_audit_progress(
		responses_json={"meta": {"execution_mode": ExecutionMode.AUDIT.value}, "sections": {}},
		instrument=builders.parse(content),
	)

	assert [section.section_key for section in progress.sections] == [builders.SECTION_KEY, "section_0_test"]

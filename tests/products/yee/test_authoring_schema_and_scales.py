from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.products.yee.schemas.instrument_authoring import AuthoringOption
from app.products.yee.services import scoring_engine
from app.products.yee.services.scoring_engine import _score_paired_item
from app.products.yee.services.scoring_spec import (
	ITEM_SPECS,
	SCORING_VERSION,
	AnswerScore,
	PairedItemSpec,
	ScoringContract,
)
from app.yee_instrument_schema import YeeInstrumentResponse


def _legacy_content() -> dict[str, object]:
	return {
		"survey_name": "Legacy",
		"version": "1",
		"scoring_items": [],
	}


def test_response_preserves_unknown_top_level_content() -> None:
	# Given a legacy document containing extension content
	content = {**_legacy_content(), "future_extension": {"nested": [1, "two"]}}

	# When it crosses the shared validation boundary
	dumped = YeeInstrumentResponse.model_validate(content).model_dump()

	# Then the unmodeled top-level content remains byte-equivalent
	assert dumped["future_extension"] == content["future_extension"]


def test_response_preserves_unknown_nested_legacy_content() -> None:
	# Given extension metadata on every nested legacy content shape
	content = {
		**_legacy_content(),
		"scoring_items": [
			{
				"item_id": "P",
				"base_question_id": "P",
				"block": "Access",
				"question_text": "Prompt",
				"choices": {"1": {"Display": "Question", "choiceExtension": {"keep": 1}}},
				"answers": {"1": {"Display": "Yes", "answerExtension": [1, 2]}},
				"itemExtension": "keep",
			}
		],
		"sections": [{"block": "Access", "title": "Access", "sectionExtension": True}],
		"pre_audit_questions": [
			{
				"id": "pre",
				"title": "Pre",
				"prompt": "Prompt",
				"options": [{"value": "1", "label": "One", "optionExtension": "keep"}],
				"questionExtension": 7,
			}
		],
		"scale_guidance": [
			{
				"id": "scale",
				"title": "Scale",
				"prompt": "Prompt",
				"rules": [{"value": "1", "label": "One", "ruleExtension": "keep"}],
				"scaleExtension": "keep",
			}
		],
		"legal_documents": [{"id": "legal", "title": "Legal", "content": "Text", "legalExtension": 1}],
		"weighting": {
			"weightingExtension": "keep",
			"options": [{"value": "1", "label": "One", "weightOptionExtension": 1}],
			"domains": [{"key": "access", "label": "Access", "prompt": "Rate", "domainExtension": 1}],
		},
	}

	# When the complete document crosses validation and serialization
	dumped = YeeInstrumentResponse.model_validate(content).model_dump()

	# Then nested extension metadata survives at every legacy boundary
	assert dumped["scoring_items"][0]["itemExtension"] == "keep"
	assert dumped["scoring_items"][0]["choices"]["1"]["choiceExtension"] == {"keep": 1}
	assert dumped["scoring_items"][0]["answers"]["1"]["answerExtension"] == [1, 2]
	assert dumped["sections"][0]["sectionExtension"] is True
	assert dumped["pre_audit_questions"][0]["questionExtension"] == 7
	assert dumped["pre_audit_questions"][0]["options"][0]["optionExtension"] == "keep"
	assert dumped["scale_guidance"][0]["scaleExtension"] == "keep"
	assert dumped["scale_guidance"][0]["rules"][0]["ruleExtension"] == "keep"
	assert dumped["legal_documents"][0]["legalExtension"] == 1
	assert dumped["weighting"]["weightingExtension"] == "keep"
	assert dumped["weighting"]["options"][0]["weightOptionExtension"] == 1
	assert dumped["weighting"]["domains"][0]["domainExtension"] == 1


def test_authoring_v2_defaults_and_serializes_follow_up_requiredness() -> None:
	from app.products.yee.schemas.instrument_authoring import AuthoringInstrumentV2

	# Given authoring-v2 content omitting the follow-up requiredness flag
	authoring = {
		"schemaVersion": 2,
		"sections": [
			{
				"id": "access",
				"title": "Access",
				"instructions": "Look around.",
				"commentPrompt": "Comments?",
				"questions": [
					{
						"id": "access.q1",
						"prompt": "A prompt",
						"primary": {"type": "single_select", "options": [{"id": "yes", "label": "Yes", "score": 1}]},
						"followUp": {
							"triggerOptionIds": ["yes"],
							"prompt": "Condition?",
							"options": [{"id": "good", "label": "Good", "score": 3}],
						},
						"scoring": {"method": "presence_condition_product", "domain": "access"},
						"responseBinding": {"presenceItemId": "P", "choiceId": "1", "conditionItemId": "C"},
					}
				],
			}
		],
	}

	# When the full response is validated and dumped through the shared boundary
	parsed = YeeInstrumentResponse.model_validate({**_legacy_content(), "authoring": authoring})
	dumped = parsed.model_dump()

	# Then authoring is typed and the resolved default is present in canonical casing
	assert isinstance(parsed.authoring, AuthoringInstrumentV2)
	assert dumped["authoring"]["sections"][0]["questions"][0]["followUp"]["requiredWhenShown"] is True


@pytest.mark.parametrize("float_score", [1.5, 1.0])
def test_authoring_options_reject_json_float_scores(float_score: float) -> None:
	# Given a fractional or integer-like JSON float score
	option = {"id": "float", "label": "Float", "score": float_score}

	# When it crosses the authoring schema boundary
	with pytest.raises(ValidationError):
		AuthoringOption.model_validate(option)


def test_paired_item_uses_its_explicit_answer_scales() -> None:
	# Given a paired spec whose scales deliberately differ from the legacy defaults
	spec = PairedItemSpec(
		key="custom.q1",
		domain="custom",
		presence_item_id="P",
		condition_item_id="C",
		choice_id="7",
		presence_answer_scores=(AnswerScore("yes", 2), AnswerScore("no", 0)),
		condition_answer_scores=(AnswerScore("good", 4),),
	)

	# When the pure scorer evaluates matching responses
	score, matched = _score_paired_item(spec, {"P": {"7": "yes"}, "C": {"7": "good"}})

	# Then it uses the spec-owned scales rather than fixed global answer ids
	assert (score, matched) == (8, True)


def test_paired_item_derived_maximum_updates_domain_metadata() -> None:
	# Given a paired scale whose negative values produce a non-default Cartesian maximum
	spec = PairedItemSpec(
		key="access.q1",
		domain="access",
		presence_item_id="P",
		condition_item_id="C",
		choice_id="1",
		presence_answer_scores=(AnswerScore("a", -2), AnswerScore("b", -1)),
		condition_answer_scores=(AnswerScore("x", -3), AnswerScore("y", -4)),
	)
	contract = ScoringContract((spec, *ITEM_SPECS[1:]), SCORING_VERSION)

	# When scoring metadata derives the access-domain maximum
	snapshot = scoring_engine.build_canonical_score_snapshot({}, contract=contract)

	# Then the spec and metadata use the actual maximum product of eight
	assert spec.max_score == 8
	assert snapshot["meta"]["domain_max_average_scores"]["access"] == 3.17

from __future__ import annotations

import json
from inspect import Parameter, signature
from pathlib import Path

import pytest

from app.products.yee.services.scoring_engine import build_canonical_score_snapshot
from app.products.yee.services.scoring_resolution import (
	ScoringContractResolutionError,
	scoring_contract_from_instrument,
)
from app.products.yee.services.scoring_spec import (
	ITEM_SPECS,
	SCHEMA_V1_SCORING_CONTRACT,
	SCORING_VERSION,
)
from app.products.yee.services.scoring_types import JsonValue

GOLDEN_PATH = Path(__file__).parent / "fixtures" / "schema_v1_scoring_golden.json"


def _instrument_with_question(question: dict[str, JsonValue]) -> dict[str, JsonValue]:
	return {
		"survey_name": "Versioned scoring",
		"version": "2",
		"scoring_items": [],
		"authoring": {
			"schemaVersion": 2,
			"sections": [
				{
					"id": "access",
					"title": "Access",
					"instructions": "Observe.",
					"commentPrompt": "Comments?",
					"questions": [question],
				}
			],
		},
	}


def _option_score_question() -> dict[str, JsonValue]:
	return {
		"id": "access.custom",
		"prompt": "Custom scored prompt",
		"primary": {
			"type": "single_select",
			"options": [
				{"id": "bonus", "label": "Bonus", "score": 9},
				{"id": "none", "label": "None", "score": 0},
			],
		},
		"followUp": None,
		"scoring": {"method": "option_score", "domain": "access"},
		"responseBinding": {
			"presenceItemId": "CUSTOM#1",
			"choiceId": "custom-choice",
			"conditionItemId": None,
		},
	}


def test_schema_v1_content_resolves_to_exact_frozen_legacy_contract() -> None:
	# Given legacy content with no authoring-v2 document
	content: dict[str, JsonValue] = {"survey_name": "Legacy", "version": "1", "scoring_items": []}

	# When its scoring contract is resolved
	contract = scoring_contract_from_instrument(content)

	# Then the shared immutable schema-v1 contract is returned exactly
	assert contract is SCHEMA_V1_SCORING_CONTRACT
	assert contract.item_specs is ITEM_SPECS
	assert contract.scoring_algorithm == SCORING_VERSION
	assert len(contract.item_specs) == 54


def test_canonical_snapshot_requires_explicit_contract() -> None:
	# Given the contract-driven canonical engine API
	contract_parameter = signature(build_canonical_score_snapshot).parameters["contract"]

	# When its contract parameter is inspected
	parameter_contract = (contract_parameter.kind, contract_parameter.default)

	# Then callers must supply it explicitly by keyword
	assert parameter_contract == (Parameter.KEYWORD_ONLY, Parameter.empty)


def test_schema_v1_corpus_is_byte_equivalent_for_all_54_items() -> None:
	# Given an independently enumerated schema-v1 response and score corpus
	corpus = json.loads(GOLDEN_PATH.read_text())

	# When the frozen schema-v1 contract scores every legacy row
	snapshot = build_canonical_score_snapshot(
		corpus["responses"],
		corpus["participant_info"],
		contract=SCHEMA_V1_SCORING_CONTRACT,
	)
	actual_bytes = json.dumps(snapshot, separators=(",", ":"), sort_keys=True).encode()
	expected_bytes = json.dumps(corpus["expected"], separators=(",", ":"), sort_keys=True).encode()

	# Then the complete canonical payload and all ten reverse rows match the frozen oracle
	assert actual_bytes == expected_bytes
	assert {key: snapshot["raw"]["item_scores"][key] for key in corpus["reverse_item_scores"]} == corpus[
		"reverse_item_scores"
	]


def test_authoring_v2_option_score_uses_its_bound_option_score() -> None:
	# Given authoring-v2 defines an option score absent from ITEM_SPECS
	contract = scoring_contract_from_instrument(_instrument_with_question(_option_score_question()))

	# When the pure engine scores the explicitly bound response
	snapshot = build_canonical_score_snapshot(
		{"CUSTOM#1": {"custom-choice": "bonus"}},
		{},
		contract=contract,
	)

	# Then the score comes from authoring-v2, not the frozen legacy table
	assert snapshot["raw"]["item_scores"] == {"access.custom": 9}
	assert snapshot["raw"]["total_score"] == 9
	assert snapshot["meta"]["domain_item_counts"]["access"] == 1


def test_authoring_v2_paired_score_uses_both_versioned_option_scales() -> None:
	# Given authoring-v2 defines non-legacy ids and scores for a paired question
	question = _option_score_question() | {
		"primary": {
			"type": "single_select",
			"options": [
				{"id": "present", "label": "Present", "score": 2},
				{"id": "absent", "label": "Absent", "score": 0},
			],
		},
		"followUp": {
			"triggerOptionIds": ["present"],
			"requiredWhenShown": True,
			"prompt": "Condition?",
			"options": [{"id": "excellent", "label": "Excellent", "score": 4}],
		},
		"scoring": {"method": "presence_condition_product", "domain": "access"},
		"responseBinding": {
			"presenceItemId": "CUSTOM#1",
			"choiceId": "custom-choice",
			"conditionItemId": "CUSTOM#2",
		},
	}
	contract = scoring_contract_from_instrument(_instrument_with_question(question))

	# When the pure engine scores the paired response
	snapshot = build_canonical_score_snapshot(
		{
			"CUSTOM#1": {"custom-choice": "present"},
			"CUSTOM#2": {"custom-choice": "excellent"},
		},
		{},
		contract=contract,
	)

	# Then both scales come from the resolved instrument contract
	assert snapshot["raw"]["item_scores"] == {"access.custom": 8}
	assert snapshot["raw"]["total_score"] == 8


def test_malformed_authoring_v2_scored_binding_fails_visibly() -> None:
	# Given a paired question whose immutable response binding omits its condition item
	question = _option_score_question() | {
		"followUp": {
			"triggerOptionIds": ["bonus"],
			"requiredWhenShown": True,
			"prompt": "Condition?",
			"options": [{"id": "good", "label": "Good", "score": 3}],
		},
		"scoring": {"method": "presence_condition_product", "domain": "access"},
		"responseBinding": {
			"presenceItemId": "CUSTOM#1",
			"choiceId": "custom-choice",
			"conditionItemId": None,
		},
	}

	# When contract resolution crosses the malformed authoring boundary
	with pytest.raises(ScoringContractResolutionError) as raised:
		scoring_contract_from_instrument(_instrument_with_question(question))

	# Then callers receive a structured, question-specific failure instead of legacy fallback
	assert raised.value.code == "missing_condition_item_binding"
	assert raised.value.question_id == "access.custom"
	assert raised.value.field == "responseBinding.conditionItemId"


def test_invalid_authoring_v2_shape_is_wrapped_as_structured_error() -> None:
	# Given schemaVersion 2 is present but its sections are malformed
	content: dict[str, JsonValue] = {
		"survey_name": "Invalid v2",
		"version": "2",
		"scoring_items": [],
		"authoring": {"schemaVersion": 2, "sections": "not-a-list"},
	}

	# When contract resolution parses the authoring boundary
	with pytest.raises(ScoringContractResolutionError) as raised:
		scoring_contract_from_instrument(content)

	# Then it exposes a typed parse error and never falls back to schema-v1
	assert raised.value.code == "invalid_authoring_v2"
	assert raised.value.field == "sections"

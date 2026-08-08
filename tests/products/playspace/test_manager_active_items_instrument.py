from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from app.products.playspace.schemas.instrument import PlayspaceInstrumentResponse
from app.products.playspace.scoring import score_audit
from app.products.playspace.services.instrument import validate_instrument_content


INSTRUMENT_DIRECTORY = Path(__file__).parents[3] / "app" / "products" / "playspace" / "instruments"
REMOVED_QUESTION_KEYS = {"q_6_3", "q_7_2", "q_19_6"}


def _read_payload(filename: str) -> dict[str, Any]:
	return json.loads((INSTRUMENT_DIRECTORY / filename).read_text())


def _question_map(instrument: PlayspaceInstrumentResponse) -> dict[str, Any]:
	return {question.question_key: question for section in instrument.sections for question in section.questions}


def test_active_v533_applies_manager_instrument_updates() -> None:
	active_bytes = (INSTRUMENT_DIRECTORY / "pvua_v5_2.active.instrument.json").read_bytes()
	v533_bytes = (INSTRUMENT_DIRECTORY / "pvua_v5_2__v5.33.instrument.json").read_bytes()
	content = _read_payload("pvua_v5_2__v5.33.instrument.json")
	instrument = PlayspaceInstrumentResponse.model_validate(content["en"])
	validate_instrument_content(content, strict_sociability=True)
	questions = _question_map(instrument)

	assert active_bytes == v533_bytes
	assert instrument.instrument_version == "5.33"
	assert REMOVED_QUESTION_KEYS.isdisjoint(questions)
	assert questions["q_1_5"].constructs == ["usability"]
	assert questions["q_8_1"].constructs == ["usability"]

	q81_not_applicable = next(
		option
		for scale in questions["q_8_1"].scales
		for option in scale.options
		if option.label.lower().startswith("not applicable")
	)
	assert q81_not_applicable.is_not_applicable is True

	assert [scale.key.value for scale in questions["q_14_4"].scales] == ["provision", "variety"]
	frequency = questions["q_14_5"]
	assert frequency.mode.value == "survey"
	assert frequency.constructs == ["play_value"]
	assert frequency.scales[0].title == "Frequency"
	assert [option.key for option in frequency.scales[0].options] == ["never", "sometimes", "always"]
	assert [option.addition_value for option in frequency.scales[0].options] == [0, 1, 2]


def test_v532_remains_unchanged_for_historical_submissions() -> None:
	instrument = PlayspaceInstrumentResponse.model_validate(_read_payload("pvua_v5_2__v5.32.instrument.json")["en"])
	questions = _question_map(instrument)

	assert instrument.instrument_version == "5.32"
	assert REMOVED_QUESTION_KEYS.issubset(questions)
	assert set(questions["q_1_5"].constructs) == {"play_value", "usability"}
	assert set(questions["q_8_1"].constructs) == {"play_value", "usability"}
	assert [scale.key.value for scale in questions["q_14_4"].scales] == [
		"provision",
		"variety",
		"challenge",
		"sociability",
	]
	assert "q_14_5" not in questions


def _score_single_answer(
	instrument: PlayspaceInstrumentResponse,
	*,
	mode: str,
	section_key: str,
	question_key: str,
	answers: dict[str, object],
) -> dict[str, Any]:
	return cast(
		dict[str, Any],
		score_audit(
			responses_json={
				"meta": {"execution_mode": mode},
				"sections": {section_key: {"responses": {question_key: answers}}},
			},
			include_maximums=True,
			instrument=instrument,
		),
	)


def test_v533_scoring_reflects_manager_updates() -> None:
	instrument = PlayspaceInstrumentResponse.model_validate(_read_payload("pvua_v5_2__v5.33.instrument.json")["en"])

	q81_na = _score_single_answer(
		instrument,
		mode="audit",
		section_key="section_8_pathways",
		question_key="q_8_1",
		answers={"provision": "new_option"},
	)
	assert q81_na["overall"]["provision_total_max"] == 0
	assert q81_na["overall"]["play_value_total_max"] == 0
	assert q81_na["overall"]["usability_total_max"] == 0

	q15 = _score_single_answer(
		instrument,
		mode="survey",
		section_key="section_1_playspace_character_community",
		question_key="q_1_5",
		answers={"provision": "a_lot"},
	)
	assert q15["overall"]["play_value_total"] == 0
	assert q15["overall"]["play_value_total_max"] == 0
	assert q15["overall"]["usability_total"] == 3

	q144 = _score_single_answer(
		instrument,
		mode="audit",
		section_key="section_14_loose_manufactured_parts_equipment",
		question_key="q_14_4",
		answers={"provision": "a_lot", "variety": "a_lot_of_variety"},
	)
	assert q144["overall"]["play_value_total"] == 6
	assert q144["overall"]["challenge_total_max"] == 0
	assert q144["overall"]["sociability_total_max"] == 0

	q145 = _score_single_answer(
		instrument,
		mode="survey",
		section_key="section_14_loose_manufactured_parts_equipment",
		question_key="q_14_5",
		answers={"provision": "always"},
	)
	assert q145["overall"]["play_value_total"] == 2
	assert q145["overall"]["play_value_total_max"] == 2

"""Every authored answer must be addressable by exactly one option key.

The 5.40 instrument was published with 69 answers all keyed `new_option`, so
three differently scored answers on the same question collapsed into one. These
tests pin the rules that stop such a version from being stored or activated
again, and the publish-time rules that a draft is still allowed to be part-way
through.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument
from app.products.playspace.schemas.instrument import PlayspaceInstrumentResponse
from app.products.playspace.schemas.management import InstrumentActivateRequest, InstrumentCreateRequest
from app.products.playspace.scoring import score_audit
from app.products.playspace.services import instrument as instrument_service
from app.products.playspace.services.instrument import (
	MAX_OPTION_KEY_LENGTH,
	InstrumentValidationError,
	validate_instrument_content,
)

INSTRUMENT_DIRECTORY = Path(__file__).parents[3] / "app" / "products" / "playspace" / "instruments"
BUGGY_SNAPSHOT = "pvua_v5_2__v5.40.instrument.json"
REPAIRED_SNAPSHOT = "pvua_v5_2__v5.41.instrument.json"
ACTIVE_SNAPSHOT = "pvua_v5_2.active.instrument.json"


def _read_snapshot(filename: str) -> dict[str, Any]:
	return json.loads((INSTRUMENT_DIRECTORY / filename).read_text())


def _scale_option_lists(payload: dict[str, Any]) -> list[list[str]]:
	"""Collect every authored scale-option key list in one locale payload."""

	lists: list[list[str]] = []
	for guidance in payload["scale_guidance"]:
		lists.append([option["key"] for option in guidance["options"]])
	for section in payload["sections"]:
		for question in section["questions"]:
			for scale in question.get("scales", []):
				lists.append([option["key"] for option in scale["options"]])
	return lists


# ── synthetic candidates ─────────────────────────────────────────────────────
#
# Minimal hand-written content keeps each rule's failure unambiguous. Published
# snapshots are read only, never mutated on disk and never copied into fixtures.


def _scale_option(key: str, label: str, addition_value: float = 0) -> dict[str, Any]:
	return {
		"key": key,
		"label": label,
		"addition_value": addition_value,
		"boost_value": 0,
		"allows_follow_up_scales": False,
		"is_not_applicable": False,
		"is_unsure": False,
	}


def _minimal_content() -> dict[str, Any]:
	"""One section, one scaled question, one checklist question - all valid."""

	return {
		"en": {
			"instrument_key": "pvua_v5_2",
			"instrument_name": "Test instrument",
			"instrument_version": "9.0",
			"current_sheet": "test",
			"source_files": [],
			"preamble": [],
			"execution_modes": [{"key": "both", "label": "Audit & Survey", "description": None}],
			"pre_audit_questions": [],
			"scale_guidance": [
				{
					"key": "provision",
					"title": "Provision",
					"prompt": "How many?",
					"description": "Guidance",
					"selection_mode": "single",
					"options": [_scale_option("no", "No"), _scale_option("some", "Some", 1)],
				}
			],
			"sections": [
				{
					"section_key": "section_1_test",
					"title": "Test section",
					"description": None,
					"instruction": "Answer the questions.",
					"notes_prompt": None,
					"questions": [
						{
							"question_key": "q_1_1",
							"mode": "both",
							"constructs": ["play_value"],
							"domains": [],
							"section_key": "section_1_test",
							"prompt": "How many?",
							"question_type": "scaled",
							"scales": [
								{
									"key": "provision",
									"title": "Provision",
									"prompt": "How many?",
									"selection_mode": "single",
									"options": [
										_scale_option("no", "No"),
										_scale_option("some", "Some", 1),
										_scale_option("a_lot", "A lot", 2),
									],
								}
							],
							"options": [],
							"required": True,
							"display_if": None,
							"notes_prompt": None,
						},
						{
							"question_key": "q_1_2",
							"mode": "both",
							"constructs": ["usability"],
							"domains": [],
							"section_key": "section_1_test",
							"prompt": "Which of these are present?",
							"question_type": "checklist",
							"scales": [],
							"options": [
								{"key": "no", "label": "None", "description": None},
								{"key": "bench", "label": "Bench", "description": None},
							],
							"required": False,
							"display_if": None,
							"notes_prompt": None,
						},
					],
				}
			],
			"legal_documents": [],
		}
	}


def _question(content: dict[str, Any], question_key: str) -> dict[str, Any]:
	for section in content["en"]["sections"]:
		for question in section["questions"]:
			if question["question_key"] == question_key:
				return question
	raise AssertionError(f"question {question_key!r} not in the synthetic candidate")


def _scaled_options(content: dict[str, Any]) -> list[dict[str, Any]]:
	return _question(content, "q_1_1")["scales"][0]["options"]


def _draft(content: dict[str, Any]) -> dict[str, PlayspaceInstrumentResponse]:
	return validate_instrument_content(content, strict_sociability=False)


def _publish(content: dict[str, Any]) -> dict[str, PlayspaceInstrumentResponse]:
	return validate_instrument_content(content, strict_sociability=False, publish_checks=True)


# ── the published snapshots ──────────────────────────────────────────────────


def test_repaired_and_active_snapshots_pass_identity_and_publish_checks() -> None:
	# The version in use must keep saving, publishing, and reactivating unchanged.
	for filename in (REPAIRED_SNAPSHOT, ACTIVE_SNAPSHOT):
		content = _read_snapshot(filename)
		validate_instrument_content(content, strict_sociability=True)
		validate_instrument_content(content, strict_sociability=False, publish_checks=True)


def test_buggy_snapshot_is_the_recorded_failure_and_the_repair_fixed_it() -> None:
	buggy = _read_snapshot(BUGGY_SNAPSHOT)["en"]
	repaired = _read_snapshot(REPAIRED_SNAPSHOT)["en"]

	buggy_lists = _scale_option_lists(buggy)
	placeholder_rows = sum(keys.count("new_option") for keys in buggy_lists)
	duplicate_lists = [keys for keys in buggy_lists if len(set(keys)) != len(keys)]
	assert placeholder_rows == 69
	assert len(duplicate_lists) == 18

	repaired_lists = _scale_option_lists(repaired)
	assert not any("new_option" in keys for keys in repaired_lists)
	assert all(len(set(keys)) == len(keys) for keys in repaired_lists)


def test_buggy_snapshot_is_rejected_for_saving_and_for_activation() -> None:
	content = _read_snapshot(BUGGY_SNAPSHOT)
	for kwargs in (
		{"strict_sociability": False},
		{"strict_sociability": False, "publish_checks": True},
		{"strict_sociability": True},
	):
		with pytest.raises(InstrumentValidationError, match="placeholder key 'new_option'"):
			validate_instrument_content(content, **cast(Any, kwargs))


def test_repaired_snapshot_scores_each_answer_separately() -> None:
	# The user-visible symptom: No / Some / A lot all scored the same under 5.40.
	def provision_total(filename: str, option_index: int) -> float:
		payload = _read_snapshot(filename)["en"]
		instrument = PlayspaceInstrumentResponse.model_validate(payload)
		question = next(
			question
			for section in instrument.sections
			for question in section.questions
			if question.question_key == "q_12_13"
		)
		scale = next(scale for scale in question.scales if scale.key.value == "provision")
		section_key = next(
			section.section_key
			for section in instrument.sections
			for candidate in section.questions
			if candidate.question_key == "q_12_13"
		)
		responses_json = {
			"meta": {"execution_mode": "audit"},
			"sections": {
				section_key: {"responses": {"q_12_13": {"provision": scale.options[option_index].key}}},
			},
		}
		scores = score_audit(responses_json=responses_json, instrument=instrument)
		# q_12_13 is an onsite-audit question, so its score lands in that partition.
		audit_partition = scores["audit"]
		assert isinstance(audit_partition, dict)
		provision_total = audit_partition["provision_total"]
		assert isinstance(provision_total, (int, float))
		return float(provision_total)

	buggy_totals = [provision_total(BUGGY_SNAPSHOT, index) for index in range(3)]
	repaired_totals = [provision_total(REPAIRED_SNAPSHOT, index) for index in range(3)]

	assert buggy_totals[0] == buggy_totals[1] == buggy_totals[2]
	assert len(set(repaired_totals)) == 3
	assert repaired_totals[0] < repaired_totals[1] < repaired_totals[2]


# ── option identity rules ────────────────────────────────────────────────────


def test_a_valid_candidate_passes_every_check() -> None:
	_draft(_minimal_content())
	_publish(_minimal_content())


@pytest.mark.parametrize(
	("bad_key", "expected"),
	[
		("", "needs a key"),
		("   ", "needs a key"),
		(" padded", "starts or ends with a space"),
		("padded ", "starts or ends with a space"),
		("x" * (MAX_OPTION_KEY_LENGTH + 1), "the limit is 80"),
		("new_option", "placeholder key"),
		("no_diversity", "reserved"),
	],
)
def test_unusable_option_keys_are_rejected_on_every_save(bad_key: str, expected: str) -> None:
	content = _minimal_content()
	_scaled_options(content)[2]["key"] = bad_key
	with pytest.raises(InstrumentValidationError, match=expected):
		_draft(content)


def test_a_key_of_exactly_the_column_length_is_accepted() -> None:
	content = _minimal_content()
	_scaled_options(content)[2]["key"] = "a" * MAX_OPTION_KEY_LENGTH
	_draft(content)


def test_existing_punctuation_and_non_ascii_keys_are_not_normalized() -> None:
	# A published checklist key is `tools (hammers_and_nails,_saws,_brushes)`;
	# authoring must keep accepting what is already stored.
	content = _minimal_content()
	_question(content, "q_1_2")["options"][1]["key"] = "tools (hammers_and_nails,_saws,_brushes)"
	_scaled_options(content)[2]["key"] = "grünfläche"
	parsed = _draft(content)
	keys = [option.key for option in parsed["en"].sections[0].questions[1].options]
	assert keys == ["no", "tools (hammers_and_nails,_saws,_brushes)"]


def test_two_answers_in_one_list_cannot_share_a_key() -> None:
	content = _minimal_content()
	_scaled_options(content)[2]["key"] = "some"
	with pytest.raises(InstrumentValidationError, match="repeats the key 'some'"):
		_draft(content)


def test_the_same_key_may_be_reused_by_different_owners() -> None:
	# `no` on the provision scale and `no` on the checklist are different answers.
	content = _minimal_content()
	assert _scaled_options(content)[0]["key"] == "no"
	assert _question(content, "q_1_2")["options"][0]["key"] == "no"
	_draft(content)


def test_ingest_aliases_are_reserved_for_scales_only() -> None:
	# Arriving audits rewrite `no_diversity` on a scale answer; a checklist answer
	# is read straight through, so the same string is not reserved there.
	content = _minimal_content()
	_question(content, "q_1_2")["options"][1]["key"] = "no_diversity"
	_draft(content)


def test_pre_audit_and_guidance_option_lists_are_checked_too() -> None:
	content = _minimal_content()
	content["en"]["scale_guidance"][0]["options"][1]["key"] = "no"
	with pytest.raises(InstrumentValidationError, match="scale guidance 'provision'"):
		_draft(content)

	content = _minimal_content()
	content["en"]["pre_audit_questions"] = [
		{
			"key": "weather_conditions",
			"label": "Weather",
			"description": None,
			"input_type": "multi_select",
			"required": False,
			"options": [
				{"key": "sun", "label": "Sunny", "description": None},
				{"key": "sun", "label": "Also sunny", "description": None},
			],
			"page_key": "space_setup",
			"visible_modes": ["both"],
			"group_key": None,
		}
	]
	with pytest.raises(InstrumentValidationError, match="pre-audit question 'weather_conditions'"):
		_draft(content)


# ── owner identity rules ─────────────────────────────────────────────────────


def test_owners_are_checked_before_their_option_lists() -> None:
	# With two questions sharing a key, an option error below them cannot be
	# reported against one question, so the owner failure must come first.
	content = _minimal_content()
	_question(content, "q_1_2")["question_key"] = "q_1_1"
	_scaled_options(content)[2]["key"] = "some"
	with pytest.raises(InstrumentValidationError, match="Question 2 repeats the key 'q_1_1'"):
		_draft(content)


def test_duplicate_sections_and_repeated_scales_are_rejected() -> None:
	content = _minimal_content()
	content["en"]["sections"].append(deepcopy(content["en"]["sections"][0]))
	with pytest.raises(InstrumentValidationError, match="Section 2 repeats the key"):
		_draft(content)

	content = _minimal_content()
	question = _question(content, "q_1_1")
	question["scales"].append(deepcopy(question["scales"][0]))
	with pytest.raises(InstrumentValidationError, match="Scale 2 repeats the key 'provision'"):
		_draft(content)


def test_a_blank_section_key_is_rejected() -> None:
	content = _minimal_content()
	content["en"]["sections"][0]["section_key"] = " "
	with pytest.raises(InstrumentValidationError, match="Section 1 needs a key"):
		_draft(content)


# ── publish-only readiness ───────────────────────────────────────────────────


def _with_condition(content: dict[str, Any], **overrides: Any) -> dict[str, Any]:
	condition = {"question_key": "q_1_1", "response_key": "provision", "any_of_option_keys": ["some"]}
	condition.update(overrides)
	_question(content, "q_1_2")["display_if"] = condition
	return content


def test_a_resolvable_follow_up_question_publishes() -> None:
	_publish(_with_condition(_minimal_content()))


@pytest.mark.parametrize(
	("overrides", "expected"),
	[
		({"question_key": "q_1_2"}, "cannot depend on the same question"),
		({"question_key": "q_9_9"}, "not in this section"),
		({"question_key": ""}, "must name the question"),
		({"response_key": "variety"}, "no such scale"),
		({"any_of_option_keys": []}, "at least one answer"),
		({"any_of_option_keys": ["some", "plenty"]}, "no longer offers"),
	],
)
def test_unresolvable_follow_up_questions_block_publication(overrides: dict[str, Any], expected: str) -> None:
	content = _with_condition(_minimal_content(), **overrides)
	with pytest.raises(InstrumentValidationError, match=expected):
		_publish(content)


def test_a_draft_may_still_be_working_on_its_follow_up_questions() -> None:
	# Identities are already sound; only publication waits for the condition.
	_draft(_with_condition(_minimal_content(), any_of_option_keys=["plenty"]))


def test_a_checklist_parent_must_be_read_through_its_own_answer_field() -> None:
	content = _minimal_content()
	_question(content, "q_1_1")["display_if"] = {
		"question_key": "q_1_2",
		"response_key": "provision",
		"any_of_option_keys": ["bench"],
	}
	with pytest.raises(InstrumentValidationError, match="answers under 'selected_option_keys'"):
		_publish(content)

	content = _minimal_content()
	_question(content, "q_1_1")["display_if"] = {
		"question_key": "q_1_2",
		"response_key": "selected_option_keys",
		"any_of_option_keys": ["bench"],
	}
	_publish(content)


def test_a_follow_up_cannot_outlive_the_question_it_depends_on() -> None:
	content = _with_condition(_minimal_content())
	_question(content, "q_1_1")["mode"] = "audit"
	_question(content, "q_1_2")["mode"] = "both"
	with pytest.raises(InstrumentValidationError, match="not shown in every workflow"):
		_publish(content)

	_question(content, "q_1_2")["mode"] = "audit"
	_publish(content)


def test_questions_that_reveal_each_other_in_a_loop_block_publication() -> None:
	content = _with_condition(_minimal_content())
	_question(content, "q_1_1")["display_if"] = {
		"question_key": "q_1_2",
		"response_key": "selected_option_keys",
		"any_of_option_keys": ["bench"],
	}
	with pytest.raises(InstrumentValidationError, match="depend on each other in a loop"):
		_publish(content)


def test_an_answer_without_a_label_can_be_drafted_but_not_published() -> None:
	content = _minimal_content()
	_scaled_options(content)[2]["label"] = "  "
	_draft(content)
	with pytest.raises(InstrumentValidationError, match="needs a label"):
		_publish(content)


def test_a_translation_must_answer_with_the_same_keys_in_the_same_order() -> None:
	content = _minimal_content()
	content["en"]["pre_audit_questions"] = [
		{
			"key": "season",
			"label": "Season",
			"description": None,
			"input_type": "single_select",
			"required": False,
			"options": [{"key": "summer", "label": "Summer", "description": None}],
			"page_key": "space_setup",
			"visible_modes": ["both"],
			"group_key": None,
		}
	]
	content["de"] = deepcopy(content["en"])
	content["de"]["instrument_name"] = "Testinstrument"
	_publish(content)

	reordered = deepcopy(content)
	german_options = reordered["de"]["sections"][0]["questions"][0]["scales"][0]["options"]
	german_options[1], german_options[2] = german_options[2], german_options[1]
	_draft(reordered)
	with pytest.raises(InstrumentValidationError, match="does not match 'en'"):
		_publish(reordered)

	dropped = deepcopy(content)
	dropped["de"]["pre_audit_questions"] = []
	with pytest.raises(InstrumentValidationError, match="missing answer lists"):
		_publish(dropped)


# ── enforcement at the write boundary ────────────────────────────────────────


class _RecordingSession:
	"""Async-session stand-in that records whether anything was written."""

	def __init__(self) -> None:
		self.execute_count = 0
		self.added: list[object] = []
		self.commit_count = 0

	async def execute(self, _statement: object) -> None:
		self.execute_count += 1

	def add(self, value: object) -> None:
		self.added.append(value)

	async def commit(self) -> None:
		self.commit_count += 1

	async def refresh(self, _value: object) -> None:
		return None


def _as_async_session(session: _RecordingSession) -> AsyncSession:
	return cast(AsyncSession, session)


def _colliding_content() -> dict[str, Any]:
	content = _minimal_content()
	_scaled_options(content)[2]["key"] = "new_option"
	return content


def test_saving_a_draft_with_colliding_keys_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
	session = _RecordingSession()
	request = InstrumentCreateRequest(
		instrument_key="pvua_v5_2",
		instrument_version="9.0",
		content=_colliding_content(),
	)

	async def fake_list_instrument_versions(_session: object, _key: str) -> list[Instrument]:
		return []

	monkeypatch.setattr(instrument_service, "list_instrument_versions", fake_list_instrument_versions)
	with pytest.raises(InstrumentValidationError, match="placeholder key"):
		asyncio.run(instrument_service.create_instrument_version(_as_async_session(session), request, activate=False))

	assert session.execute_count == 0
	assert session.added == []
	assert session.commit_count == 0


def test_publishing_content_with_colliding_keys_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
	session = _RecordingSession()
	request = InstrumentCreateRequest(
		instrument_key="pvua_v5_2",
		instrument_version="9.0",
		content=_colliding_content(),
	)

	async def fake_list_instrument_versions(_session: object, _key: str) -> list[Instrument]:
		return []

	monkeypatch.setattr(instrument_service, "list_instrument_versions", fake_list_instrument_versions)
	with pytest.raises(InstrumentValidationError, match="placeholder key"):
		asyncio.run(instrument_service.create_instrument_version(_as_async_session(session), request, activate=True))

	assert session.execute_count == 0
	assert session.added == []
	assert session.commit_count == 0


def test_activating_a_stored_row_with_colliding_keys_changes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
	session = _RecordingSession()
	instrument_id = uuid.uuid4()
	parent_id = uuid.uuid4()
	row = Instrument(
		id=instrument_id,
		instrument_key="pvua_v5_2",
		instrument_version="9.0.1",
		parent_instrument_id=parent_id,
		is_active=False,
		content=_colliding_content(),
	)

	async def fake_get_instrument_by_id(_session: object, _instrument_id: uuid.UUID) -> Instrument:
		return row

	async def fake_list_instrument_versions(_session: object, _key: str) -> list[Instrument]:
		return []

	monkeypatch.setattr(instrument_service, "get_instrument_by_id", fake_get_instrument_by_id)
	monkeypatch.setattr(instrument_service, "list_instrument_versions", fake_list_instrument_versions)
	with pytest.raises(InstrumentValidationError, match="placeholder key"):
		asyncio.run(
			instrument_service.update_instrument_status(
				_as_async_session(session),
				instrument_id,
				InstrumentActivateRequest(is_active=True),
			)
		)

	assert session.execute_count == 0
	assert session.commit_count == 0
	assert row.is_active is False
	assert row.parent_instrument_id == parent_id
	assert row.instrument_version == "9.0.1"


def test_a_sound_draft_still_saves(monkeypatch: pytest.MonkeyPatch) -> None:
	session = _RecordingSession()
	request = InstrumentCreateRequest(
		instrument_key="pvua_v5_2",
		instrument_version="9.0",
		content=_minimal_content(),
	)

	async def fake_list_instrument_versions(_session: object, _key: str) -> list[Instrument]:
		return []

	monkeypatch.setattr(instrument_service, "list_instrument_versions", fake_list_instrument_versions)
	result = asyncio.run(
		instrument_service.create_instrument_version(_as_async_session(session), request, activate=False)
	)

	assert result is not None
	assert len(session.added) == 1
	assert session.commit_count == 1

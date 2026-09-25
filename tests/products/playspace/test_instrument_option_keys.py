"""Every authored answer must be addressable by exactly one option key.

An answer left on the editor's `new_option` placeholder shares its key with every
other unnamed answer, so differently scored answers on one question collapse into
one. These tests pin the rules that stop such content from being stored or
activated, and the publish-time rules that a draft is still allowed to be
part-way through.

Each rule runs against the shared builder's small instrument. The active
snapshot is the only instrument file read here: whichever version is live must
keep passing every identity and publish check.
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
from tests.products.playspace import _instrument_builders as builders

# The sync that exports instruments from the database always writes this snapshot
# for the live version, so it is safe to read whichever version that is.
ACTIVE_SNAPSHOT = (
	Path(__file__).parents[3]
	/ "app"
	/ "products"
	/ "playspace"
	/ "instruments"
	/ f"{builders.INSTRUMENT_KEY}.active.instrument.json"
)


# ── candidate content ────────────────────────────────────────────────────────
#
# The shared builder's minimal instrument keeps each rule's failure unambiguous.
# The active snapshot is read only, never mutated on disk and never copied into
# fixtures.


def _scaled_options(content: dict[str, Any]) -> list[dict[str, Any]]:
	return builders.scale(content, builders.SCALED_QUESTION_KEY, "provision")["options"]


def _draft(content: dict[str, Any]) -> dict[str, PlayspaceInstrumentResponse]:
	return validate_instrument_content(content, strict_sociability=False)


def _publish(content: dict[str, Any]) -> dict[str, PlayspaceInstrumentResponse]:
	return validate_instrument_content(content, strict_sociability=False, publish_checks=True)


# ── the live instrument ──────────────────────────────────────────────────────


def test_the_active_instrument_passes_identity_and_publish_checks() -> None:
	# The version in use must keep saving, publishing, and reactivating unchanged.
	content = json.loads(ACTIVE_SNAPSHOT.read_text())
	validate_instrument_content(content, strict_sociability=True)
	validate_instrument_content(content, strict_sociability=False, publish_checks=True)


# ── answers left on the editor placeholder ───────────────────────────────────


@pytest.mark.parametrize(
	"kwargs",
	[
		{"strict_sociability": False},
		{"strict_sociability": False, "publish_checks": True},
		{"strict_sociability": True},
	],
	ids=["save", "publish", "activate"],
)
def test_answers_left_on_the_placeholder_key_are_rejected_for_saving_and_for_activation(
	kwargs: dict[str, Any],
) -> None:
	# Carries Sociability so activation rejects the placeholder, not a missing scale.
	content = builders.minimal_content(sociability="multiple")
	validate_instrument_content(content, **kwargs)

	# Every answer on the list is left on the placeholder, so the keys also repeat. The
	# placeholder message must win over the repeated-key one: it tells the author that
	# each answer needs its own key, which fixes both problems.
	for option in _scaled_options(content):
		option["key"] = "new_option"
	with pytest.raises(InstrumentValidationError, match="placeholder key 'new_option'"):
		validate_instrument_content(content, **kwargs)


def _multiplier_scale(key: str, title: str) -> dict[str, Any]:
	"""A three-answer Variety or Challenge scale that Some and A lot unlock."""

	return {
		"key": key,
		"title": title,
		"prompt": f"How much {key}?",
		"selection_mode": "single",
		"options": [
			builders.scale_option(f"no_{key}", "None", 0, 1),
			builders.scale_option(f"some_{key}", "Some", 1, 2),
			builders.scale_option(f"a_lot_of_{key}", "A lot", 2, 3),
		],
	}


def test_each_provision_answer_on_a_question_scores_its_own_points() -> None:
	# Answers that share a key score as one; distinct keys must keep No / Some / A lot apart.
	content = builders.minimal_content(sociability="multiple")
	question = builders.question(content, builders.SCALED_QUESTION_KEY)
	# An onsite-audit question, so its score lands in the audit partition.
	question["mode"] = "audit"
	# Some and A lot unlock Variety, Challenge and Sociability. Those follow-ups stay
	# unanswered: each Provision answer keeps its own points while they are still open.
	question["scales"][1:1] = [_multiplier_scale("variety", "Variety"), _multiplier_scale("challenge", "Challenge")]
	instrument = builders.parse(content)
	options = _scaled_options(content)
	assert [option["allows_follow_up_scales"] for option in options] == [False, True, True]

	def provision_total(option_key: str) -> float:
		responses_json = {
			"meta": {"execution_mode": "audit"},
			"sections": {
				builders.SECTION_KEY: {"responses": {builders.SCALED_QUESTION_KEY: {"provision": option_key}}},
			},
		}
		scores = score_audit(responses_json=responses_json, instrument=instrument)
		audit_partition = scores["audit"]
		assert isinstance(audit_partition, dict)
		total = audit_partition["provision_total"]
		assert isinstance(total, (int, float))
		return float(total)

	totals = [provision_total(option["key"]) for option in options]

	assert len(set(totals)) == 3
	assert totals[0] < totals[1] < totals[2]
	assert totals == [float(option["addition_value"]) for option in options]


# ── option identity rules ────────────────────────────────────────────────────


def test_a_valid_candidate_passes_every_check() -> None:
	_draft(builders.minimal_content())
	_publish(builders.minimal_content())


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
	content = builders.minimal_content()
	_scaled_options(content)[2]["key"] = bad_key
	with pytest.raises(InstrumentValidationError, match=expected):
		_draft(content)


def test_a_key_of_exactly_the_column_length_is_accepted() -> None:
	content = builders.minimal_content()
	_scaled_options(content)[2]["key"] = "a" * MAX_OPTION_KEY_LENGTH
	_draft(content)


def test_existing_punctuation_and_non_ascii_keys_are_not_normalized() -> None:
	# A published checklist key is `tools (hammers_and_nails,_saws,_brushes)`;
	# authoring must keep accepting what is already stored.
	content = builders.minimal_content()
	builders.question(content, builders.CHECKLIST_QUESTION_KEY)["options"][1]["key"] = (
		"tools (hammers_and_nails,_saws,_brushes)"
	)
	_scaled_options(content)[2]["key"] = "grünfläche"
	parsed = _draft(content)
	keys = [option.key for option in parsed["en"].sections[0].questions[1].options]
	assert keys == ["no", "tools (hammers_and_nails,_saws,_brushes)"]


def test_two_answers_in_one_list_cannot_share_a_key() -> None:
	content = builders.minimal_content()
	_scaled_options(content)[2]["key"] = "some"
	with pytest.raises(InstrumentValidationError, match="repeats the key 'some'"):
		_draft(content)


def test_the_same_key_may_be_reused_by_different_owners() -> None:
	# `no` on the provision scale and `no` on the checklist are different answers.
	content = builders.minimal_content()
	assert _scaled_options(content)[0]["key"] == "no"
	assert builders.question(content, builders.CHECKLIST_QUESTION_KEY)["options"][0]["key"] == "no"
	_draft(content)


def test_ingest_aliases_are_reserved_for_scales_only() -> None:
	# Arriving audits rewrite `no_diversity` on a scale answer; a checklist answer
	# is read straight through, so the same string is not reserved there.
	content = builders.minimal_content()
	builders.question(content, builders.CHECKLIST_QUESTION_KEY)["options"][1]["key"] = "no_diversity"
	_draft(content)


def test_pre_audit_and_guidance_option_lists_are_checked_too() -> None:
	content = builders.minimal_content()
	builders.guidance(content, "provision")["options"][1]["key"] = "no"
	with pytest.raises(InstrumentValidationError, match="scale guidance 'provision'"):
		_draft(content)

	content = builders.minimal_content()
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
	content = builders.minimal_content()
	builders.question(content, builders.CHECKLIST_QUESTION_KEY)["question_key"] = builders.SCALED_QUESTION_KEY
	_scaled_options(content)[2]["key"] = "some"
	with pytest.raises(InstrumentValidationError, match=f"Question 2 repeats the key '{builders.SCALED_QUESTION_KEY}'"):
		_draft(content)


def test_duplicate_sections_and_repeated_scales_are_rejected() -> None:
	content = builders.minimal_content()
	content["en"]["sections"].append(deepcopy(content["en"]["sections"][0]))
	with pytest.raises(InstrumentValidationError, match="Section 2 repeats the key"):
		_draft(content)

	content = builders.minimal_content()
	question = builders.question(content, builders.SCALED_QUESTION_KEY)
	question["scales"].append(deepcopy(question["scales"][0]))
	with pytest.raises(InstrumentValidationError, match="Scale 2 repeats the key 'provision'"):
		_draft(content)


def test_a_blank_section_key_is_rejected() -> None:
	content = builders.minimal_content()
	content["en"]["sections"][0]["section_key"] = " "
	with pytest.raises(InstrumentValidationError, match="Section 1 needs a key"):
		_draft(content)


# ── publish-only readiness ───────────────────────────────────────────────────


def _with_condition(content: dict[str, Any], **overrides: Any) -> dict[str, Any]:
	condition = {
		"question_key": builders.SCALED_QUESTION_KEY,
		"response_key": "provision",
		"any_of_option_keys": ["some"],
	}
	condition.update(overrides)
	builders.question(content, builders.CHECKLIST_QUESTION_KEY)["display_if"] = condition
	return content


def test_a_resolvable_follow_up_question_publishes() -> None:
	_publish(_with_condition(builders.minimal_content()))


@pytest.mark.parametrize(
	("overrides", "expected"),
	[
		({"question_key": builders.CHECKLIST_QUESTION_KEY}, "cannot depend on the same question"),
		({"question_key": "q_9_9"}, "not in this section"),
		({"question_key": ""}, "must name the question"),
		({"response_key": "variety"}, "no such scale"),
		({"any_of_option_keys": []}, "at least one answer"),
		({"any_of_option_keys": ["some", "plenty"]}, "no longer offers"),
	],
)
def test_unresolvable_follow_up_questions_block_publication(overrides: dict[str, Any], expected: str) -> None:
	content = _with_condition(builders.minimal_content(), **overrides)
	with pytest.raises(InstrumentValidationError, match=expected):
		_publish(content)


def test_a_draft_may_still_be_working_on_its_follow_up_questions() -> None:
	# Identities are already sound; only publication waits for the condition.
	_draft(_with_condition(builders.minimal_content(), any_of_option_keys=["plenty"]))


def test_a_checklist_parent_must_be_read_through_its_own_answer_field() -> None:
	content = builders.minimal_content()
	builders.question(content, builders.SCALED_QUESTION_KEY)["display_if"] = {
		"question_key": builders.CHECKLIST_QUESTION_KEY,
		"response_key": "provision",
		"any_of_option_keys": ["bench"],
	}
	with pytest.raises(InstrumentValidationError, match="answers under 'selected_option_keys'"):
		_publish(content)

	content = builders.minimal_content()
	builders.question(content, builders.SCALED_QUESTION_KEY)["display_if"] = {
		"question_key": builders.CHECKLIST_QUESTION_KEY,
		"response_key": "selected_option_keys",
		"any_of_option_keys": ["bench"],
	}
	_publish(content)


def test_a_follow_up_cannot_outlive_the_question_it_depends_on() -> None:
	content = _with_condition(builders.minimal_content())
	builders.question(content, builders.SCALED_QUESTION_KEY)["mode"] = "audit"
	builders.question(content, builders.CHECKLIST_QUESTION_KEY)["mode"] = "both"
	with pytest.raises(InstrumentValidationError, match="not shown in every workflow"):
		_publish(content)

	builders.question(content, builders.CHECKLIST_QUESTION_KEY)["mode"] = "audit"
	_publish(content)


def test_questions_that_reveal_each_other_in_a_loop_block_publication() -> None:
	content = _with_condition(builders.minimal_content())
	builders.question(content, builders.SCALED_QUESTION_KEY)["display_if"] = {
		"question_key": builders.CHECKLIST_QUESTION_KEY,
		"response_key": "selected_option_keys",
		"any_of_option_keys": ["bench"],
	}
	with pytest.raises(InstrumentValidationError, match="depend on each other in a loop"):
		_publish(content)


def test_an_answer_without_a_label_can_be_drafted_but_not_published() -> None:
	content = builders.minimal_content()
	_scaled_options(content)[2]["label"] = "  "
	_draft(content)
	with pytest.raises(InstrumentValidationError, match="needs a label"):
		_publish(content)


def test_a_translation_must_answer_with_the_same_keys_in_the_same_order() -> None:
	content = builders.minimal_content()
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
	content = builders.minimal_content()
	_scaled_options(content)[2]["key"] = "new_option"
	return content


def test_saving_a_draft_with_colliding_keys_writes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
	session = _RecordingSession()
	request = InstrumentCreateRequest(
		instrument_key=builders.INSTRUMENT_KEY,
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
		instrument_key=builders.INSTRUMENT_KEY,
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
		instrument_key=builders.INSTRUMENT_KEY,
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
		instrument_key=builders.INSTRUMENT_KEY,
		instrument_version="9.0",
		content=builders.minimal_content(),
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

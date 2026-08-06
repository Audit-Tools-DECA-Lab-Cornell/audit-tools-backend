from __future__ import annotations

import asyncio
import json
import uuid
from copy import deepcopy
from pathlib import Path
from random import Random
from typing import Any, Callable, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument
from app.products.playspace.schemas.instrument import PlayspaceInstrumentResponse
from app.products.playspace.schemas.management import InstrumentActivateRequest, InstrumentCreateRequest
from app.products.playspace.seed_data import _build_question_answers
from app.products.playspace.services import instrument as instrument_service
from app.products.playspace.scoring_metadata import build_scoring_sections_from_instrument

INSTRUMENT_DIRECTORY = Path(__file__).parents[3] / "app" / "products" / "playspace" / "instruments"
EXPECTED_PROMPT = "Does this feature/environmental characteristic provide opportunities for a child to"
EXPECTED_KEYS = ["play_alone", "small_group", "large_group"]
EXPECTED_LABELS = [
	"Play on their own",
	"Play together in a small group (1-4 other users)",
	"Play together in a larger group (5 or more other users)",
]


def _read_localized_payload(filename: str) -> dict[str, Any]:
	return json.loads((INSTRUMENT_DIRECTORY / filename).read_text())


def _semantically_invalid_multiple_content() -> dict[str, Any]:
	content = deepcopy(_read_localized_payload("pvua_v5_2__v5.31.instrument.json"))
	for section in content["en"]["sections"]:
		for question in section["questions"]:
			for scale in question.get("scales", []):
				if scale["key"] == "sociability":
					scale["selection_mode"] = "multiple"
					return content
	raise AssertionError("Expected at least one Sociability scale")


class _RecordingSession:
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


def test_candidate_v532_has_exact_multiselect_sociability_contract() -> None:
	candidate = _read_localized_payload("pvua_v5_2__v5.32.instrument.json")
	parsed = PlayspaceInstrumentResponse.model_validate(candidate["en"])
	instrument_service.validate_instrument_content(candidate, strict_sociability=True)

	sociability_scales = [
		scale
		for section in parsed.sections
		for question in section.questions
		for scale in question.scales
		if scale.key.value == "sociability"
	]
	assert parsed.instrument_version == "5.32"
	assert len(sociability_scales) == 34
	assert all(scale.selection_mode == "multiple" for scale in sociability_scales)
	assert all(scale.prompt == EXPECTED_PROMPT for scale in sociability_scales)
	assert all([option.key for option in scale.options] == EXPECTED_KEYS for scale in sociability_scales)
	assert all([option.label for option in scale.options] == EXPECTED_LABELS for scale in sociability_scales)
	assert all(
		option.addition_value == 1 and option.boost_value == 1
		for scale in sociability_scales
		for option in scale.options
	)
	assert any(question.question_key == "q_14_4" for section in parsed.sections for question in section.questions)

	sociability_guidance = next(guidance for guidance in parsed.scale_guidance if guidance.key.value == "sociability")
	assert sociability_guidance.selection_mode == "multiple"
	assert sociability_guidance.prompt == EXPECTED_PROMPT
	assert [option.key for option in sociability_guidance.options] == EXPECTED_KEYS
	assert [option.label for option in sociability_guidance.options] == EXPECTED_LABELS
	assert any("multiple selections" in paragraph.lower() for paragraph in parsed.preamble)
	assert any("equal opportunities" in paragraph.lower() for paragraph in parsed.preamble)


def test_active_instrument_matches_v532_and_v531_remains_single_select() -> None:
	active_bytes = (INSTRUMENT_DIRECTORY / "pvua_v5_2.active.instrument.json").read_bytes()
	v532_bytes = (INSTRUMENT_DIRECTORY / "pvua_v5_2__v5.32.instrument.json").read_bytes()
	v531_bytes = (INSTRUMENT_DIRECTORY / "pvua_v5_2__v5.31.instrument.json").read_bytes()
	active = json.loads(active_bytes)["en"]
	v531 = json.loads(v531_bytes)["en"]

	assert active_bytes == v532_bytes
	assert active["instrument_version"] == "5.32"
	assert (
		sum(
			1
			for section in active["sections"]
			for question in section["questions"]
			for scale in question.get("scales", [])
			if scale["key"] == "sociability"
		)
		== 34
	)
	assert all(
		scale.get("selection_mode") == "multiple"
		for section in active["sections"]
		for question in section["questions"]
		for scale in question.get("scales", [])
		if scale["key"] == "sociability"
	)
	assert v531["instrument_version"] == "5.31"
	assert all(
		scale.get("selection_mode", "single") == "single"
		for section in v531["sections"]
		for question in section["questions"]
		for scale in question.get("scales", [])
		if scale["key"] == "sociability"
	)
	assert any(
		question["question_key"] == "q_14_4" for section in active["sections"] for question in section["questions"]
	)


def test_seed_answer_generation_supports_candidate_multiple_sociability() -> None:
	candidate = PlayspaceInstrumentResponse.model_validate(
		_read_localized_payload("pvua_v5_2__v5.32.instrument.json")["en"]
	)
	question = next(
		question
		for section in build_scoring_sections_from_instrument(candidate)
		for question in section.questions
		if any(scale.key == "sociability" for scale in question.scales)
	)
	generated_multiple_answers: list[list[str]] = []
	for seed in range(50):
		answers = _build_question_answers(
			question=question,
			quality_bias=0.8,
			usage_bias=0.8,
			randomizer=Random(seed),
		)
		sociability_answer = answers.get("sociability")
		if sociability_answer is not None:
			assert isinstance(sociability_answer, list)
			generated_multiple_answers.append(sociability_answer)

	assert generated_multiple_answers
	assert all(answer for answer in generated_multiple_answers)
	assert all(set(answer) <= set(EXPECTED_KEYS) for answer in generated_multiple_answers)


@pytest.mark.parametrize(
	("mutation", "message"),
	[
		(lambda scale: scale.update(prompt="Wrong prompt"), "prompt"),
		(lambda scale: scale["options"].reverse(), "ordered keys"),
		(lambda scale: scale["options"][0].update(addition_value=2), "addition_value"),
		(lambda scale: scale["options"][0].update(boost_value=2), "boost_value"),
		(lambda scale: scale["options"][0].update(is_unsure=True), "Unsure"),
		(lambda scale: scale["options"][0].update(is_not_applicable=True), "not-applicable"),
	],
)
def test_strict_semantic_validation_rejects_invalid_multiple_sociability(
	mutation: Callable[[dict[str, Any]], object],
	message: str,
) -> None:
	content = deepcopy(_read_localized_payload("pvua_v5_2__v5.32.instrument.json"))
	scale = next(
		scale
		for section in content["en"]["sections"]
		for question in section["questions"]
		for scale in question.get("scales", [])
		if scale["key"] == "sociability"
	)
	mutation(scale)

	with pytest.raises(ValueError, match=message):
		instrument_service.validate_instrument_content(content, strict_sociability=True)


def test_strict_semantic_validation_rejects_one_unconverted_assigned_scale() -> None:
	content = deepcopy(_read_localized_payload("pvua_v5_2__v5.32.instrument.json"))
	scale = next(
		scale
		for section in content["en"]["sections"]
		for question in section["questions"]
		for scale in question.get("scales", [])
		if scale["key"] == "sociability"
	)
	scale["selection_mode"] = "single"

	with pytest.raises(ValueError, match="selection_mode='multiple'"):
		instrument_service.validate_instrument_content(content, strict_sociability=True)


def test_strict_semantic_validation_requires_an_assigned_sociability_scale() -> None:
	content = deepcopy(_read_localized_payload("pvua_v5_2__v5.32.instrument.json"))
	for section in content["en"]["sections"]:
		for question in section["questions"]:
			question["scales"] = [scale for scale in question.get("scales", []) if scale["key"] != "sociability"]

	with pytest.raises(ValueError, match="at least one assigned Sociability scale"):
		instrument_service.validate_instrument_content(content, strict_sociability=True)


def test_strict_semantic_validation_allows_immutable_v531_legacy_contract() -> None:
	content = _read_localized_payload("pvua_v5_2__v5.31.instrument.json")

	instrument_service.validate_instrument_content(content, strict_sociability=True)


def test_strict_semantic_validation_rejects_v532_all_single_contract() -> None:
	content = deepcopy(_read_localized_payload("pvua_v5_2__v5.31.instrument.json"))
	content["en"]["instrument_version"] = "5.32"

	with pytest.raises(ValueError, match="selection_mode='multiple'"):
		instrument_service.validate_instrument_content(content, strict_sociability=True)


def test_strict_semantic_validation_rejects_v532_with_one_unconverted_assignment() -> None:
	content = deepcopy(_read_localized_payload("pvua_v5_2__v5.32.instrument.json"))
	scale = next(
		scale
		for section in content["en"]["sections"]
		for question in section["questions"]
		for scale in question.get("scales", [])
		if scale["key"] == "sociability"
	)
	scale["selection_mode"] = "single"

	with pytest.raises(ValueError, match="selection_mode='multiple'"):
		instrument_service.validate_instrument_content(content, strict_sociability=True)


def test_strict_semantic_validation_rejects_mixed_modes_even_with_legacy_version() -> None:
	content = deepcopy(_read_localized_payload("pvua_v5_2__v5.32.instrument.json"))
	content["en"]["instrument_version"] = "5.31"
	scale = next(
		scale
		for section in content["en"]["sections"]
		for question in section["questions"]
		for scale in question.get("scales", [])
		if scale["key"] == "sociability"
	)
	scale["selection_mode"] = "single"

	with pytest.raises(ValueError, match="selection_mode='multiple'"):
		instrument_service.validate_instrument_content(content, strict_sociability=True)


def test_strict_semantic_validation_allows_complete_v532_candidate() -> None:
	content = _read_localized_payload("pvua_v5_2__v5.32.instrument.json")

	instrument_service.validate_instrument_content(content, strict_sociability=True)


@pytest.mark.parametrize("mutation", ["single", "invalid_options"])
def test_strict_semantic_validation_rejects_noncanonical_sociability_guidance(mutation: str) -> None:
	content = deepcopy(_read_localized_payload("pvua_v5_2__v5.32.instrument.json"))
	guidance = next(item for item in content["en"]["scale_guidance"] if item["key"] == "sociability")
	if mutation == "single":
		guidance["selection_mode"] = "single"
	else:
		guidance["options"].reverse()

	with pytest.raises(ValueError, match="Sociability"):
		instrument_service.validate_instrument_content(content, strict_sociability=True)


def test_inactive_draft_create_allows_semantic_work_in_progress_after_base_parse(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	session = _RecordingSession()
	content = _semantically_invalid_multiple_content()
	request = InstrumentCreateRequest(
		instrument_key="pvua_v5_2",
		instrument_version="5.31.1",
		content=content,
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


def test_publish_create_rejects_invalid_semantics_before_database_writes(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	session = _RecordingSession()
	request = InstrumentCreateRequest(
		instrument_key="pvua_v5_2",
		instrument_version="5.32",
		content=_semantically_invalid_multiple_content(),
	)

	async def fake_list_instrument_versions(_session: object, _key: str) -> list[Instrument]:
		return []

	monkeypatch.setattr(instrument_service, "list_instrument_versions", fake_list_instrument_versions)
	with pytest.raises(ValueError, match="Sociability"):
		asyncio.run(instrument_service.create_instrument_version(_as_async_session(session), request, activate=True))

	assert session.execute_count == 0
	assert session.added == []
	assert session.commit_count == 0


def test_activate_rejects_invalid_semantics_before_database_writes(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	session = _RecordingSession()
	instrument_id = uuid.uuid4()
	content = _semantically_invalid_multiple_content()
	content["en"]["instrument_version"] = "5.32"
	row = Instrument(
		id=instrument_id,
		instrument_key="pvua_v5_2",
		instrument_version="5.32",
		parent_instrument_id=None,
		is_active=False,
		content=content,
	)

	async def fake_get_instrument_by_id(_session: object, _instrument_id: uuid.UUID) -> Instrument:
		return row

	monkeypatch.setattr(instrument_service, "get_instrument_by_id", fake_get_instrument_by_id)
	with pytest.raises(ValueError, match="Sociability"):
		asyncio.run(
			instrument_service.update_instrument_status(
				_as_async_session(session),
				instrument_id,
				InstrumentActivateRequest(is_active=True),
			)
		)

	assert session.execute_count == 0
	assert session.commit_count == 0


def test_reactivate_immutable_v531_legacy_instrument_is_allowed(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	session = _RecordingSession()
	instrument_id = uuid.uuid4()
	row = Instrument(
		id=instrument_id,
		instrument_key="pvua_v5_2",
		instrument_version="5.31",
		parent_instrument_id=None,
		is_active=False,
		content=_read_localized_payload("pvua_v5_2__v5.31.instrument.json"),
	)

	async def fake_get_instrument_by_id(_session: object, _instrument_id: uuid.UUID) -> Instrument:
		return row

	monkeypatch.setattr(instrument_service, "get_instrument_by_id", fake_get_instrument_by_id)
	result = asyncio.run(
		instrument_service.update_instrument_status(
			_as_async_session(session),
			instrument_id,
			InstrumentActivateRequest(is_active=True),
		)
	)

	assert result is row
	assert row.is_active is True
	assert session.commit_count == 1


def test_reactivate_immutable_nonnumeric_legacy_instrument_is_allowed(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	session = _RecordingSession()
	instrument_id = uuid.uuid4()
	content = deepcopy(_read_localized_payload("pvua_v5_2__v5.31.instrument.json"))
	content["en"]["instrument_version"] = "legacy-release"
	row = Instrument(
		id=instrument_id,
		instrument_key="pvua_v5_2",
		instrument_version="legacy-release",
		parent_instrument_id=None,
		is_active=False,
		content=content,
	)

	async def fake_get_instrument_by_id(_session: object, _instrument_id: uuid.UUID) -> Instrument:
		return row

	monkeypatch.setattr(instrument_service, "get_instrument_by_id", fake_get_instrument_by_id)
	result = asyncio.run(
		instrument_service.update_instrument_status(
			_as_async_session(session),
			instrument_id,
			InstrumentActivateRequest(is_active=True),
		)
	)

	assert result is row
	assert row.is_active is True
	assert session.commit_count == 1


def test_new_publication_rejects_nonnumeric_version_before_database_writes(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	session = _RecordingSession()
	content = deepcopy(_read_localized_payload("pvua_v5_2__v5.31.instrument.json"))
	content["en"]["instrument_version"] = "candidate"
	request = InstrumentCreateRequest(
		instrument_key="pvua_v5_2",
		instrument_version="candidate",
		content=content,
	)

	async def fake_list_instrument_versions(_session: object, _key: str) -> list[Instrument]:
		return []

	monkeypatch.setattr(instrument_service, "list_instrument_versions", fake_list_instrument_versions)
	with pytest.raises(instrument_service.InstrumentValidationError, match="numeric"):
		asyncio.run(instrument_service.create_instrument_version(_as_async_session(session), request, activate=True))

	assert session.execute_count == 0
	assert session.added == []
	assert session.commit_count == 0


def test_inactive_root_create_rejects_nonnumeric_version_before_row_exists(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	session = _RecordingSession()
	content = deepcopy(_read_localized_payload("pvua_v5_2__v5.31.instrument.json"))
	content["en"]["instrument_version"] = "wip"
	request = InstrumentCreateRequest(
		instrument_key="pvua_v5_2",
		instrument_version="wip",
		content=content,
	)

	async def fake_list_instrument_versions(_session: object, _key: str) -> list[Instrument]:
		return []

	monkeypatch.setattr(instrument_service, "list_instrument_versions", fake_list_instrument_versions)
	with pytest.raises(instrument_service.InstrumentValidationError, match="Root instrument versions must be numeric"):
		asyncio.run(instrument_service.create_instrument_version(_as_async_session(session), request, activate=False))

	assert session.added == []
	assert session.commit_count == 0


def test_inactive_root_create_keeps_numeric_version_behavior(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	session = _RecordingSession()
	request = InstrumentCreateRequest(
		instrument_key="pvua_v5_2",
		instrument_version="5.31",
		content=_read_localized_payload("pvua_v5_2__v5.31.instrument.json"),
	)

	async def fake_list_instrument_versions(_session: object, _key: str) -> list[Instrument]:
		return []

	monkeypatch.setattr(instrument_service, "list_instrument_versions", fake_list_instrument_versions)
	result = asyncio.run(
		instrument_service.create_instrument_version(_as_async_session(session), request, activate=False)
	)

	assert result is not None
	assert result.instrument_version == "5.31"
	assert result.parent_instrument_id is None
	assert result.is_active is False
	assert session.added == [result]
	assert session.commit_count == 1


def test_promoting_legacy_draft_validates_against_new_publication_version(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	session = _RecordingSession()
	instrument_id = uuid.uuid4()
	content = deepcopy(_read_localized_payload("pvua_v5_2__v5.31.instrument.json"))
	content["en"]["instrument_version"] = "5.31.1"
	row = Instrument(
		id=instrument_id,
		instrument_key="pvua_v5_2",
		instrument_version="5.31.1",
		parent_instrument_id=uuid.uuid4(),
		is_active=False,
		content=content,
	)
	published = Instrument(
		id=uuid.uuid4(),
		instrument_key="pvua_v5_2",
		instrument_version="5.31",
		parent_instrument_id=None,
		is_active=True,
		content=_read_localized_payload("pvua_v5_2__v5.31.instrument.json"),
	)

	async def fake_get_instrument_by_id(_session: object, _instrument_id: uuid.UUID) -> Instrument:
		return row

	async def fake_list_instrument_versions(_session: object, _instrument_key: str) -> list[Instrument]:
		return [published, row]

	monkeypatch.setattr(instrument_service, "get_instrument_by_id", fake_get_instrument_by_id)
	monkeypatch.setattr(instrument_service, "list_instrument_versions", fake_list_instrument_versions)
	with pytest.raises(ValueError, match="selection_mode='multiple'"):
		asyncio.run(
			instrument_service.update_instrument_status(
				_as_async_session(session),
				instrument_id,
				InstrumentActivateRequest(is_active=True),
			)
		)

	assert session.execute_count == 0
	assert session.commit_count == 0

from __future__ import annotations

import asyncio
import uuid
from random import Random
from typing import Any, Callable, cast

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument
from app.products.playspace.schemas.management import InstrumentActivateRequest, InstrumentCreateRequest
from app.products.playspace.scoring_metadata import build_scoring_sections_from_instrument
from app.products.playspace.seed_data import _build_question_answers
from app.products.playspace.services import instrument as instrument_service
from tests.products.playspace import _instrument_builders as builders

ASSIGNED_SOCIABILITY_QUESTION_KEYS = [builders.SCALED_QUESTION_KEY, builders.SECOND_SCALED_QUESTION_KEY]


def _single_select_content(version: str = builders.LEGACY_SOCIABILITY_VERSION) -> dict[str, Any]:
	return builders.minimal_content(version=version, sociability="single")


def _stored_single_select_content(version: str = builders.LEGACY_SOCIABILITY_VERSION) -> dict[str, Any]:
	"""Single-select content in the shape publications below 5.32 are stored in.

	No scale guidance block or question scale declares ``selection_mode``, so every
	scale, Sociability included, relies on the schema default of single-select.
	"""

	content = _single_select_content(version)
	for block in content["en"]["scale_guidance"]:
		del block["selection_mode"]
	for section in content["en"]["sections"]:
		for question in section["questions"]:
			for scale in question["scales"]:
				del scale["selection_mode"]
	return content


# Single-select content that declares its mode, and the stored shape that omits it.
SINGLE_SELECT_SHAPES = [
	pytest.param(_single_select_content, id="declared_selection_mode"),
	pytest.param(_stored_single_select_content, id="omitted_selection_mode"),
]


def _multi_select_content(version: str = builders.MULTI_SELECT_SOCIABILITY_VERSION) -> dict[str, Any]:
	return builders.minimal_content(version=version, sociability="multiple")


def _revert_to_single_select(content: dict[str, Any], question_key: str) -> None:
	"""Put one question's Sociability scale back on the single-select contract."""

	builders.scale(content, question_key, "sociability").update(builders.sociability_scale("single"))


def _semantically_invalid_multiple_content() -> dict[str, Any]:
	"""Single-select content where one assigned scale declares multiple but keeps its single-select answers."""

	content = _single_select_content()
	builders.sociability_scales(content)[0]["selection_mode"] = "multiple"
	return content


def _one_assigned_scale_single_content() -> dict[str, Any]:
	"""Multi-select content where the first assigned scale is back on the single-select contract."""

	content = _multi_select_content(version=builders.LEGACY_SOCIABILITY_VERSION)
	_revert_to_single_select(content, builders.SCALED_QUESTION_KEY)
	return content


def _only_guidance_multiple_content() -> dict[str, Any]:
	"""Single-select content where only the Sociability guidance block is multi-select."""

	content = _single_select_content()
	builders.guidance(content, "sociability").update(builders.sociability_guidance("multiple"))
	return content


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


def test_seed_answer_generation_picks_non_empty_multi_select_sociability_answers() -> None:
	instrument = builders.parse(_multi_select_content())
	question = next(
		question
		for section in build_scoring_sections_from_instrument(instrument)
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
	assert all(set(answer) <= set(builders.MULTI_SELECT_SOCIABILITY_KEYS) for answer in generated_multiple_answers)


@pytest.mark.parametrize(
	("mutation", "message"),
	[
		(lambda scale: scale.update(prompt="Wrong prompt"), "prompt"),
		(lambda scale: scale["options"].reverse(), "ordered keys"),
	],
)
def test_strict_semantic_validation_rejects_invalid_multiple_sociability(
	mutation: Callable[[dict[str, Any]], object],
	message: str,
) -> None:
	content = _multi_select_content()
	mutation(builders.sociability_scales(content)[0])

	with pytest.raises(ValueError, match=message):
		instrument_service.validate_instrument_content(content, strict_sociability=True)


# Each rule is applied to the first and the last option, so every option is checked.
@pytest.mark.parametrize("option_index", [0, -1], ids=["first_option", "last_option"])
@pytest.mark.parametrize(
	("change", "message"),
	[
		pytest.param({"addition_value": 2}, "addition_value", id="addition_value"),
		pytest.param({"boost_value": 2}, "boost_value", id="boost_value"),
		pytest.param({"is_unsure": True}, "Unsure", id="unsure"),
		pytest.param({"is_not_applicable": True}, "not-applicable", id="not_applicable"),
	],
)
def test_strict_semantic_validation_rejects_invalid_multiple_sociability_option(
	change: dict[str, Any],
	message: str,
	option_index: int,
) -> None:
	content = _multi_select_content()
	builders.sociability_scales(content)[0]["options"][option_index].update(change)

	with pytest.raises(ValueError, match=message):
		instrument_service.validate_instrument_content(content, strict_sociability=True)


@pytest.mark.parametrize("question_key", ASSIGNED_SOCIABILITY_QUESTION_KEYS)
def test_strict_semantic_validation_rejects_one_unconverted_assigned_scale(question_key: str) -> None:
	content = _multi_select_content()
	_revert_to_single_select(content, question_key)

	with pytest.raises(ValueError, match="selection_mode='multiple'"):
		instrument_service.validate_instrument_content(content, strict_sociability=True)


def test_strict_semantic_validation_requires_an_assigned_sociability_scale() -> None:
	content = _multi_select_content()
	for section in content["en"]["sections"]:
		for question in section["questions"]:
			question["scales"] = [scale for scale in question["scales"] if scale["key"] != "sociability"]

	with pytest.raises(ValueError, match="at least one assigned Sociability scale"):
		instrument_service.validate_instrument_content(content, strict_sociability=True)


def test_strict_semantic_validation_requires_a_sociability_guidance_block() -> None:
	content = _multi_select_content()
	content["en"]["scale_guidance"] = [
		block for block in content["en"]["scale_guidance"] if block["key"] != "sociability"
	]

	with pytest.raises(ValueError, match="must define exactly one Sociability scale guidance block"):
		instrument_service.validate_instrument_content(content, strict_sociability=True)


@pytest.mark.parametrize("build_content", SINGLE_SELECT_SHAPES)
def test_strict_semantic_validation_allows_single_select_sociability_before_multi_select_version(
	build_content: Callable[[str], dict[str, Any]],
) -> None:
	content = build_content(builders.LEGACY_SOCIABILITY_VERSION)

	instrument_service.validate_instrument_content(content, strict_sociability=True)


def test_strict_semantic_validation_rejects_single_select_sociability_from_multi_select_version() -> None:
	content = _single_select_content(version=builders.MULTI_SELECT_SOCIABILITY_VERSION)

	with pytest.raises(ValueError, match="selection_mode='multiple'"):
		instrument_service.validate_instrument_content(content, strict_sociability=True)


# Below 5.32, one multi-select Sociability scale, whether the guidance block or an
# assigned scale, puts every Sociability scale on the multi-select contract.
@pytest.mark.parametrize(
	"build_content",
	[
		pytest.param(_one_assigned_scale_single_content, id="one_assigned_scale_single"),
		pytest.param(_only_guidance_multiple_content, id="only_guidance_multiple"),
		pytest.param(_semantically_invalid_multiple_content, id="only_one_assigned_scale_multiple"),
	],
)
def test_strict_semantic_validation_rejects_mixed_modes_even_with_legacy_version(
	build_content: Callable[[], dict[str, Any]],
) -> None:
	content = build_content()
	assert content["en"]["instrument_version"] == builders.LEGACY_SOCIABILITY_VERSION

	with pytest.raises(ValueError, match="selection_mode='multiple'"):
		instrument_service.validate_instrument_content(content, strict_sociability=True)


def test_strict_semantic_validation_allows_complete_multi_select_sociability() -> None:
	content = _multi_select_content()

	instrument_service.validate_instrument_content(content, strict_sociability=True)


@pytest.mark.parametrize("mutation", ["single", "invalid_options"])
def test_strict_semantic_validation_rejects_noncanonical_sociability_guidance(mutation: str) -> None:
	content = _multi_select_content()
	guidance = builders.guidance(content, "sociability")
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
		instrument_key=builders.INSTRUMENT_KEY,
		instrument_version=f"{builders.LEGACY_SOCIABILITY_VERSION}.1",
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
		instrument_key=builders.INSTRUMENT_KEY,
		instrument_version=builders.MULTI_SELECT_SOCIABILITY_VERSION,
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
	content["en"]["instrument_version"] = builders.MULTI_SELECT_SOCIABILITY_VERSION
	row = Instrument(
		id=instrument_id,
		instrument_key=builders.INSTRUMENT_KEY,
		instrument_version=builders.MULTI_SELECT_SOCIABILITY_VERSION,
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


@pytest.mark.parametrize("build_content", SINGLE_SELECT_SHAPES)
def test_reactivate_single_select_sociability_publication_before_multi_select_version_is_allowed(
	monkeypatch: pytest.MonkeyPatch,
	build_content: Callable[[str], dict[str, Any]],
) -> None:
	session = _RecordingSession()
	instrument_id = uuid.uuid4()
	row = Instrument(
		id=instrument_id,
		instrument_key=builders.INSTRUMENT_KEY,
		instrument_version=builders.LEGACY_SOCIABILITY_VERSION,
		parent_instrument_id=None,
		is_active=False,
		content=build_content(builders.LEGACY_SOCIABILITY_VERSION),
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


@pytest.mark.parametrize("build_content", SINGLE_SELECT_SHAPES)
def test_reactivate_nonnumeric_publication_with_single_select_sociability_is_allowed(
	monkeypatch: pytest.MonkeyPatch,
	build_content: Callable[[str], dict[str, Any]],
) -> None:
	session = _RecordingSession()
	instrument_id = uuid.uuid4()
	row = Instrument(
		id=instrument_id,
		instrument_key=builders.INSTRUMENT_KEY,
		instrument_version="legacy-release",
		parent_instrument_id=None,
		is_active=False,
		content=build_content("legacy-release"),
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


def test_root_publication_rejects_nonnumeric_version_before_database_writes(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	session = _RecordingSession()
	request = InstrumentCreateRequest(
		instrument_key=builders.INSTRUMENT_KEY,
		instrument_version="candidate",
		content=_single_select_content(version="candidate"),
	)

	async def fake_list_instrument_versions(_session: object, _key: str) -> list[Instrument]:
		return []

	monkeypatch.setattr(instrument_service, "list_instrument_versions", fake_list_instrument_versions)
	with pytest.raises(instrument_service.InstrumentValidationError, match="Root instrument versions must be numeric"):
		asyncio.run(instrument_service.create_instrument_version(_as_async_session(session), request, activate=True))

	assert session.execute_count == 0
	assert session.added == []
	assert session.commit_count == 0


def test_new_publication_from_draft_rejects_nonnumeric_version_before_database_writes(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	session = _RecordingSession()
	draft = Instrument(
		id=uuid.uuid4(),
		instrument_key=builders.INSTRUMENT_KEY,
		instrument_version=f"{builders.MULTI_SELECT_SOCIABILITY_VERSION}.1",
		parent_instrument_id=uuid.uuid4(),
		is_active=False,
		content=_multi_select_content(),
	)
	# The content is otherwise publishable. Having a parent skips the root rule, and
	# with no publication to number from, the requested version is the one published.
	request = InstrumentCreateRequest(
		instrument_key=builders.INSTRUMENT_KEY,
		instrument_version="candidate",
		parent_instrument_id=draft.id,
		content=_multi_select_content(version="candidate"),
	)

	async def fake_get_instrument_by_id(_session: object, _instrument_id: uuid.UUID) -> Instrument:
		return draft

	async def fake_list_instrument_versions(_session: object, _key: str) -> list[Instrument]:
		return [draft]

	monkeypatch.setattr(instrument_service, "get_instrument_by_id", fake_get_instrument_by_id)
	monkeypatch.setattr(instrument_service, "list_instrument_versions", fake_list_instrument_versions)
	with pytest.raises(
		instrument_service.InstrumentValidationError,
		match="New published instruments must use a numeric version",
	):
		asyncio.run(instrument_service.create_instrument_version(_as_async_session(session), request, activate=True))

	assert session.execute_count == 0
	assert session.added == []
	assert session.commit_count == 0


def test_inactive_root_create_rejects_nonnumeric_version_before_row_exists(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	session = _RecordingSession()
	request = InstrumentCreateRequest(
		instrument_key=builders.INSTRUMENT_KEY,
		instrument_version="wip",
		content=_single_select_content(version="wip"),
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
		instrument_key=builders.INSTRUMENT_KEY,
		instrument_version="9.0",
		content=_single_select_content(version="9.0"),
	)

	async def fake_list_instrument_versions(_session: object, _key: str) -> list[Instrument]:
		return []

	monkeypatch.setattr(instrument_service, "list_instrument_versions", fake_list_instrument_versions)
	result = asyncio.run(
		instrument_service.create_instrument_version(_as_async_session(session), request, activate=False)
	)

	assert result is not None
	assert result.instrument_version == "9.0"
	assert result.parent_instrument_id is None
	assert result.is_active is False
	assert session.added == [result]
	assert session.commit_count == 1


def test_promoting_draft_validates_sociability_against_new_publication_version(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	session = _RecordingSession()
	instrument_id = uuid.uuid4()
	draft_version = f"{builders.LEGACY_SOCIABILITY_VERSION}.1"
	row = Instrument(
		id=instrument_id,
		instrument_key=builders.INSTRUMENT_KEY,
		instrument_version=draft_version,
		parent_instrument_id=uuid.uuid4(),
		is_active=False,
		content=_single_select_content(version=draft_version),
	)
	published = Instrument(
		id=uuid.uuid4(),
		instrument_key=builders.INSTRUMENT_KEY,
		instrument_version=builders.LEGACY_SOCIABILITY_VERSION,
		parent_instrument_id=None,
		is_active=True,
		content=_single_select_content(),
	)
	# The draft is single-select, which its parent's version permits, but promoting
	# it mints the multi-select version, so the draft is judged against that.
	assert (
		instrument_service.next_published_version([builders.LEGACY_SOCIABILITY_VERSION])
		== builders.MULTI_SELECT_SOCIABILITY_VERSION
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

"""Structural publication: the mode that is allowed to change the questions.

Copy-only activation compares a candidate to its parent and rejects anything
score-affecting that differs. A structurally new instrument differs on purpose,
so those comparisons would reject it for being what it is. These tests pin the
two halves of the split: structural candidates skip parent parity and are
instead held to standing on their own, and declaring structural does not relax
anything about the parent lineage that makes rollback possible.

Every test here is pure — mock sessions, no database — so the gate is provable
without a live catalog.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError

from app.products.yee.schemas.instrument import YeeInstrumentActivateRequest
from app.products.yee.schemas.instrument_authoring import (
	AuthoringFollowUp,
	AuthoringInstrumentV2,
	AuthoringOption,
	AuthoringPrimary,
	AuthoringQuestion,
	AuthoringResponseBinding,
	AuthoringScoring,
	AuthoringSection,
)
from app.products.yee.services.instrument import _update_yee_instrument_status
from app.products.yee.services.instrument_activation import (
	content_digest,
	validate_copy_only_activation,
	validate_structural_activation,
	validated_activation_content,
)
from app.yee_instrument_schema import YeeInstrumentResponse
from tests.products.yee._helpers import error_detail


# ---------------------------------------------------------------------------
# A small instrument that shares nothing with the frozen schema-v1 contract.
# ---------------------------------------------------------------------------


def _question(
	question_id: str = "shade.q1",
	*,
	binding: AuthoringResponseBinding | None = None,
	follow_up: AuthoringFollowUp | None = None,
) -> AuthoringQuestion:
	return AuthoringQuestion(
		id=question_id,
		prompt="Is there shade over the seating?",
		primary=AuthoringPrimary(
			options=[AuthoringOption(id="1", label="Yes", score=1), AuthoringOption(id="2", label="No", score=0)]
		),
		follow_up=follow_up,
		scoring=AuthoringScoring(
			method="presence_condition_product" if follow_up else "option_score",
			domain="access",
		),
		response_binding=binding,
	)


def _follow_up(trigger_option_ids: list[str] | None = None) -> AuthoringFollowUp:
	return AuthoringFollowUp(
		trigger_option_ids=["1"] if trigger_option_ids is None else trigger_option_ids,
		required_when_shown=True,
		prompt="Rate the shade.",
		options=[AuthoringOption(id="1", label="Poor", score=1), AuthoringOption(id="2", label="Good", score=2)],
	)


def _binding(condition_item_id: str | None = None) -> AuthoringResponseBinding:
	return AuthoringResponseBinding(
		presence_item_id="QIDNEW#1",
		choice_id="1",
		condition_item_id=condition_item_id,
	)


def _item(item_id: str) -> dict[str, object]:
	return {
		"item_id": item_id,
		"base_question_id": item_id.split("#")[0],
		"block": "Access",
		"question_text": "Shade",
		"choices": {"1": {"Display": "Seating"}},
		"answers": {"1": {"Display": "Yes"}, "2": {"Display": "No"}},
	}


def _instrument(
	questions: list[AuthoringQuestion],
	item_ids: list[str] | None = None,
) -> YeeInstrumentResponse:
	return YeeInstrumentResponse.model_validate(
		{
			"survey_name": "YEE structural",
			"version": "3.0",
			"scoring_items": [_item(item_id) for item_id in (item_ids or ["QIDNEW#1", "QIDNEW#2"])],
			"authoring": AuthoringInstrumentV2(
				schemaVersion=2,
				sections=[
					AuthoringSection(
						id="access",
						title="Access",
						instructions="",
						comment_prompt="",
						questions=questions,
					)
				],
			).model_dump(by_alias=True),
		}
	)


def _sound_structural() -> YeeInstrumentResponse:
	"""A structurally new instrument with nothing wrong with it."""

	return _instrument([_question(binding=_binding("QIDNEW#2"), follow_up=_follow_up())])


# ---------------------------------------------------------------------------
# The split itself
# ---------------------------------------------------------------------------


def test_structural_candidate_is_accepted_although_copy_only_would_reject_it() -> None:
	# Given an instrument that shares no question ids with the frozen contract
	candidate = _sound_structural()
	parent = _instrument([_question("access.q1", binding=_binding("QIDNEW#2"), follow_up=_follow_up())])

	# When each mode judges it
	structural = validate_structural_activation(candidate)
	copy_only = validate_copy_only_activation(candidate, parent)

	# Then structural accepts what copy-only refuses, which is the whole split
	assert structural.ok, [reason.code for reason in structural.reasons]
	assert not copy_only.ok


def test_structural_keeps_the_candidates_own_scoring_items() -> None:
	# Given a structural candidate whose items exist only in itself
	candidate = _sound_structural()

	# When it is validated
	result = validate_structural_activation(candidate)

	# Then its items are taken as authored, not projected from a parent
	assert [item.item_id for item in result.projected_content.scoring_items] == ["QIDNEW#1", "QIDNEW#2"]


# ---------------------------------------------------------------------------
# Standing on its own: storage, reachability, scorability
# ---------------------------------------------------------------------------


def _codes(candidate: YeeInstrumentResponse) -> set[str]:
	return {reason.code for reason in validate_structural_activation(candidate).reasons}


def test_question_with_no_binding_cannot_store_an_answer() -> None:
	assert "missing_response_binding" in _codes(_instrument([_question()]))


def test_binding_to_an_item_this_instrument_does_not_have_is_rejected() -> None:
	candidate = _instrument([_question(binding=_binding())], item_ids=["QIDOTHER#1"])
	assert "binding_item_missing" in _codes(candidate)


def test_binding_to_a_choice_the_item_does_not_offer_is_rejected() -> None:
	candidate = _sound_structural()
	authoring = candidate.authoring
	assert authoring is not None
	rebound = (
		authoring.sections[0]
		.questions[0]
		.model_copy(
			update={
				"response_binding": AuthoringResponseBinding(
					presence_item_id="QIDNEW#1", choice_id="99", condition_item_id="QIDNEW#2"
				)
			}
		)
	)
	assert "binding_choice_missing" in _codes(_replaced(candidate, rebound))


def test_follow_up_without_a_bound_item_has_nowhere_to_be_stored() -> None:
	candidate = _instrument([_question(binding=_binding(None), follow_up=_follow_up())])
	assert "follow_up_binding_missing" in _codes(candidate)


def test_follow_up_triggering_on_an_option_that_does_not_exist_is_rejected() -> None:
	candidate = _instrument([_question(binding=_binding("QIDNEW#2"), follow_up=_follow_up(["7"]))])
	assert "follow_up_trigger_unknown" in _codes(candidate)


def test_follow_up_that_nothing_can_reveal_is_rejected() -> None:
	candidate = _instrument([_question(binding=_binding("QIDNEW#2"), follow_up=_follow_up([]))])
	assert "follow_up_never_shown" in _codes(candidate)


def test_duplicate_question_ids_are_rejected() -> None:
	question = _question(binding=_binding("QIDNEW#2"), follow_up=_follow_up())
	candidate = _instrument([question, question.model_copy()])
	codes = _codes(candidate)
	# Either guard is acceptable; both name the same defect.
	assert {"duplicate_question_id", "scoring_contract_unresolvable"} & codes


def test_an_instrument_with_no_questions_is_rejected() -> None:
	assert "no_scored_questions" in _codes(_instrument([]))


def _replaced(candidate: YeeInstrumentResponse, question: AuthoringQuestion) -> YeeInstrumentResponse:
	authoring = candidate.authoring
	assert authoring is not None
	section = authoring.sections[0].model_copy(update={"questions": [question]})
	return candidate.model_copy(update={"authoring": authoring.model_copy(update={"sections": [section]})}, deep=True)


# ---------------------------------------------------------------------------
# Mode is declared, not inferred
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_structural_mode_requires_authoring_content() -> None:
	# Given legacy content with no authoring document
	session = Mock()
	session.get = AsyncMock()

	# When it is published as structural
	with pytest.raises(HTTPException) as caught:
		await validated_activation_content(
			session,
			{"survey_name": "YEE", "version": "1", "scoring_items": []},
			uuid.uuid4(),
			mode="structural",
		)

	# Then the declaration is refused rather than quietly ignored
	assert error_detail(caught.value)["reasons"][0]["code"] == "structural_requires_authoring"
	session.get.assert_not_awaited()


@pytest.mark.anyio
async def test_declaring_structural_routes_past_the_parent_parity_checks() -> None:
	# Given the same candidate and parent that copy-only rejects above
	parent = _parent_row(
		content=_instrument([_question("access.q1", binding=_binding("QIDNEW#2"), follow_up=_follow_up())]).model_dump()
	)
	session = Mock()
	session.get = AsyncMock(return_value=parent)

	# When the publisher declares structural intent
	stored = await validated_activation_content(
		session,
		_sound_structural().model_dump(),
		parent.id,
		mode="structural",
	)

	# Then the declaration actually changes which validator runs
	assert stored["authoring"]["sections"][0]["questions"][0]["id"] == "shade.q1"


@pytest.mark.anyio
async def test_copy_only_is_the_default_and_still_rejects_a_structural_change() -> None:
	# Given a structurally new candidate and a real parent
	parent_row = Mock(
		id=uuid.uuid4(),
		instrument_key="yee",
		instrument_version="2.0",
		is_active=True,
		parent_instrument_id=None,
		content=_instrument(
			[_question("access.q1", binding=_binding("QIDNEW#2"), follow_up=_follow_up())]
		).model_dump(),
	)
	session = Mock()
	session.get = AsyncMock(return_value=parent_row)

	# When it is published without declaring a mode
	with pytest.raises(HTTPException) as caught:
		await validated_activation_content(session, _sound_structural().model_dump(), parent_row.id)

	# Then the safe default applies and parity still blocks it
	assert caught.value.status_code == 409
	assert error_detail(caught.value)["code"] == "structural_activation_blocked"


# ---------------------------------------------------------------------------
# Parent lineage — unchanged by the mode, because it is the rollback target
# ---------------------------------------------------------------------------


def _parent_row(**overrides: object) -> Mock:
	defaults: dict[str, object] = {
		"id": uuid.uuid4(),
		"instrument_key": "yee",
		"instrument_version": "2.0",
		"is_active": True,
		"parent_instrument_id": None,
		"content": _sound_structural().model_dump(),
	}
	defaults.update(overrides)
	return Mock(**defaults)


@pytest.mark.anyio
async def test_an_instrument_may_not_be_its_own_parent() -> None:
	candidate_id = uuid.uuid4()
	session = Mock()
	session.get = AsyncMock()

	with pytest.raises(HTTPException) as caught:
		await validated_activation_content(
			session,
			_sound_structural().model_dump(),
			candidate_id,
			mode="structural",
			candidate_instrument_id=candidate_id,
		)

	assert error_detail(caught.value)["reasons"][0]["code"] == "parent_instrument_self_reference"
	# Rejected before any lookup, so a self-parent cannot even be read back.
	session.get.assert_not_awaited()


@pytest.mark.anyio
async def test_a_parent_chain_that_loops_back_to_the_candidate_is_rejected() -> None:
	candidate_id = uuid.uuid4()
	grandparent_id = candidate_id
	parent = _parent_row(parent_instrument_id=grandparent_id)
	session = Mock()
	session.get = AsyncMock(return_value=parent)

	with pytest.raises(HTTPException) as caught:
		await validated_activation_content(
			session,
			_sound_structural().model_dump(),
			parent.id,
			mode="structural",
			candidate_instrument_id=candidate_id,
		)

	assert error_detail(caught.value)["reasons"][0]["code"] == "parent_instrument_cycle"


@pytest.mark.anyio
async def test_an_unused_inactive_parent_is_too_mutable_to_roll_back_to() -> None:
	# Given a parent that is still an editable draft
	parent = _parent_row(is_active=False)
	session = Mock()
	session.get = AsyncMock(return_value=parent)
	session.execute = AsyncMock(return_value=Mock(scalar_one=Mock(return_value=0)))

	# When it is named as the parent
	with pytest.raises(HTTPException) as caught:
		await validated_activation_content(
			session,
			_sound_structural().model_dump(),
			parent.id,
			mode="structural",
		)

	# Then activation refuses, because the rollback target could still change
	assert error_detail(caught.value)["reasons"][0]["code"] == "parent_instrument_mutable"


@pytest.mark.anyio
async def test_an_inactive_parent_that_audits_reference_is_an_acceptable_target() -> None:
	# Given an archived parent some audit is stamped with
	parent = _parent_row(is_active=False)
	session = Mock()
	session.get = AsyncMock(return_value=parent)
	session.execute = AsyncMock(return_value=Mock(scalar_one=Mock(return_value=3)))

	# When a sound structural candidate names it
	stored = await validated_activation_content(
		session,
		_sound_structural().model_dump(),
		parent.id,
		mode="structural",
	)

	# Then activation proceeds: an audit-referenced row can no longer be edited
	assert stored["authoring"] is not None


@pytest.mark.anyio
async def test_a_parent_chain_crossing_into_another_instrument_key_is_rejected() -> None:
	ancestor = _parent_row(instrument_key="yee_site_copy")
	parent = _parent_row(parent_instrument_id=ancestor.id)
	session = Mock()
	session.get = AsyncMock(side_effect=[parent, ancestor])

	with pytest.raises(HTTPException) as caught:
		await validated_activation_content(
			session,
			_sound_structural().model_dump(),
			parent.id,
			mode="structural",
		)

	assert error_detail(caught.value)["reasons"][0]["code"] == "parent_instrument_cross_key"


# ---------------------------------------------------------------------------
# Rollback target
# ---------------------------------------------------------------------------


def test_content_digest_ignores_key_order() -> None:
	assert content_digest({"a": 1, "b": [2, 3]}) == content_digest({"b": [2, 3], "a": 1})


def test_content_digest_changes_when_content_changes() -> None:
	assert content_digest({"a": 1}) != content_digest({"a": 2})


# ---------------------------------------------------------------------------
# Concurrent activation
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_losing_the_single_active_race_is_a_conflict_not_a_server_error() -> None:
	# Given an activation whose commit loses to a concurrent one
	row = Mock(
		id=uuid.uuid4(),
		instrument_key="yee",
		instrument_version="3.0",
		is_active=False,
		parent_instrument_id=None,
		content={"survey_name": "YEE", "version": "3", "scoring_items": []},
	)
	session = Mock()
	session.execute = AsyncMock(return_value=Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=[])))))
	session.commit = AsyncMock(
		side_effect=IntegrityError(
			"INSERT", {}, Exception('duplicate key value violates unique constraint "uq_instruments_yee_single_active"')
		)
	)
	session.rollback = AsyncMock()
	session.refresh = AsyncMock()

	async def _get(_model: object, _pk: object) -> object:
		return row

	session.get = AsyncMock(side_effect=_get)

	# When the losing caller commits
	with pytest.raises(HTTPException) as caught:
		await _update_yee_instrument_status(session, row.id, YeeInstrumentActivateRequest(is_active=True))

	# Then it is told to reload and retry rather than shown a 500
	assert caught.value.status_code == 409
	assert error_detail(caught.value)["code"] == "instrument_activation_conflict"
	session.rollback.assert_awaited_once()


@pytest.mark.anyio
async def test_an_unrelated_integrity_error_is_not_disguised_as_a_race() -> None:
	# Given a commit that fails for a reason activation cannot recover from
	row = Mock(
		id=uuid.uuid4(),
		instrument_key="yee",
		instrument_version="3.0",
		is_active=False,
		parent_instrument_id=None,
		content={"survey_name": "YEE", "version": "3", "scoring_items": []},
	)
	session = Mock()
	session.execute = AsyncMock(return_value=Mock(scalars=Mock(return_value=Mock(all=Mock(return_value=[])))))
	session.commit = AsyncMock(
		side_effect=IntegrityError("INSERT", {}, Exception('null value in column "content" violates not-null'))
	)
	session.rollback = AsyncMock()
	session.refresh = AsyncMock()
	session.get = AsyncMock(return_value=row)

	# When it is committed
	with pytest.raises(IntegrityError):
		await _update_yee_instrument_status(session, row.id, YeeInstrumentActivateRequest(is_active=True))

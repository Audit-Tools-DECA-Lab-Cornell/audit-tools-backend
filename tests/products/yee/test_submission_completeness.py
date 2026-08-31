"""Logical completeness of a submission, and the switch that enforces it.

These run against the real committed instrument rather than a hand-built
fixture: the rule has to hold for the 54 questions auditors actually answer,
including the reverse-scored ones and the sections with no follow-up at all.
"""

from __future__ import annotations

import copy
from typing import Any

import pytest

from app.products.yee.services.scoring import get_yee_instrument_data
from app.products.yee.services.submission_validation import (
	SUBMIT_COMPLETENESS_ENV_VAR,
	authoring_contract,
	find_incomplete_responses,
	submit_completeness_enforced,
)
from app.yee_instrument_schema import YeeInstrumentResponse


def _active_content() -> YeeInstrumentResponse:
	return YeeInstrumentResponse.model_validate(get_yee_instrument_data())


def _complete_responses(content: YeeInstrumentResponse) -> dict[str, dict[str, str]]:
	"""Answer every logical question, plus every follow-up its answer triggers."""

	responses: dict[str, dict[str, str]] = {}
	for section in authoring_contract(content).sections:
		for question in section.questions:
			binding = question.response_binding
			if binding is None:
				continue
			follow_up = question.follow_up
			# Prefer an answer that triggers the follow-up so the completed
			# payload exercises the conditional branch rather than avoiding it.
			primary = (
				follow_up.trigger_option_ids[0]
				if follow_up is not None and follow_up.trigger_option_ids
				else question.primary.options[0].id
			)
			responses.setdefault(binding.presence_item_id, {})[binding.choice_id] = primary
			if follow_up is not None and binding.condition_item_id is not None:
				responses.setdefault(binding.condition_item_id, {})[binding.choice_id] = follow_up.options[0].id
	return responses


def test_a_fully_answered_audit_is_complete() -> None:
	content = _active_content()
	result = find_incomplete_responses(content, _complete_responses(content))
	assert result.is_complete
	assert result.missing_primary_question_ids == ()
	assert result.missing_follow_up_question_ids == ()


def test_an_empty_audit_reports_every_primary_question() -> None:
	content = _active_content()
	result = find_incomplete_responses(content, {})
	expected = tuple(
		question.id
		for section in authoring_contract(content).sections
		for question in section.questions
		if question.response_binding is not None
	)
	assert result.missing_primary_question_ids == expected
	# A hidden follow-up is not reported: telling an auditor to answer a control
	# that is not on screen is worse than saying nothing.
	assert result.missing_follow_up_question_ids == ()


def test_a_triggered_follow_up_is_required() -> None:
	content = _active_content()
	responses = _complete_responses(content)
	target = next(
		question
		for section in authoring_contract(content).sections
		for question in section.questions
		if question.follow_up is not None and question.response_binding is not None
	)
	binding = target.response_binding
	assert binding is not None and binding.condition_item_id is not None
	del responses[binding.condition_item_id][binding.choice_id]

	result = find_incomplete_responses(content, responses)
	assert result.missing_follow_up_question_ids == (target.id,)
	assert result.missing_primary_question_ids == ()


def test_an_untriggered_follow_up_is_not_required() -> None:
	"""Answering "No" must not demand a condition rating for a feature that is absent."""

	content = _active_content()
	responses = _complete_responses(content)
	target = next(
		question
		for section in authoring_contract(content).sections
		for question in section.questions
		if question.follow_up is not None and question.response_binding is not None
	)
	binding = target.response_binding
	follow_up = target.follow_up
	assert binding is not None and follow_up is not None and binding.condition_item_id is not None
	non_trigger = next(option.id for option in target.primary.options if option.id not in follow_up.trigger_option_ids)
	responses[binding.presence_item_id][binding.choice_id] = non_trigger
	del responses[binding.condition_item_id][binding.choice_id]

	assert find_incomplete_responses(content, responses).is_complete


def test_an_explicitly_optional_follow_up_is_not_required() -> None:
	raw = copy.deepcopy(get_yee_instrument_data())
	content = YeeInstrumentResponse.model_validate(raw)
	logical = authoring_contract(content)
	target = next(
		question
		for section in logical.sections
		for question in section.questions
		if question.follow_up is not None and question.response_binding is not None
	)
	assert target.follow_up is not None
	object.__setattr__(target.follow_up, "required_when_shown", False)
	content = content.model_copy(update={"authoring": logical}, deep=False)

	responses = _complete_responses(content)
	binding = target.response_binding
	assert binding is not None and binding.condition_item_id is not None
	del responses[binding.condition_item_id][binding.choice_id]

	assert find_incomplete_responses(content, responses).is_complete


def test_unknown_extra_response_keys_are_ignored() -> None:
	"""A historical or superset payload stays valid.

	Completeness is judged against the stamped contract, never against whatever
	else a client sent, so an old submission carrying retired keys still passes.
	"""

	content = _active_content()
	responses: dict[str, Any] = dict(_complete_responses(content))
	responses["QID_RETIRED"] = {"9": "1"}
	responses["not-even-a-map"] = "legacy-scalar"

	assert find_incomplete_responses(content, responses).is_complete


def test_the_error_detail_carries_ids_only() -> None:
	content = _active_content()
	detail = find_incomplete_responses(content, {}).as_error_detail()

	assert detail["code"] == "incomplete_audit_responses"
	assert detail["missing_primary_question_ids"]
	# No question text and no response values may reach a 422 body or a log line.
	serialized = str(detail)
	for section in authoring_contract(content).sections:
		for question in section.questions:
			assert question.prompt not in serialized


def test_enforcement_is_off_unless_explicitly_enabled(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Deploying the rule and turning it on have to be separate events."""

	monkeypatch.delenv(SUBMIT_COMPLETENESS_ENV_VAR, raising=False)
	assert submit_completeness_enforced() is False

	for value in ("", "false", "0", "yes", "TRUE ", "1"):
		monkeypatch.setenv(SUBMIT_COMPLETENESS_ENV_VAR, value)
		assert submit_completeness_enforced() is (value.strip().lower() == "true")

	monkeypatch.setenv(SUBMIT_COMPLETENESS_ENV_VAR, "true")
	assert submit_completeness_enforced() is True

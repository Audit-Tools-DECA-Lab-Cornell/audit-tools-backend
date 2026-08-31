from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from app.products.yee.schemas.instrument import YeeInstrumentActivateRequest, YeeInstrumentCreateRequest
from app.products.yee.schemas.instrument_authoring import AuthoringInstrumentV2, AuthoringQuestion
from app.products.yee.services.instrument import _create_yee_instrument_version, _update_yee_instrument_status
from app.products.yee.services.instrument_activation import (
	mobile_affirmative,
	validated_activation_content,
	validate_copy_only_activation,
	web_affirmative,
)
from app.products.yee.services.instrument_authoring import legacy_to_authoring
from app.yee_instrument_schema import YeeInstrumentResponse
from tests.products.yee._helpers import SEED_PASSWORD, _bearer_headers, _unique_suffix

ACTIVE_PATH = Path(__file__).parents[3] / "app/products/yee/instruments/yee.active.instrument.json"
SEED_ADMIN_EMAIL = "admin-demo@yee.local"


def _active_content() -> YeeInstrumentResponse:
	return YeeInstrumentResponse.model_validate_json(ACTIVE_PATH.read_text())


def _with_authoring(
	parent: YeeInstrumentResponse,
	authoring: AuthoringInstrumentV2,
) -> YeeInstrumentResponse:
	return parent.model_copy(update={"authoring": authoring})


def _replace_question(
	authoring: AuthoringInstrumentV2,
	replacement: AuthoringQuestion,
) -> AuthoringInstrumentV2:
	sections = [
		section.model_copy(
			update={
				"questions": [
					replacement if question.id == replacement.id else question for question in section.questions
				]
			}
		)
		for section in authoring.sections
	]
	return authoring.model_copy(update={"sections": sections})


def _reason_codes(candidate: YeeInstrumentResponse, parent: YeeInstrumentResponse) -> set[str]:
	return {reason.code for reason in validate_copy_only_activation(candidate, parent).reasons}


def _login_admin(client: TestClient) -> str:
	response = client.post(
		"/yee/auth/login",
		json={"email": SEED_ADMIN_EMAIL, "password": SEED_PASSWORD},
	)
	assert response.status_code == 200, response.text
	return response.json()["access_token"]


@pytest.mark.anyio
async def test_service_rejects_force_for_yee_even_when_saving_inactive() -> None:
	# Given an ordinary YEE create request using the forbidden force escape hatch
	session = Mock()
	session.commit = AsyncMock()
	session.refresh = AsyncMock()
	data = YeeInstrumentCreateRequest(
		instrument_version="force-rejected",
		content={"survey_name": "YEE", "version": "1", "scoring_items": []},
	)

	# When the service boundary receives the request
	with pytest.raises(HTTPException) as caught:
		await _create_yee_instrument_version(session, data, activate=False, force=True)

	# Then force is rejected before the database can be mutated
	assert caught.value.status_code == 409
	assert caught.value.detail["code"] == "force_activation_not_allowed"
	session.add.assert_not_called()


@pytest.mark.anyio
async def test_non_yee_service_create_keeps_active_default_and_force_behavior() -> None:
	# Given a non-YEE instrument key using the shared create service
	session = Mock()
	session.execute = AsyncMock()
	session.commit = AsyncMock()
	session.refresh = AsyncMock()
	data = YeeInstrumentCreateRequest(
		instrument_key="other_instrument",
		instrument_version="unchanged-default",
		content={"unrelated": True},
	)

	# When create omits activation and retains its existing force input
	row = await _create_yee_instrument_version(session, data, force=True)

	# Then only the YEE key has the new inactive and force restrictions
	assert row.is_active is True
	assert row.instrument_key == "other_instrument"
	session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_yee_activation_refuses_to_silently_heal_multiple_active_rows() -> None:
	# Given two active YEE rows at the service boundary
	rows = [
		Mock(id=uuid.uuid4(), instrument_version="active-one"),
		Mock(id=uuid.uuid4(), instrument_version="active-two"),
	]
	query_result = Mock()
	query_result.scalars.return_value.all.return_value = rows
	session = Mock()
	session.execute = AsyncMock(return_value=query_result)
	session.add = Mock()
	data = YeeInstrumentCreateRequest(
		instrument_version="must-not-heal",
		content=_active_content().model_dump(),
	)

	# When another YEE activation is attempted
	with pytest.raises(HTTPException) as caught:
		await _create_yee_instrument_version(session, data, activate=True)

	# Then the conflict is visible before deactivation or insertion
	assert caught.value.detail["code"] == "multiple_active_instruments"
	assert {row["instrument_version"] for row in caught.value.detail["conflicts"]} == {
		"active-one",
		"active-two",
	}
	session.add.assert_not_called()


@pytest.mark.anyio
async def test_service_rejects_force_for_yee_patch_before_mutation() -> None:
	# Given a YEE row returned to the direct service boundary
	instrument = Mock(instrument_key="yee")
	query_result = Mock()
	query_result.scalar_one_or_none.return_value = instrument
	session = Mock()
	session.execute = AsyncMock(return_value=query_result)
	session.commit = AsyncMock()

	# When force is supplied directly to the patch service
	with pytest.raises(HTTPException) as caught:
		await _update_yee_instrument_status(
			session,
			uuid.uuid4(),
			YeeInstrumentActivateRequest(is_active=False),
			force=True,
		)

	# Then it is rejected before status or transaction mutation
	assert caught.value.detail["code"] == "force_activation_not_allowed"
	session.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_authoring_v2_activation_requires_parent_before_database_lookup() -> None:
	# Given valid authoring-v2 content without a parent id
	parent = _active_content()
	candidate = _with_authoring(parent, legacy_to_authoring(parent).authoring)
	session = Mock()
	session.get = AsyncMock()

	# When activation content is resolved
	with pytest.raises(HTTPException) as caught:
		await validated_activation_content(session, candidate.model_dump(), None)

	# Then the typed parent requirement is returned without querying or mutating
	assert caught.value.status_code == 409
	assert caught.value.detail["reasons"][0]["code"] == "parent_instrument_required"
	session.get.assert_not_awaited()


def test_http_force_escape_hatches_are_rejected(yee_client: TestClient) -> None:
	# Given an authenticated ordinary YEE create request using force
	token = _login_admin(yee_client)
	content = _active_content().model_dump()

	# When force is supplied to create and to patch
	create_force = yee_client.post(
		"/yee/admin/instruments",
		params={"force": "true"},
		headers=_bearer_headers(token),
		json={"instrument_version": f"force-create-{_unique_suffix()}", "content": content},
	)
	draft = yee_client.post(
		"/yee/admin/instruments",
		headers=_bearer_headers(token),
		json={"instrument_version": f"force-patch-{_unique_suffix()}", "content": content},
	)
	assert draft.status_code == 201, draft.text
	patch_force = yee_client.patch(
		f"/yee/admin/instruments/{draft.json()['id']}",
		params={"force": "true"},
		headers=_bearer_headers(token),
		json={"is_active": False},
	)

	# Then both paths reject force and the draft remains inactive
	assert create_force.status_code == 409, create_force.text
	assert create_force.json()["detail"]["code"] == "force_activation_not_allowed"
	assert patch_force.status_code == 409, patch_force.text
	assert patch_force.json()["detail"]["code"] == "force_activation_not_allowed"
	deleted = yee_client.delete(f"/yee/admin/instruments/{draft.json()['id']}", headers=_bearer_headers(token))
	assert deleted.status_code == 200, deleted.text


def test_copy_only_wording_edit_is_accepted() -> None:
	# Given canonical bindings with one question prompt changed
	parent = _active_content()
	authoring = legacy_to_authoring(parent).authoring
	question = authoring.sections[0].questions[0]
	edited = _replace_question(authoring, question.model_copy(update={"prompt": "Updated access prompt"}))
	candidate = _with_authoring(parent, edited)

	# When activation compatibility is evaluated
	result = validate_copy_only_activation(candidate, parent)

	# Then copy changes project without changing structural behavior
	assert result.ok is True
	assert result.projected_content.authoring == edited
	assert result.projected_content.scoring_items != candidate.scoring_items


def test_ordered_binding_change_is_rejected() -> None:
	# Given the same questions reordered inside a section
	parent = _active_content()
	authoring = legacy_to_authoring(parent).authoring
	section = authoring.sections[0]
	reordered = section.model_copy(update={"questions": list(reversed(section.questions))})
	candidate = _with_authoring(parent, authoring.model_copy(update={"sections": [reordered, *authoring.sections[1:]]}))

	# When activation compatibility is evaluated
	codes = _reason_codes(candidate, parent)

	# Then order drift is reported as structural
	assert "ordered_bindings_changed" in codes


def test_scoring_method_score_trigger_and_requiredness_changes_are_rejected() -> None:
	# Given one paired question with four independent behavior changes
	parent = _active_content()
	authoring = legacy_to_authoring(parent).authoring
	question = authoring.sections[0].questions[0]
	assert question.follow_up is not None
	primary_option = question.primary.options[0].model_copy(update={"score": 99})
	primary = question.primary.model_copy(update={"options": [primary_option, *question.primary.options[1:]]})
	follow_up = question.follow_up.model_copy(update={"trigger_option_ids": ["2"], "required_when_shown": False})
	scoring = question.scoring.model_copy(update={"domain": "amenities"})
	edited_question = question.model_copy(update={"primary": primary, "follow_up": follow_up, "scoring": scoring})
	candidate = _with_authoring(parent, _replace_question(authoring, edited_question))

	# When activation compatibility is evaluated
	codes = _reason_codes(candidate, parent)

	# Then each changed contract dimension is named
	assert {
		"item_behavior_changed",
		"option_scores_changed",
		"trigger_options_changed",
		"requiredness_changed",
	}.issubset(codes)


def test_incomplete_item_spec_coverage_is_rejected() -> None:
	# Given an authoring document missing one required logical question
	parent = _active_content()
	authoring = legacy_to_authoring(parent).authoring
	section = authoring.sections[0]
	incomplete = section.model_copy(update={"questions": section.questions[1:]})
	candidate = _with_authoring(
		parent,
		authoring.model_copy(update={"sections": [incomplete, *authoring.sections[1:]]}),
	)

	# When activation compatibility is evaluated
	codes = _reason_codes(candidate, parent)

	# Then exact ITEM_SPECS coverage and ordered identity both fail
	assert "item_spec_coverage_changed" in codes
	assert "ordered_bindings_changed" in codes


def test_missing_scoring_item_and_choice_are_rejected() -> None:
	# Given a parent missing one entire scored item and one required matrix choice
	canonical = _active_content()
	authoring = legacy_to_authoring(canonical).authoring
	items = []
	for item in canonical.scoring_items:
		if item.item_id == "QID4#1":
			continue
		if item.item_id == "QID1#1":
			choices = {key: choice for key, choice in item.choices.items() if key != "1"}
			items.append(item.model_copy(update={"choices": choices}))
			continue
		items.append(item)
	parent = canonical.model_copy(update={"scoring_items": items})
	candidate = _with_authoring(parent, authoring)

	# When activation compatibility is evaluated
	codes = _reason_codes(candidate, parent)

	# Then both missing storage contracts are named
	assert {"missing_scoring_item", "missing_scoring_choice"}.issubset(codes)


def test_projection_conflict_is_rejected() -> None:
	# Given one sibling edits an option stored in a shared legacy item
	parent = _active_content()
	authoring = legacy_to_authoring(parent).authoring
	question = authoring.sections[0].questions[0]
	changed_option = question.primary.options[0].model_copy(update={"label": "Yes, edited once"})
	primary = question.primary.model_copy(update={"options": [changed_option, *question.primary.options[1:]]})
	candidate = _with_authoring(parent, _replace_question(authoring, question.model_copy(update={"primary": primary})))

	# When activation compatibility is evaluated
	codes = _reason_codes(candidate, parent)

	# Then projection cannot silently overwrite the sibling
	assert "projection_error" in codes


def test_shipping_predicates_pin_their_known_disagreement() -> None:
	# Given a label on which the deployed clients disagree
	label = "Mostly yes, in places"

	# When both deployed predicates classify it
	classifications = (mobile_affirmative(label), web_affirmative(label))

	# Then the mobile predicate is affirmative while the web predicate is not
	assert classifications == (True, False)


def test_mobile_only_affirmative_classification_change_is_rejected() -> None:
	# Given every sibling using QID15 changes the same non-affirmative label
	parent = _active_content()
	authoring = legacy_to_authoring(parent).authoring
	sections = []
	for section in authoring.sections:
		questions = []
		for question in section.questions:
			binding = question.response_binding
			if binding is None or binding.presence_item_id != "QID15#1":
				questions.append(question)
				continue
			option = question.primary.options[0].model_copy(update={"label": "Mostly yes, in places"})
			primary = question.primary.model_copy(update={"options": [option, *question.primary.options[1:]]})
			questions.append(question.model_copy(update={"primary": primary}))
		sections.append(section.model_copy(update={"questions": questions}))
	candidate = _with_authoring(parent, authoring.model_copy(update={"sections": sections}))

	# When activation compatibility is evaluated
	codes = _reason_codes(candidate, parent)

	# Then a change under either shipping predicate blocks activation
	assert "affirmative_classification_changed" in codes

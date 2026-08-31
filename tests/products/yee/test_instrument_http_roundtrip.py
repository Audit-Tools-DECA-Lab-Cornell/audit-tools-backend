from __future__ import annotations

import asyncio
import uuid

import pytest
from typing import Any
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Instrument
from app.products.yee.schemas.instrument_authoring import AuthoringInstrumentV2, AuthoringQuestion
from app.products.yee.services.instrument_authoring import legacy_to_authoring
from app.seed import YEE_INSTRUMENT_ID
from app.yee_instrument_schema import YeeInstrumentResponse
from tests.products.yee._helpers import SEED_PASSWORD, _bearer_headers, _unique_suffix

SEED_ADMIN_EMAIL = "admin-demo@yee.local"


def _login_admin(client: TestClient) -> str:
	response = client.post(
		"/yee/auth/login",
		json={"email": SEED_ADMIN_EMAIL, "password": SEED_PASSWORD},
	)
	assert response.status_code == 200, response.text
	return response.json()["access_token"]


def _active_content(client: TestClient) -> dict[str, Any]:
	response = client.get("/yee/instrument")
	assert response.status_code == 200, response.text
	return response.json()


def _replace_question(
	authoring: AuthoringInstrumentV2,
	replacement: AuthoringQuestion,
) -> AuthoringInstrumentV2:
	sections = [
		section.model_copy(
			update={
				"questions": [
					replacement if candidate.id == replacement.id else candidate for candidate in section.questions
				]
			}
		)
		for section in authoring.sections
	]
	return authoring.model_copy(update={"sections": sections})


def test_schema_v1_unknown_keys_round_trip_through_admin_and_public(yee_client: TestClient) -> None:
	# Given canonical content with unknown top-level and nested extensions
	token = _login_admin(yee_client)
	content = _active_content(yee_client)
	content["futureTopLevel"] = {"sequence": [3, 1, 2], "enabled": True}
	items = content["scoring_items"]
	assert isinstance(items, list)
	items[0]["futureNested"] = {"literal": "keep-me", "nullable": None}
	version = f"roundtrip-{_unique_suffix()}"

	# When the ordinary create omits activate and the draft is read back
	created = yee_client.post(
		"/yee/admin/instruments",
		headers=_bearer_headers(token),
		json={"instrument_version": version, "content": content},
	)
	assert created.status_code == 201, created.text
	draft = created.json()
	listing = yee_client.get("/yee/admin/instruments", headers=_bearer_headers(token))
	assert listing.status_code == 200, listing.text
	listed = next(row for row in listing.json() if row["id"] == draft["id"])
	detail = yee_client.get(f"/yee/admin/instruments/{listed['id']}", headers=_bearer_headers(token))
	assert detail.status_code == 200, detail.text

	# Then unknown values survive draft POST/list and active public delivery exactly
	assert draft["is_active"] is False
	assert detail.json()["content"]["futureTopLevel"] == content["futureTopLevel"]
	assert detail.json()["content"]["scoring_items"][0]["futureNested"] == items[0]["futureNested"]
	activated = yee_client.patch(
		f"/yee/admin/instruments/{draft['id']}",
		headers=_bearer_headers(token),
		json={"is_active": True},
	)
	assert activated.status_code == 200, activated.text
	public = _active_content(yee_client)
	assert public["futureTopLevel"] == content["futureTopLevel"]
	assert public["scoring_items"][0]["futureNested"] == items[0]["futureNested"]
	restored = yee_client.patch(
		f"/yee/admin/instruments/{YEE_INSTRUMENT_ID}",
		headers=_bearer_headers(token),
		json={"is_active": True},
	)
	assert restored.status_code == 200, restored.text
	deleted = yee_client.delete(f"/yee/admin/instruments/{draft['id']}", headers=_bearer_headers(token))
	assert deleted.status_code == 200, deleted.text


def test_inactive_authoring_v2_round_trips_typed_content_and_extras(yee_client: TestClient) -> None:
	# Given a typed authoring-v2 child plus an unrelated extension
	token = _login_admin(yee_client)
	parent_content = YeeInstrumentResponse.model_validate(_active_content(yee_client))
	authoring = legacy_to_authoring(parent_content).authoring
	content = parent_content.model_copy(
		update={"authoring": authoring, "futureExtension": {"untouched": ["a", "b"]}}
	).model_dump()
	version = f"authoring-v2-{_unique_suffix()}"

	# When the child is saved without an activate query parameter
	created = yee_client.post(
		"/yee/admin/instruments",
		headers=_bearer_headers(token),
		json={
			"instrument_version": version,
			"parent_instrument_id": str(YEE_INSTRUMENT_ID),
			"content": content,
		},
	)

	# Then the resolved authoring contract and unrelated data are returned unchanged
	assert created.status_code == 201, created.text
	draft = created.json()
	assert draft["is_active"] is False
	assert draft["parent_instrument_id"] == str(YEE_INSTRUMENT_ID)
	assert draft["content"]["authoring"] == content["authoring"]
	assert draft["content"]["futureExtension"] == content["futureExtension"]
	deleted = yee_client.delete(f"/yee/admin/instruments/{draft['id']}", headers=_bearer_headers(token))
	assert deleted.status_code == 200, deleted.text


def test_a_second_active_yee_row_cannot_be_created(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""The database refuses the defect this test used to simulate.

	This previously inserted a second active YEE row and asserted the public
	resolver reported a structured 409. Since yee_0010 / ps_0012 that insert cannot
	succeed: ``uq_instruments_yee_single_active`` rejects it, which is a stronger
	guarantee than detecting the state after the fact.

	The application-level guard is still there and still worth having if the index
	is ever dropped — it is covered without a database in
	``test_instrument_activation_safety.test_yee_activation_refuses_to_silently_heal_multiple_active_rows``.
	"""

	# Given the seeded active YEE row and a second one claiming to be active
	content = _active_content(yee_client)

	async def insert_conflict() -> None:
		async with yee_test_session_factory() as session:
			session.add(
				Instrument(
					id=uuid.uuid4(),
					instrument_key="yee",
					instrument_version=f"conflicting-active-{_unique_suffix()}",
					is_active=True,
					content=content,
				)
			)
			await session.commit()

	# When the write is attempted
	with pytest.raises(IntegrityError) as caught:
		asyncio.run(insert_conflict())

	# Then the constraint stops it, so two active rows never exist to be detected
	assert "uq_instruments_yee_single_active" in str(caught.value.orig)

	# And the public resolver still serves the one legitimate active row
	response = yee_client.get("/yee/instrument")
	assert response.status_code == 200, response.text
	assert response.json()["instrument_key"] == "yee"


def test_site_copy_default_activation_and_public_read_are_unchanged(yee_client: TestClient) -> None:
	# Given a new site-copy payload on the separate instrument key
	token = _login_admin(yee_client)
	content = {"regression": _unique_suffix(), "nested": {"keep": True}}

	# When it is created with the existing default and fetched publicly
	created = yee_client.post(
		"/yee/admin/site-copy",
		headers=_bearer_headers(token),
		json={"instrument_version": f"site-copy-{_unique_suffix()}", "content": content},
	)
	public = yee_client.get("/yee/site-copy")

	# Then site copy still activates by default and remains byte-preserving
	assert created.status_code == 201, created.text
	assert created.json()["is_active"] is True
	assert public.status_code == 200, public.text
	assert public.json() == content


def test_copy_only_authoring_v2_child_can_activate(yee_client: TestClient) -> None:
	# Given a parent-linked authoring-v2 child with only prompt wording changed
	token = _login_admin(yee_client)
	parent = YeeInstrumentResponse.model_validate(_active_content(yee_client))
	authoring = legacy_to_authoring(parent).authoring
	question = authoring.sections[0].questions[0]
	edited = _replace_question(authoring, question.model_copy(update={"prompt": "Copy-only access wording"}))
	content = parent.model_copy(update={"authoring": edited}).model_dump()
	created = yee_client.post(
		"/yee/admin/instruments",
		headers=_bearer_headers(token),
		json={
			"instrument_version": f"copy-only-{_unique_suffix()}",
			"parent_instrument_id": str(YEE_INSTRUMENT_ID),
			"content": content,
		},
	)
	assert created.status_code == 201, created.text

	# When the child is activated without force
	activated = yee_client.patch(
		f"/yee/admin/instruments/{created.json()['id']}",
		headers=_bearer_headers(token),
		json={"is_active": True},
	)

	# Then it becomes active with wording projected onto the bound legacy choice
	assert activated.status_code == 200, activated.text
	assert activated.json()["content"]["scoring_items"][0]["choices"]["1"]["Display"] == ("Copy-only access wording")
	restored = yee_client.patch(
		f"/yee/admin/instruments/{YEE_INSTRUMENT_ID}",
		headers=_bearer_headers(token),
		json={"is_active": True},
	)
	assert restored.status_code == 200, restored.text
	deleted = yee_client.delete(f"/yee/admin/instruments/{created.json()['id']}", headers=_bearer_headers(token))
	assert deleted.status_code == 200, deleted.text


def test_structural_authoring_v2_activation_is_refused_and_stays_inactive(yee_client: TestClient) -> None:
	# Given a parent-linked authoring-v2 child with a changed primary score
	token = _login_admin(yee_client)
	parent = YeeInstrumentResponse.model_validate(_active_content(yee_client))
	authoring = legacy_to_authoring(parent).authoring
	question = authoring.sections[0].questions[0]
	option = question.primary.options[0].model_copy(update={"score": 99})
	primary = question.primary.model_copy(update={"options": [option, *question.primary.options[1:]]})
	edited = _replace_question(authoring, question.model_copy(update={"primary": primary}))
	content = parent.model_copy(update={"authoring": edited}).model_dump()
	created = yee_client.post(
		"/yee/admin/instruments",
		headers=_bearer_headers(token),
		json={
			"instrument_version": f"structural-{_unique_suffix()}",
			"parent_instrument_id": str(YEE_INSTRUMENT_ID),
			"content": content,
		},
	)
	assert created.status_code == 201, created.text

	# When activation is attempted without any override
	blocked = yee_client.patch(
		f"/yee/admin/instruments/{created.json()['id']}",
		headers=_bearer_headers(token),
		json={"is_active": True},
	)

	# Then a machine-readable reason is returned and the saved child remains inactive
	assert blocked.status_code == 409, blocked.text
	detail = blocked.json()["detail"]
	assert "option_scores_changed" in {reason["code"] for reason in detail["reasons"]}
	listing = yee_client.get("/yee/admin/instruments", headers=_bearer_headers(token))
	draft_summary = next(row for row in listing.json() if row["id"] == created.json()["id"])
	assert draft_summary["is_active"] is False
	deleted = yee_client.delete(f"/yee/admin/instruments/{created.json()['id']}", headers=_bearer_headers(token))
	assert deleted.status_code == 200, deleted.text

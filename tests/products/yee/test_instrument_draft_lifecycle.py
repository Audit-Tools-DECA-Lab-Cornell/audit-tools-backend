from __future__ import annotations

import asyncio
from copy import deepcopy
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Audit, AuditStatus
from app.seed import YEE_INSTRUMENT_ID
from tests.products.yee._helpers import SEED_PASSWORD, _bearer_headers, _unique_suffix

SEED_ADMIN_EMAIL = "admin-demo@yee.local"


def _login_admin(client: TestClient) -> str:
	response = client.post(
		"/yee/auth/login",
		json={"email": SEED_ADMIN_EMAIL, "password": SEED_PASSWORD},
	)
	assert response.status_code == 200, response.text
	return response.json()["access_token"]


def _fork(client: TestClient, token: str, label: str) -> dict:
	response = client.post(
		f"/yee/admin/instruments/{YEE_INSTRUMENT_ID}/fork",
		headers=_bearer_headers(token),
		json={"instrument_version": label},
	)
	assert response.status_code == 201, response.text
	return response.json()


def _delete(client: TestClient, token: str, instrument_id: str) -> None:
	response = client.delete(
		f"/yee/admin/instruments/{instrument_id}",
		headers=_bearer_headers(token),
	)
	assert response.status_code == 200, response.text


def _save(client: TestClient, token: str, draft: dict, content: dict):
	return client.put(
		f"/yee/admin/instruments/{draft['id']}/draft",
		headers=_bearer_headers(token),
		json={
			"expected_updated_at": draft["updated_at"],
			"instrument_version": draft["instrument_version"],
			"content": content,
		},
	)


async def _reference_draft(
	session_factory: async_sessionmaker[AsyncSession],
	instrument_version: str,
) -> tuple[uuid.UUID, str | None, str | None]:
	async with session_factory() as session:
		audit = (
			(
				await session.execute(
					select(Audit).where(Audit.status.in_([AuditStatus.IN_PROGRESS, AuditStatus.PAUSED])).limit(1)
				)
			)
			.scalars()
			.first()
		)
		assert audit is not None
		original_key = audit.instrument_key
		original_version = audit.instrument_version
		audit.instrument_key = "yee"
		audit.instrument_version = instrument_version
		await session.commit()
		return audit.id, original_key, original_version


async def _restore_reference(
	session_factory: async_sessionmaker[AsyncSession],
	audit_id: uuid.UUID,
	instrument_key: str | None,
	instrument_version: str | None,
) -> None:
	async with session_factory() as session:
		audit = await session.get(Audit, audit_id)
		assert audit is not None
		audit.instrument_key = instrument_key
		audit.instrument_version = instrument_version
		await session.commit()


def test_fork_materializes_authoring_v2_and_rejects_case_insensitive_duplicate(yee_client: TestClient) -> None:
	token = _login_admin(yee_client)
	label = f"Workbench-{_unique_suffix()}"
	draft = _fork(yee_client, token, label)
	assert draft["parent_instrument_id"] == str(YEE_INSTRUMENT_ID)
	assert draft["schema_generation"] == "authoring_v2"
	assert draft["lifecycle"] == "draft"
	assert draft["content"]["authoring"]["schemaVersion"] == 2
	duplicate = yee_client.post(
		f"/yee/admin/instruments/{YEE_INSTRUMENT_ID}/fork",
		headers=_bearer_headers(token),
		json={"instrument_version": label.upper()},
	)
	assert duplicate.status_code == 409, duplicate.text
	assert duplicate.json()["detail"]["code"] == "instrument_version_conflict"
	_delete(yee_client, token, draft["id"])


def test_draft_save_isolated_from_sibling_and_stale_write_returns_409(yee_client: TestClient) -> None:
	token = _login_admin(yee_client)
	first = _fork(yee_client, token, f"isolation-a-{_unique_suffix()}")
	second = _fork(yee_client, token, f"isolation-b-{_unique_suffix()}")
	content = deepcopy(first["content"])
	content["authoring"]["sections"][0]["questions"][0]["prompt"] = "Edited only in the first draft"
	saved = _save(yee_client, token, first, content)
	assert saved.status_code == 200, saved.text
	assert saved.json()["content"]["authoring"]["sections"][0]["questions"][0]["prompt"] == (
		"Edited only in the first draft"
	)
	stale = _save(yee_client, token, first, content)
	assert stale.status_code == 409, stale.text
	assert stale.json()["detail"]["code"] == "draft_conflict"
	sibling = yee_client.get(
		f"/yee/admin/instruments/{second['id']}",
		headers=_bearer_headers(token),
	)
	assert sibling.status_code == 200, sibling.text
	assert sibling.json()["content"]["authoring"]["sections"][0]["questions"][0]["prompt"] != (
		"Edited only in the first draft"
	)
	_delete(yee_client, token, first["id"])
	_delete(yee_client, token, second["id"])


def test_draft_save_names_deleted_scored_question(yee_client: TestClient) -> None:
	token = _login_admin(yee_client)
	draft = _fork(yee_client, token, f"delete-guard-{_unique_suffix()}")
	content = deepcopy(draft["content"])
	deleted = content["authoring"]["sections"][0]["questions"].pop(0)
	response = _save(yee_client, token, draft, content)
	assert response.status_code == 422, response.text
	assert response.json()["detail"]["code"] == "missing_scored_questions"
	assert deleted["id"] in response.json()["detail"]["question_ids"]
	_delete(yee_client, token, draft["id"])


def test_active_version_cannot_be_saved_as_a_draft(yee_client: TestClient) -> None:
	token = _login_admin(yee_client)
	detail = yee_client.get(
		f"/yee/admin/instruments/{YEE_INSTRUMENT_ID}",
		headers=_bearer_headers(token),
	)
	assert detail.status_code == 200, detail.text
	response = _save(yee_client, token, detail.json(), detail.json()["content"])
	assert response.status_code == 409, response.text
	assert response.json()["detail"]["code"] == "instrument_immutable"


def test_referenced_version_cannot_be_saved_as_a_draft(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	token = _login_admin(yee_client)
	draft = _fork(yee_client, token, f"referenced-{_unique_suffix()}")
	audit_id, original_key, original_version = asyncio.run(
		_reference_draft(yee_test_session_factory, draft["instrument_version"])
	)
	try:
		response = _save(yee_client, token, draft, draft["content"])
		assert response.status_code == 409, response.text
		assert response.json()["detail"]["code"] == "instrument_immutable"
		assert response.json()["detail"]["lifecycle"] == "archived"
	finally:
		asyncio.run(
			_restore_reference(
				yee_test_session_factory,
				audit_id,
				original_key,
				original_version,
			)
		)
	_delete(yee_client, token, draft["id"])


def test_copy_only_draft_validates_and_publishes(yee_client: TestClient) -> None:
	token = _login_admin(yee_client)
	draft = _fork(yee_client, token, f"copy-publish-{_unique_suffix()}")
	content = deepcopy(draft["content"])
	content["authoring"]["sections"][0]["questions"][0]["prompt"] = "Published through the workbench"
	saved = _save(yee_client, token, draft, content)
	assert saved.status_code == 200, saved.text
	validation = yee_client.post(
		f"/yee/admin/instruments/{draft['id']}/validate",
		headers=_bearer_headers(token),
	)
	assert validation.status_code == 200, validation.text
	assert validation.json()["activation_ready"] is True
	published = yee_client.post(
		f"/yee/admin/instruments/{draft['id']}/publish",
		headers=_bearer_headers(token),
		json={"expected_updated_at": saved.json()["updated_at"]},
	)
	assert published.status_code == 200, published.text
	assert published.json()["is_active"] is True
	restored = yee_client.patch(
		f"/yee/admin/instruments/{YEE_INSTRUMENT_ID}",
		headers=_bearer_headers(token),
		json={"is_active": True},
	)
	assert restored.status_code == 200, restored.text
	_delete(yee_client, token, draft["id"])


def test_structural_draft_saves_but_publish_stays_blocked(yee_client: TestClient) -> None:
	token = _login_admin(yee_client)
	draft = _fork(yee_client, token, f"structural-save-{_unique_suffix()}")
	content = deepcopy(draft["content"])
	content["authoring"]["sections"][0]["questions"][0]["primary"]["options"][0]["score"] = 99
	saved = _save(yee_client, token, draft, content)
	assert saved.status_code == 200, saved.text
	validation = yee_client.post(
		f"/yee/admin/instruments/{draft['id']}/validate",
		headers=_bearer_headers(token),
	)
	assert validation.status_code == 200, validation.text
	assert validation.json()["activation_ready"] is False
	published = yee_client.post(
		f"/yee/admin/instruments/{draft['id']}/publish",
		headers=_bearer_headers(token),
		json={"expected_updated_at": saved.json()["updated_at"]},
	)
	assert published.status_code == 409, published.text
	assert published.json()["detail"]["code"] == "structural_activation_blocked"
	_delete(yee_client, token, draft["id"])

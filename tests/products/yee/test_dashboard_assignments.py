"""YEE dashboard assignment (FLOW K) integration tests.

Covers the assignment CRUD lifecycle and auditor-scoped ``my-places`` view:

- POST /yee/dashboard/assignments — manager creates assignment (success, authz)
- DELETE /yee/dashboard/assignments — manager removes assignment (success)
- GET /yee/dashboard/my-places — auditor sees only their assigned places (success, authz)

Each test creates a fresh manager + project + place + auditor-invite so the seed
graph is not corrupted for other test files in the session-scoped DB.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.dashboard_router as dashboard_router_module
from tests.products.yee._helpers import (
	SEED_AUDITOR_EMAIL,
	SEED_MANAGER_EMAIL,
	SEED_PASSWORD,
	_bearer_headers,
	_login_auditor,
	_signup_primary_manager,
	_unique_suffix,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_auditor_via_invite(
	client: TestClient,
	manager_headers: dict[str, str],
	monkeypatch,
	*,
	email: str,
) -> dict:
	"""Mint an auditor invite, accept it, and return the auditor profile metadata.

	Returns a dict with keys: ``email``, ``headers`` (bearer), ``auditor_id``
	(from the ``/yee/auth/me`` response -> ``has_auditor_profile``), and the
	raw accept-response user payload.
	"""
	captured: list[dict[str, str]] = []

	def _capture(*, to_email: str, invite_url: str) -> bool:
		captured.append({"to_email": to_email, "invite_url": invite_url})
		return True

	monkeypatch.setattr(dashboard_router_module, "send_auditor_invite_email", _capture)

	create_resp = client.post(
		"/yee/dashboard/auditor-invites",
		headers=manager_headers,
		json={"email": email},
	)
	assert create_resp.status_code == 200, create_resp.text
	token = captured[-1]["invite_url"].rsplit("/", 1)[-1]

	accept_resp = client.post(
		f"/yee/auth/invite/{token}/accept",
		json={"name": f"Auditor {email.split('@')[0]}", "password": SEED_PASSWORD},
	)
	assert accept_resp.status_code == 200, accept_resp.text
	accept_body = accept_resp.json()
	headers = _bearer_headers(accept_body["access_token"])

	# Fetch auditor profile id via the auditors list (manager view)
	auditors_resp = client.get("/yee/dashboard/auditors", headers=manager_headers)
	assert auditors_resp.status_code == 200, auditors_resp.text
	auditor_row = next(
		(a for a in auditors_resp.json() if a["email"] == email),
		None,
	)
	assert auditor_row is not None, f"Auditor {email} not found in auditors list"

	return {
		"email": email,
		"headers": headers,
		"auditor_profile_id": auditor_row["id"],
		"user": accept_body["user"],
	}


def _create_project_and_place(
	client: TestClient,
	manager_headers: dict[str, str],
	suffix: str,
) -> tuple[str, str]:
	"""Create a project and a place linked to it.  Returns (project_id, place_id)."""

	proj_resp = client.post(
		"/yee/dashboard/projects",
		headers=manager_headers,
		json={"name": f"Assignment Test Project {suffix}"},
	)
	assert proj_resp.status_code == 200, proj_resp.text
	project_id = proj_resp.json()["id"]

	place_resp = client.post(
		"/yee/dashboard/places",
		headers=manager_headers,
		json={
			"project_id": project_id,
			"name": f"Assignment Test Place {suffix}",
			"address": "123 Test St",
			"city": "Ithaca",
			"province": "New York",
			"country": "United States",
		},
	)
	assert place_resp.status_code == 200, place_resp.text
	place_id = place_resp.json()["id"]

	return project_id, place_id


# ---------------------------------------------------------------------------
# POST /yee/dashboard/assignments — happy path
# ---------------------------------------------------------------------------


def test_manager_can_assign_auditor_to_place(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""A manager can assign an auditor to a project+place via POST /yee/dashboard/assignments."""

	suffix = _unique_suffix()
	manager = _signup_primary_manager(yee_client, yee_test_session_factory)
	project_id, place_id = _create_project_and_place(yee_client, manager["headers"], suffix)

	auditor_email = f"assign-aud-{suffix}@example.org"
	auditor = _create_auditor_via_invite(
		yee_client,
		manager["headers"],
		monkeypatch,
		email=auditor_email,
	)

	# POST /yee/dashboard/assignments
	resp = yee_client.post(
		"/yee/dashboard/assignments",
		headers=manager["headers"],
		json={
			"project_id": project_id,
			"auditor_ids": [auditor["auditor_profile_id"]],
			"place_ids": [place_id],
		},
	)
	assert resp.status_code == 200, resp.text
	body = resp.json()
	assert body["created_count"] == 1
	assert body["existing_count"] == 0
	assert len(body["assignments"]) == 1

	assignment = body["assignments"][0]
	assert assignment["auditor_id"] == auditor["auditor_profile_id"]
	assert assignment["place_id"] == place_id
	assert assignment["project_id"] == project_id
	assert assignment["id"]  # UUID string present


def test_duplicate_assignment_returns_existing_count(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""Re-posting the same auditor+place returns existing_count=1, created_count=0."""

	suffix = _unique_suffix()
	manager = _signup_primary_manager(yee_client, yee_test_session_factory)
	project_id, place_id = _create_project_and_place(yee_client, manager["headers"], suffix)

	auditor_email = f"dup-assign-{suffix}@example.org"
	auditor = _create_auditor_via_invite(
		yee_client,
		manager["headers"],
		monkeypatch,
		email=auditor_email,
	)

	payload = {
		"project_id": project_id,
		"auditor_ids": [auditor["auditor_profile_id"]],
		"place_ids": [place_id],
	}

	first = yee_client.post("/yee/dashboard/assignments", headers=manager["headers"], json=payload)
	assert first.status_code == 200
	assert first.json()["created_count"] == 1

	second = yee_client.post("/yee/dashboard/assignments", headers=manager["headers"], json=payload)
	assert second.status_code == 200
	assert second.json()["created_count"] == 0
	assert second.json()["existing_count"] == 1


# ---------------------------------------------------------------------------
# POST /yee/dashboard/assignments — authz
# ---------------------------------------------------------------------------


def test_auditor_cannot_create_assignment(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""An auditor token on POST /yee/dashboard/assignments -> 403."""

	token = _login_auditor(yee_client)
	resp = yee_client.post(
		"/yee/dashboard/assignments",
		headers=_bearer_headers(token),
		json={
			"project_id": "88888888-8888-4888-8888-888888888881",
			"auditor_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"],
			"place_ids": ["99999999-9999-4999-8999-999999999991"],
		},
	)
	assert resp.status_code == 403


def test_no_token_create_assignment_returns_401(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""POST /yee/dashboard/assignments without auth -> 401."""

	resp = yee_client.post(
		"/yee/dashboard/assignments",
		json={
			"project_id": "88888888-8888-4888-8888-888888888881",
			"auditor_ids": ["aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1"],
			"place_ids": ["99999999-9999-4999-8999-999999999991"],
		},
	)
	assert resp.status_code == 401


# ---------------------------------------------------------------------------
# DELETE /yee/dashboard/assignments — happy path
# ---------------------------------------------------------------------------


def test_manager_can_delete_assignment(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""DELETE /yee/dashboard/assignments removes the assignment."""

	suffix = _unique_suffix()
	manager = _signup_primary_manager(yee_client, yee_test_session_factory)
	project_id, place_id = _create_project_and_place(yee_client, manager["headers"], suffix)

	auditor_email = f"del-assign-{suffix}@example.org"
	auditor = _create_auditor_via_invite(
		yee_client,
		manager["headers"],
		monkeypatch,
		email=auditor_email,
	)

	# Create the assignment first
	create_resp = yee_client.post(
		"/yee/dashboard/assignments",
		headers=manager["headers"],
		json={
			"project_id": project_id,
			"auditor_ids": [auditor["auditor_profile_id"]],
			"place_ids": [place_id],
		},
	)
	assert create_resp.status_code == 200
	assert create_resp.json()["created_count"] == 1

	# DELETE the assignment
	del_resp = yee_client.request(
		"DELETE",
		"/yee/dashboard/assignments",
		headers=manager["headers"],
		json={
			"project_id": project_id,
			"auditor_id": auditor["auditor_profile_id"],
			"place_id": place_id,
		},
	)
	assert del_resp.status_code == 200, del_resp.text
	assert del_resp.json()["deleted_count"] == 1

	# Verify it is gone — re-deleting should 404
	del_again = yee_client.request(
		"DELETE",
		"/yee/dashboard/assignments",
		headers=manager["headers"],
		json={
			"project_id": project_id,
			"auditor_id": auditor["auditor_profile_id"],
			"place_id": place_id,
		},
	)
	assert del_again.status_code == 404


# ---------------------------------------------------------------------------
# DELETE /yee/dashboard/assignments — authz
# ---------------------------------------------------------------------------


def test_auditor_cannot_delete_assignment(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""An auditor token on DELETE /yee/dashboard/assignments -> 403."""

	token = _login_auditor(yee_client)
	resp = yee_client.request(
		"DELETE",
		"/yee/dashboard/assignments",
		headers=_bearer_headers(token),
		json={
			"project_id": "88888888-8888-4888-8888-888888888881",
			"auditor_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
		},
	)
	assert resp.status_code == 403


def test_no_token_delete_assignment_returns_401(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""DELETE /yee/dashboard/assignments without auth -> 401."""

	resp = yee_client.request(
		"DELETE",
		"/yee/dashboard/assignments",
		json={
			"project_id": "88888888-8888-4888-8888-888888888881",
			"auditor_id": "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1",
		},
	)
	assert resp.status_code == 401


# ---------------------------------------------------------------------------
# GET /yee/dashboard/my-places — auditor happy path
# ---------------------------------------------------------------------------


def test_auditor_sees_assigned_places_via_my_places(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""An auditor can see their assigned places via GET /yee/dashboard/my-places."""

	suffix = _unique_suffix()
	manager = _signup_primary_manager(yee_client, yee_test_session_factory)
	project_id, place_id = _create_project_and_place(yee_client, manager["headers"], suffix)

	auditor_email = f"myplaces-{suffix}@example.org"
	auditor = _create_auditor_via_invite(
		yee_client,
		manager["headers"],
		monkeypatch,
		email=auditor_email,
	)

	# Assign auditor to the place
	assign_resp = yee_client.post(
		"/yee/dashboard/assignments",
		headers=manager["headers"],
		json={
			"project_id": project_id,
			"auditor_ids": [auditor["auditor_profile_id"]],
			"place_ids": [place_id],
		},
	)
	assert assign_resp.status_code == 200

	# GET my-places as the auditor
	my_places_resp = yee_client.get(
		"/yee/dashboard/my-places",
		headers=auditor["headers"],
	)
	assert my_places_resp.status_code == 200, my_places_resp.text
	places = my_places_resp.json()
	assert isinstance(places, list)

	# The auditor should see the place we just assigned
	matching = [p for p in places if p["id"] == place_id]
	assert len(matching) == 1
	assert matching[0]["name"] == f"Assignment Test Place {suffix}"
	assert matching[0]["project"] == f"Assignment Test Project {suffix}"
	assert "address" in matching[0]
	assert "audits" in matching[0]


def test_seeded_auditor_my_places_returns_seed_assignments(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""Seeded auditor-demo-1 sees Hub, Plaza, Eastside (3 Baseline assignments) via my-places."""

	token = _login_auditor(yee_client, SEED_AUDITOR_EMAIL, SEED_PASSWORD)
	resp = yee_client.get("/yee/dashboard/my-places", headers=_bearer_headers(token))
	assert resp.status_code == 200, resp.text
	places = resp.json()
	assert isinstance(places, list)

	place_names = {p["name"] for p in places}
	# Auditor One is assigned to Hub, Plaza, and Eastside in the Baseline project
	assert "Westside Youth Hub" in place_names
	assert "South Transit Plaza" in place_names
	assert "Eastside Community Green" in place_names


# ---------------------------------------------------------------------------
# GET /yee/dashboard/my-places — authz
# ---------------------------------------------------------------------------


def test_manager_cannot_access_my_places(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""A manager token on GET /yee/dashboard/my-places -> 403 (auditor only)."""

	manager = _signup_primary_manager(yee_client, yee_test_session_factory)
	resp = yee_client.get("/yee/dashboard/my-places", headers=manager["headers"])
	assert resp.status_code == 403


def test_no_token_my_places_returns_401(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""GET /yee/dashboard/my-places without auth -> 401."""

	resp = yee_client.get("/yee/dashboard/my-places")
	assert resp.status_code == 401

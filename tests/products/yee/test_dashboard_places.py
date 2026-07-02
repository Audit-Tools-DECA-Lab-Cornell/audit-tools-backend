"""YEE dashboard place CRUD integration tests (Flow J).

Covers GET list, GET detail, POST create, and PATCH update for
``/yee/dashboard/places`` with manager, admin, auditor, and
unauthenticated callers.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.seed import (
	YEE_PLACE_COMMONS_ID,
	YEE_PLACE_HUB_ID,
	YEE_PLACE_LIBRARY_ID,
	YEE_PLACE_PLAZA_ID,
	YEE_PLACE_GREEN_ID,
	YEE_PROJECT_CORE_ID,
)
from tests.products.yee._helpers import (
	SEED_MANAGER_EMAIL,
	SEED_PASSWORD,
	_bearer_headers,
	_login_auditor,
	_signup_primary_manager,
	_unique_suffix,
)

SEED_ADMIN_EMAIL = "admin-demo@yee.local"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login_manager(client: TestClient) -> str:
	"""Login the seeded demo manager and return a bearer token."""
	resp = client.post("/yee/auth/login", json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD})
	assert resp.status_code == 200, resp.text
	return resp.json()["access_token"]


def _login_admin(client: TestClient) -> str:
	"""Login the seeded admin and return a bearer token."""
	resp = client.post("/yee/auth/login", json={"email": SEED_ADMIN_EMAIL, "password": SEED_PASSWORD})
	assert resp.status_code == 200, resp.text
	return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# GET /yee/dashboard/places — list
# ---------------------------------------------------------------------------


def test_manager_can_list_places(yee_client: TestClient) -> None:
	"""Seeded manager sees the 5 seeded places as a bare JSON array."""

	token = _login_manager(yee_client)
	resp = yee_client.get("/yee/dashboard/places", headers=_bearer_headers(token))
	assert resp.status_code == 200, resp.text

	data = resp.json()
	assert isinstance(data, list)

	seeded_ids = {
		str(YEE_PLACE_HUB_ID),
		str(YEE_PLACE_PLAZA_ID),
		str(YEE_PLACE_LIBRARY_ID),
		str(YEE_PLACE_COMMONS_ID),
		str(YEE_PLACE_GREEN_ID),
	}
	returned_ids = {item["id"] for item in data}
	assert seeded_ids.issubset(returned_ids), f"Expected {seeded_ids} in {returned_ids}"

	# Spot-check response shape on one place
	hub = next(p for p in data if p["id"] == str(YEE_PLACE_HUB_ID))
	assert "name" in hub
	assert "project_id" in hub
	assert "project" in hub
	assert "address" in hub
	assert "audits" in hub
	assert "last_audit" in hub
	assert "status" in hub


def test_admin_can_list_places(yee_client: TestClient) -> None:
	"""Admin role is also allowed to list places (200)."""

	token = _login_admin(yee_client)
	resp = yee_client.get("/yee/dashboard/places", headers=_bearer_headers(token))
	assert resp.status_code == 200, resp.text
	assert isinstance(resp.json(), list)


def test_auditor_cannot_list_places(yee_client: TestClient) -> None:
	"""Auditor tokens must be rejected with 403."""

	token = _login_auditor(yee_client)
	resp = yee_client.get("/yee/dashboard/places", headers=_bearer_headers(token))
	assert resp.status_code == 403, resp.text


def test_unauthenticated_cannot_list_places(yee_client: TestClient) -> None:
	"""No bearer token -> 401."""

	resp = yee_client.get("/yee/dashboard/places")
	assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# GET /yee/dashboard/places/{place_id} — detail
# ---------------------------------------------------------------------------


def test_manager_can_get_place_detail(yee_client: TestClient) -> None:
	"""Manager retrieves full detail for a seeded place."""

	token = _login_manager(yee_client)
	resp = yee_client.get(
		f"/yee/dashboard/places/{YEE_PLACE_HUB_ID}",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 200, resp.text

	data = resp.json()
	assert data["id"] == str(YEE_PLACE_HUB_ID)
	assert "name" in data
	assert "address" in data
	assert "city" in data
	assert "province" in data
	assert "country" in data
	assert "place_type" in data
	assert "status" in data
	assert "project_id" in data
	assert "project_name" in data
	assert "assigned_auditors" in data
	assert "total_audits" in data
	assert "submitted_audits" in data
	assert isinstance(data["auditors"], list)
	assert "comparisons" in data


def test_place_detail_unknown_id_returns_404(yee_client: TestClient) -> None:
	"""Unknown place id -> 404."""

	token = _login_manager(yee_client)
	fake_id = uuid.uuid4()
	resp = yee_client.get(
		f"/yee/dashboard/places/{fake_id}",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# POST /yee/dashboard/places — create
# ---------------------------------------------------------------------------


def test_manager_can_create_place(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""Manager creates a place linked to a project via CreatePlaceRequest fields."""

	# Use an isolated manager with its own project
	mgr = _signup_primary_manager(yee_client, yee_test_session_factory)
	suffix = _unique_suffix()

	# First create a project in the manager's scope
	proj_resp = yee_client.post(
		"/yee/dashboard/projects",
		headers=mgr["headers"],
		json={"name": f"Place Test Project {suffix}"},
	)
	assert proj_resp.status_code == 200, proj_resp.text
	project_id = proj_resp.json()["id"]

	place_name = f"Test Place {suffix}"
	resp = yee_client.post(
		"/yee/dashboard/places",
		headers=mgr["headers"],
		json={
			"project_id": project_id,
			"name": place_name,
			"address": "123 Test Street",
			"city": "Testville",
			"province": "New York",
			"country": "United States",
			"postal_code": "14850",
			"place_type": "park",
			"start_date": "2026-07-01",
			"end_date": "2026-12-31",
			"estimated_auditors": 3,
			"auditor_population_types": ["student"],
			"auditor_inclusion_exclusion_criteria": "Ages 16-24",
			"auditor_notes": "Evening sessions",
			"lat": 42.4440,
			"lng": -76.5019,
		},
	)
	# The route returns 200 (no explicit status_code on decorator)
	assert resp.status_code == 200, resp.text

	data = resp.json()
	assert data["name"] == place_name
	assert "id" in data
	assert data["project_id"] == project_id
	assert data["audits"] == 0
	assert data["status"] == "Needs review"

	# Verify we can retrieve it
	detail = yee_client.get(
		f"/yee/dashboard/places/{data['id']}",
		headers=mgr["headers"],
	)
	assert detail.status_code == 200, detail.text
	assert detail.json()["id"] == data["id"]
	assert detail.json()["name"] == place_name


def test_auditor_cannot_create_place(yee_client: TestClient) -> None:
	"""Auditor is blocked from creating places (403)."""

	token = _login_auditor(yee_client)
	resp = yee_client.post(
		"/yee/dashboard/places",
		headers=_bearer_headers(token),
		json={
			"project_id": str(YEE_PROJECT_CORE_ID),
			"name": "Should fail",
			"address": "Nowhere",
		},
	)
	assert resp.status_code == 403, resp.text


def test_create_place_with_unknown_project_returns_404(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""Creating a place with a non-existent project_id returns 404."""

	mgr = _signup_primary_manager(yee_client, yee_test_session_factory)
	fake_project_id = str(uuid.uuid4())
	resp = yee_client.post(
		"/yee/dashboard/places",
		headers=mgr["headers"],
		json={
			"project_id": fake_project_id,
			"name": "Orphan place",
			"address": "123 Ghost Lane",
		},
	)
	assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# PATCH /yee/dashboard/places/{place_id} — update
# ---------------------------------------------------------------------------


def test_manager_can_update_place(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""Manager updates a place's name and address via UpdatePlaceRequest."""

	mgr = _signup_primary_manager(yee_client, yee_test_session_factory)
	suffix = _unique_suffix()

	# Create project + place to update
	proj_resp = yee_client.post(
		"/yee/dashboard/projects",
		headers=mgr["headers"],
		json={"name": f"Update Place Project {suffix}"},
	)
	assert proj_resp.status_code == 200, proj_resp.text
	project_id = proj_resp.json()["id"]

	create_resp = yee_client.post(
		"/yee/dashboard/places",
		headers=mgr["headers"],
		json={
			"project_id": project_id,
			"name": f"Original Place {suffix}",
			"address": "100 Old Road",
		},
	)
	assert create_resp.status_code == 200, create_resp.text
	place_id = create_resp.json()["id"]

	# Patch it
	updated_name = f"Updated Place {suffix}"
	patch_resp = yee_client.patch(
		f"/yee/dashboard/places/{place_id}",
		headers=mgr["headers"],
		json={
			"project_id": project_id,
			"name": updated_name,
			"address": "200 New Road",
			"city": "Newtown",
			"province": "California",
			"country": "United States",
			"postal_code": "90210",
			"place_type": "library",
		},
	)
	assert patch_resp.status_code == 200, patch_resp.text
	assert patch_resp.json()["name"] == updated_name
	assert patch_resp.json()["id"] == place_id


def test_auditor_cannot_update_place(yee_client: TestClient) -> None:
	"""Auditor is blocked from updating places (403)."""

	token = _login_auditor(yee_client)
	resp = yee_client.patch(
		f"/yee/dashboard/places/{YEE_PLACE_HUB_ID}",
		headers=_bearer_headers(token),
		json={
			"project_id": str(YEE_PROJECT_CORE_ID),
			"name": "Nope",
			"address": "Blocked",
		},
	)
	assert resp.status_code == 403, resp.text


def test_update_unknown_place_returns_404(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""Updating a non-existent place returns 404."""

	mgr = _signup_primary_manager(yee_client, yee_test_session_factory)

	# Create a project so we have a valid project_id for the payload
	proj_resp = yee_client.post(
		"/yee/dashboard/projects",
		headers=mgr["headers"],
		json={"name": f"Ghost Place Project {_unique_suffix()}"},
	)
	assert proj_resp.status_code == 200, proj_resp.text
	project_id = proj_resp.json()["id"]

	fake_id = uuid.uuid4()
	resp = yee_client.patch(
		f"/yee/dashboard/places/{fake_id}",
		headers=mgr["headers"],
		json={
			"project_id": project_id,
			"name": "Ghost",
			"address": "Nowhere",
		},
	)
	assert resp.status_code == 404, resp.text

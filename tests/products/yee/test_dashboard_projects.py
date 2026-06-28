"""YEE dashboard project CRUD integration tests (Flow I).

Covers GET list, GET detail, POST create, and PATCH update for
``/yee/dashboard/projects`` with manager, admin, auditor, and
unauthenticated callers.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.seed import (
	YEE_PROJECT_CORE_ID,
	YEE_PROJECT_FOLLOW_UP_ID,
)
from tests.products.yee._helpers import (
	SEED_AUDITOR_EMAIL,
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
# GET /yee/dashboard/projects — list
# ---------------------------------------------------------------------------


def test_manager_can_list_projects(yee_client: TestClient) -> None:
	"""Seeded manager sees the 2 seeded projects as a bare JSON array."""

	token = _login_manager(yee_client)
	resp = yee_client.get("/yee/dashboard/projects", headers=_bearer_headers(token))
	assert resp.status_code == 200, resp.text

	data = resp.json()
	assert isinstance(data, list)

	seeded_ids = {str(YEE_PROJECT_CORE_ID), str(YEE_PROJECT_FOLLOW_UP_ID)}
	returned_ids = {item["id"] for item in data}
	assert seeded_ids.issubset(returned_ids), f"Expected {seeded_ids} in {returned_ids}"

	# Spot-check response shape on one project
	baseline = next(p for p in data if p["id"] == str(YEE_PROJECT_CORE_ID))
	assert "name" in baseline
	assert "summary" in baseline
	assert "places" in baseline
	assert "audits" in baseline
	assert "status" in baseline


def test_admin_can_list_projects(yee_client: TestClient) -> None:
	"""Admin role is also allowed to list projects (200)."""

	token = _login_admin(yee_client)
	resp = yee_client.get("/yee/dashboard/projects", headers=_bearer_headers(token))
	assert resp.status_code == 200, resp.text
	assert isinstance(resp.json(), list)


def test_auditor_cannot_list_projects(yee_client: TestClient) -> None:
	"""Auditor tokens must be rejected with 403."""

	token = _login_auditor(yee_client)
	resp = yee_client.get("/yee/dashboard/projects", headers=_bearer_headers(token))
	assert resp.status_code == 403, resp.text


def test_unauthenticated_cannot_list_projects(yee_client: TestClient) -> None:
	"""No bearer token -> 401."""

	resp = yee_client.get("/yee/dashboard/projects")
	assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# GET /yee/dashboard/projects/{project_id} — detail
# ---------------------------------------------------------------------------


def test_manager_can_get_project_detail(yee_client: TestClient) -> None:
	"""Manager retrieves full detail for a seeded project."""

	token = _login_manager(yee_client)
	resp = yee_client.get(
		f"/yee/dashboard/projects/{YEE_PROJECT_CORE_ID}",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 200, resp.text

	data = resp.json()
	assert data["id"] == str(YEE_PROJECT_CORE_ID)
	assert "name" in data
	assert "description" in data
	assert "status" in data
	assert "organization" in data
	assert "total_places" in data
	assert "total_audits" in data
	assert "submitted_audits" in data
	assert "assigned_auditors" in data
	assert isinstance(data["places"], list)
	assert isinstance(data["auditors"], list)
	assert isinstance(data["latest_audits"], list)


def test_project_detail_unknown_id_returns_404(yee_client: TestClient) -> None:
	"""Unknown project id -> 404."""

	token = _login_manager(yee_client)
	fake_id = uuid.uuid4()
	resp = yee_client.get(
		f"/yee/dashboard/projects/{fake_id}",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 404, resp.text


# ---------------------------------------------------------------------------
# POST /yee/dashboard/projects — create
# ---------------------------------------------------------------------------


def test_manager_can_create_project(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""Manager creates a project via CreateProjectRequest fields and gets it back."""

	# Use an isolated manager so the created project doesn't pollute the
	# seeded demo manager's scope for other tests.
	mgr = _signup_primary_manager(yee_client, yee_test_session_factory)
	suffix = _unique_suffix()
	project_name = f"Test Project {suffix}"

	resp = yee_client.post(
		"/yee/dashboard/projects",
		headers=mgr["headers"],
		json={
			"name": project_name,
			"description": f"Description for {suffix}",
			"place_types": ["park", "plaza"],
			"start_date": "2026-07-01",
			"end_date": "2026-12-31",
			"estimated_places": 5,
			"auditor_population_types": ["student"],
			"auditor_inclusion_exclusion_criteria": "Ages 16-24",
			"auditor_notes": "Morning sessions only",
		},
	)
	# The route returns 200 (no explicit status_code=201 on the decorator)
	assert resp.status_code == 200, resp.text

	data = resp.json()
	assert data["name"] == project_name
	assert "id" in data
	assert data["places"] == 0
	assert data["audits"] == 0
	assert data["status"] == "Planning"  # start_date present -> "Active" only after commit refresh
	# Verify we can retrieve it
	detail = yee_client.get(
		f"/yee/dashboard/projects/{data['id']}",
		headers=mgr["headers"],
	)
	assert detail.status_code == 200, detail.text
	assert detail.json()["id"] == data["id"]


def test_auditor_cannot_create_project(yee_client: TestClient) -> None:
	"""Auditor is blocked from creating projects (403)."""

	token = _login_auditor(yee_client)
	resp = yee_client.post(
		"/yee/dashboard/projects",
		headers=_bearer_headers(token),
		json={"name": "Should fail"},
	)
	assert resp.status_code == 403, resp.text


# ---------------------------------------------------------------------------
# PATCH /yee/dashboard/projects/{project_id} — update
# ---------------------------------------------------------------------------


def test_manager_can_update_project(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""Manager updates a project's name and description via UpdateProjectRequest."""

	mgr = _signup_primary_manager(yee_client, yee_test_session_factory)
	suffix = _unique_suffix()

	# Create a project to update
	create_resp = yee_client.post(
		"/yee/dashboard/projects",
		headers=mgr["headers"],
		json={"name": f"Original {suffix}"},
	)
	assert create_resp.status_code == 200, create_resp.text
	project_id = create_resp.json()["id"]

	# Patch it
	updated_name = f"Updated {suffix}"
	patch_resp = yee_client.patch(
		f"/yee/dashboard/projects/{project_id}",
		headers=mgr["headers"],
		json={
			"name": updated_name,
			"description": "Updated description",
			"place_types": ["school"],
			"estimated_places": 10,
			"auditor_population_types": [],
		},
	)
	assert patch_resp.status_code == 200, patch_resp.text
	assert patch_resp.json()["name"] == updated_name
	assert patch_resp.json()["id"] == project_id


def test_auditor_cannot_update_project(yee_client: TestClient) -> None:
	"""Auditor is blocked from updating projects (403)."""

	token = _login_auditor(yee_client)
	resp = yee_client.patch(
		f"/yee/dashboard/projects/{YEE_PROJECT_CORE_ID}",
		headers=_bearer_headers(token),
		json={"name": "Nope"},
	)
	assert resp.status_code == 403, resp.text


def test_update_unknown_project_returns_404(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""Updating a non-existent project returns 404."""

	mgr = _signup_primary_manager(yee_client, yee_test_session_factory)
	fake_id = uuid.uuid4()
	resp = yee_client.patch(
		f"/yee/dashboard/projects/{fake_id}",
		headers=mgr["headers"],
		json={"name": "Ghost"},
	)
	assert resp.status_code == 404, resp.text

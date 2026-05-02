"""Backend smoke coverage for the shared Playspace E2E seed foundation."""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.products.playspace.conftest import PlayspaceSeedSnapshot

ADMIN_EMAIL = "playspace.admin@example.org"
MANAGER_EMAIL = "amelia.carter@example.org"
AUDITOR_EMAIL = "ariana.ngata@example.org"
SEED_PASSWORD = "DemoPass123!"


def _login(client: TestClient, email: str) -> str:
	"""Login one seeded Playspace user and return a bearer token."""

	response = client.post(
		"/playspace/auth/login",
		json={"email": email, "password": SEED_PASSWORD},
	)
	assert response.status_code == 200
	return response.json()["access_token"]


def _bearer(token: str) -> dict[str, str]:
	"""Build bearer auth headers for authenticated endpoint calls."""

	return {"Authorization": f"bearer {token}"}


def test_seeded_roles_can_authenticate_and_read_core_dashboards(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""Seeded admin, manager, and auditor identities should support cross-app E2E setup."""

	admin_token = _login(playspace_client, ADMIN_EMAIL)
	manager_token = _login(playspace_client, MANAGER_EMAIL)
	auditor_token = _login(playspace_client, AUDITOR_EMAIL)

	admin_response = playspace_client.get("/playspace/admin/overview", headers=_bearer(admin_token))
	assert admin_response.status_code == 200

	projects_response = playspace_client.get(
		f"/playspace/accounts/{playspace_seed_snapshot.manager_account_id}/projects",
		headers=_bearer(manager_token),
	)
	assert projects_response.status_code == 200
	assert any(project["id"] == playspace_seed_snapshot.urban_project_id for project in projects_response.json())

	auditor_places_response = playspace_client.get("/playspace/auditor/me/places", headers=_bearer(auditor_token))
	assert auditor_places_response.status_code == 200
	assert auditor_places_response.json()["total_count"] >= 1


def test_auditor_can_create_or_resume_seeded_place_audit(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""The seeded auditor can reach the audit lifecycle entrypoint used by web and mobile."""

	auditor_token = _login(playspace_client, AUDITOR_EMAIL)
	headers = _bearer(auditor_token)
	places_response = playspace_client.get("/playspace/auditor/me/places?page_size=100", headers=headers)
	assert places_response.status_code == 200
	assigned_places = places_response.json()["items"]
	assert assigned_places
	target_place = next(
		(place for place in assigned_places if place["place_audit_status"] != "submitted"),
		assigned_places[0],
	)

	response = playspace_client.post(
		f"/playspace/places/{target_place['place_id']}/audits/access",
		headers=headers,
		json={"project_id": target_place["project_id"], "execution_mode": "both"},
	)
	assert response.status_code == 200
	payload = response.json()
	assert payload["place_id"] == target_place["place_id"]
	assert payload["project_id"] == target_place["project_id"]
	assert payload["status"] in {"IN_PROGRESS", "SUBMITTED"}

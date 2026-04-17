"""Integration coverage for the full Playspace FastAPI route surface."""

from __future__ import annotations

import uuid

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app
from tests.products.playspace.conftest import PlayspaceSeedSnapshot

MANAGER_EMAIL = "manager@example.org"
ADMIN_EMAIL = "playspace.admin@example.org"
SEED_PASSWORD = "DemoPass123!"


def _bearer_headers(access_token: str) -> dict[str, str]:
	"""Build bearer auth headers for session-backed authorization."""

	return {
		"Authorization": f"bearer {access_token}",
	}


def _login_manager(client: TestClient) -> str:
	"""Login the seeded manager account and return a bearer token."""

	response = client.post(
		"/playspace/auth/login",
		json={"email": MANAGER_EMAIL, "password": SEED_PASSWORD},
	)
	assert response.status_code == 200
	return response.json()["access_token"]


def _login_admin(client: TestClient) -> str:
	"""Login the seeded admin account and return a bearer token."""

	response = client.post(
		"/playspace/auth/login",
		json={"email": ADMIN_EMAIL, "password": SEED_PASSWORD},
	)
	assert response.status_code == 200
	return response.json()["access_token"]


def _login_auditor(client: TestClient, email: str) -> str:
	"""Login an auditor account and return a bearer token."""

	response = client.post(
		"/playspace/auth/login",
		json={"email": email, "password": SEED_PASSWORD},
	)
	assert response.status_code == 200
	return response.json()["access_token"]


def _signup_and_login_auditor(
	client: TestClient,
	email: str,
	full_name: str,
	auditor_code: str,
) -> str:
	"""Create an auditor user account and return a bearer token.

	The management API auto-creates an Account + AuditorProfile when a
	manager creates an auditor profile.  This helper creates only the User
	side; the AuditorProfile link is backfilled by the migration/seed.
	For tests that need an auditor with a full profile, use the management
	API to create the profile and then login via the auto-created account.
	"""

	signup_response = client.post(
		"/playspace/auth/signup",
		json={
			"email": email,
			"password": SEED_PASSWORD,
			"name": full_name,
			"account_type": "AUDITOR",
		},
	)
	assert signup_response.status_code == 201
	return signup_response.json()["access_token"]


def _route_inventory() -> set[tuple[str, str]]:
	"""Collect the concrete Playspace route methods and paths from the app."""

	inventory: set[tuple[str, str]] = set()
	print(app.routes)
	for route in app.routes:
		if not isinstance(route, APIRoute):
			continue
		if not route.path.startswith("/playspace"):
			continue
		for method in route.methods:
			if method in {"HEAD", "OPTIONS"}:
				continue
			inventory.add((method, route.path))
	return inventory


def _unique_suffix() -> str:
	"""Create a short unique suffix for ephemeral test resource names."""

	return uuid.uuid4().hex[:8]


def _create_project(client: TestClient, manager_token: str, *, suffix: str) -> dict[str, object]:
	"""Create an ephemeral project through the Playspace management API."""

	response = client.post(
		"/playspace/projects",
		headers=_bearer_headers(manager_token),
		json={
			"name": f"Endpoint Project {suffix}",
			"overview": f"Endpoint project {suffix}",
			"place_types": ["public playspace"],
			"start_date": "2026-01-10",
			"end_date": "2026-12-20",
			"est_places": 1,
			"est_auditors": 1,
			"auditor_description": f"Endpoint guidance {suffix}",
		},
	)
	assert response.status_code == 201
	return response.json()


def _create_place(
	client: TestClient,
	manager_token: str,
	*,
	project_id: str,
	suffix: str,
) -> dict[str, object]:
	"""Create an ephemeral place linked to one project."""

	response = client.post(
		"/playspace/places",
		headers=_bearer_headers(manager_token),
		json={
			"project_ids": [project_id],
			"name": f"Endpoint Place {suffix}",
			"city": "Auckland",
			"province": "Auckland",
			"country": "New Zealand",
			"place_type": "public playspace",
			"lat": -36.85,
			"lng": 174.76,
			"start_date": "2026-02-01",
			"end_date": "2026-11-30",
			"est_auditors": 1,
			"auditor_description": f"Endpoint place guidance {suffix}",
		},
	)
	assert response.status_code == 201
	return response.json()


def _create_auditor_profile(
	client: TestClient,
	manager_token: str,
	*,
	suffix: str,
) -> dict[str, object]:
	"""Create an ephemeral auditor profile through the Playspace management API."""

	response = client.post(
		"/playspace/auditor-profiles",
		headers=_bearer_headers(manager_token),
		json={
			"email": f"endpoint-{suffix}@example.org",
			"full_name": f"Endpoint Auditor {suffix}",
			"auditor_code": f"EPT-{suffix.upper()}",
			"country": "New Zealand",
			"role": "Tester",
		},
	)
	assert response.status_code == 201
	return response.json()


def test_playspace_route_inventory_matches_expected_surface() -> None:
	"""Keep the endpoint coverage suite aligned with the real Playspace route tree."""

	expected_routes = {
		("POST", "/playspace/auth/signup"),
		("POST", "/playspace/auth/login"),
		("GET", "/playspace/auth/me"),
		("POST", "/playspace/auth/complete-profile"),
		("GET", "/playspace/auth/verify-email"),
		("POST", "/playspace/auth/resend-verification"),
		("GET", "/playspace/auth/invite/{token}"),
		("POST", "/playspace/auth/invite/{token}/accept"),
		("GET", "/playspace/accounts/{account_id}"),
		("GET", "/playspace/accounts/{account_id}/manager-profiles"),
		("GET", "/playspace/accounts/{account_id}/projects"),
		("GET", "/playspace/accounts/{account_id}/auditors"),
		("GET", "/playspace/accounts/{account_id}/places"),
		("GET", "/playspace/accounts/{account_id}/audits"),
		("GET", "/playspace/projects/{project_id}"),
		("GET", "/playspace/projects/{project_id}/stats"),
		("GET", "/playspace/projects/{project_id}/places"),
		("GET", "/playspace/places/{place_id}/audits"),
		("GET", "/playspace/places/{place_id}/history"),
		("GET", "/playspace/auditor-profiles/{auditor_profile_id}/assignments"),
		("POST", "/playspace/auditor-profiles/{auditor_profile_id}/assignments"),
		(
			"PATCH",
			"/playspace/auditor-profiles/{auditor_profile_id}/assignments/{assignment_id}",
		),
		(
			"DELETE",
			"/playspace/auditor-profiles/{auditor_profile_id}/assignments/{assignment_id}",
		),
		("POST", "/playspace/places/{place_id}/audits/access"),
		("GET", "/playspace/audits/{audit_id}"),
		("PATCH", "/playspace/audits/{audit_id}/draft"),
		("PATCH", "/playspace/places/{place_id}/audits/draft"),
		("POST", "/playspace/audits/{audit_id}/submit"),
		("GET", "/playspace/auditor/me/places"),
		("GET", "/playspace/auditor/me/audits"),
		("GET", "/playspace/auditor/me/dashboard-summary"),
		("GET", "/playspace/admin/overview"),
		("GET", "/playspace/admin/accounts"),
		("GET", "/playspace/admin/projects"),
		("GET", "/playspace/admin/places"),
		("GET", "/playspace/admin/auditors"),
		("GET", "/playspace/admin/audits"),
		("GET", "/playspace/admin/system"),
		("GET", "/playspace/admin/instruments"),
		("POST", "/playspace/bulk-assignments"),
		("POST", "/playspace/admin/instruments"),
		("GET", "/playspace/instruments/active/{instrument_key}"),
		("PATCH", "/playspace/admin/instruments/{instrument_id}"),
		("GET", "/playspace/me"),
		("GET", "/playspace/me/auditor-profile"),
		("GET", "/playspace/instrument"),
		("PATCH", "/playspace/accounts/{account_id}"),
		("POST", "/playspace/projects"),
		("PATCH", "/playspace/projects/{project_id}"),
		("DELETE", "/playspace/projects/{project_id}"),
		("POST", "/playspace/places"),
		("PATCH", "/playspace/places/{place_id}"),
		("DELETE", "/playspace/places/{place_id}"),
		("POST", "/playspace/auditor-profiles"),
		("PATCH", "/playspace/auditor-profiles/{auditor_profile_id}"),
		("DELETE", "/playspace/auditor-profiles/{auditor_profile_id}"),
		("GET", "/playspace/api/notifications"),
		("GET", "/playspace/api/notifications/unread/count"),
		("POST", "/playspace/api/notifications/read-all"),
		("POST", "/playspace/api/notifications/{notification_id}/read"),
	}

	assert _route_inventory() == expected_routes


def test_auth_self_service_and_instrument_endpoints(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""Exercise auth, self-service, and instrument metadata endpoints."""

	signup_response = playspace_client.post(
		"/playspace/auth/signup",
		json={
			"email": "signup-playspace@example.org",
			"password": SEED_PASSWORD,
			"name": "Signup User",
			"account_type": "MANAGER",
		},
	)
	assert signup_response.status_code == 201
	assert signup_response.json()["user"]["account_type"] == "MANAGER"
	signup_token = signup_response.json()["access_token"]
	signup_auth_headers = _bearer_headers(signup_token)

	duplicate_signup_response = playspace_client.post(
		"/playspace/auth/signup",
		json={
			"email": "signup-playspace@example.org",
			"password": SEED_PASSWORD,
			"name": "Signup User",
			"account_type": "MANAGER",
		},
	)
	assert duplicate_signup_response.status_code == 409

	signup_me_response = playspace_client.get(
		"/playspace/auth/me",
		headers=signup_auth_headers,
	)
	assert signup_me_response.status_code == 200
	assert signup_me_response.json()["user"]["email"] == "signup-playspace@example.org"

	complete_profile_response = playspace_client.post(
		"/playspace/auth/complete-profile",
		headers=signup_auth_headers,
		json={"name": "Updated Playspace Manager"},
	)
	assert complete_profile_response.status_code == 200
	assert complete_profile_response.json()["user"]["name"] == "Updated Playspace Manager"

	manager_token = _login_manager(playspace_client)
	manager_auth_headers = _bearer_headers(manager_token)

	login_me_response = playspace_client.get(
		"/playspace/auth/me",
		headers=manager_auth_headers,
	)
	assert login_me_response.status_code == 200
	assert login_me_response.json()["user"]["email"] == MANAGER_EMAIL

	auditor_token = _login_auditor(
		playspace_client,
		playspace_seed_snapshot.seeded_auditor_email,
	)
	auditor_auth_headers = _bearer_headers(auditor_token)

	invalid_login_response = playspace_client.post(
		"/playspace/auth/login",
		json={
			"email": MANAGER_EMAIL,
			"password": "wrong-password",
		},
	)
	assert invalid_login_response.status_code == 401

	me_response = playspace_client.get(
		"/playspace/me",
		headers=auditor_auth_headers,
	)
	assert me_response.status_code == 200
	assert me_response.json()["account_id"] == playspace_seed_snapshot.seeded_auditor_account_id

	profile_response = playspace_client.get(
		"/playspace/me/auditor-profile",
		headers=auditor_auth_headers,
	)
	assert profile_response.status_code == 200
	assert profile_response.json()["profile_id"] == playspace_seed_snapshot.seeded_auditor_profile_id

	auditor_dashboard_response = playspace_client.get(
		"/playspace/auditor/me/dashboard-summary",
		headers=auditor_auth_headers,
	)
	assert auditor_dashboard_response.status_code == 200
	assert "total_assigned_places" in auditor_dashboard_response.json()

	instrument_response = playspace_client.get(
		"/playspace/instrument",
		headers=manager_auth_headers,
	)
	assert instrument_response.status_code == 200
	assert instrument_response.json()["instrument_key"] == "pvua_v5_2"
	assert len(instrument_response.json()["sections"]) > 0


def test_manager_dashboard_endpoints(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""Exercise every manager-facing Playspace dashboard endpoint."""

	manager_token = _login_manager(playspace_client)
	headers = _bearer_headers(manager_token)
	account_id = playspace_seed_snapshot.manager_account_id
	project_id = playspace_seed_snapshot.urban_project_id
	place_id = playspace_seed_snapshot.riverside_place_id

	account_response = playspace_client.get(f"/playspace/accounts/{account_id}", headers=headers)
	assert account_response.status_code == 200
	assert account_response.json()["id"] == account_id

	manager_profiles_response = playspace_client.get(
		f"/playspace/accounts/{account_id}/manager-profiles",
		headers=headers,
	)
	assert manager_profiles_response.status_code == 200
	assert len(manager_profiles_response.json()) >= 1

	projects_response = playspace_client.get(
		f"/playspace/accounts/{account_id}/projects",
		headers=headers,
	)
	assert projects_response.status_code == 200
	assert len(projects_response.json()) >= 1

	auditors_response = playspace_client.get(
		f"/playspace/accounts/{account_id}/auditors",
		headers=headers,
	)
	assert auditors_response.status_code == 200
	assert len(auditors_response.json()) >= 1

	places_response = playspace_client.get(
		f"/playspace/accounts/{account_id}/places",
		headers=headers,
	)
	assert places_response.status_code == 200
	assert len(places_response.json()["items"]) >= 1

	audits_response = playspace_client.get(
		f"/playspace/accounts/{account_id}/audits",
		headers=headers,
	)
	assert audits_response.status_code == 200
	assert len(audits_response.json()["items"]) >= 1

	project_detail_response = playspace_client.get(
		f"/playspace/projects/{project_id}",
		headers=headers,
	)
	assert project_detail_response.status_code == 200
	assert project_detail_response.json()["id"] == project_id

	project_stats_response = playspace_client.get(
		f"/playspace/projects/{project_id}/stats",
		headers=headers,
	)
	assert project_stats_response.status_code == 200
	assert project_stats_response.json()["project_id"] == project_id

	project_places_response = playspace_client.get(
		f"/playspace/projects/{project_id}/places",
		headers=headers,
	)
	assert project_places_response.status_code == 200
	assert len(project_places_response.json()) >= 1

	place_audits_response = playspace_client.get(
		f"/playspace/places/{place_id}/audits",
		headers=headers,
		params={"project_id": project_id},
	)
	assert place_audits_response.status_code == 200
	assert isinstance(place_audits_response.json(), list)

	place_history_response = playspace_client.get(
		f"/playspace/places/{place_id}/history",
		headers=headers,
		params={"project_id": project_id},
	)
	assert place_history_response.status_code == 200
	assert place_history_response.json()["project_id"] == project_id


def test_admin_dashboard_endpoints(
	playspace_client: TestClient,
) -> None:
	"""Exercise every admin-facing Playspace dashboard endpoint."""

	admin_token = _login_admin(playspace_client)
	headers = _bearer_headers(admin_token)

	overview_response = playspace_client.get("/playspace/admin/overview", headers=headers)
	assert overview_response.status_code == 200
	assert overview_response.json()["total_projects"] >= 1

	accounts_response = playspace_client.get("/playspace/admin/accounts", headers=headers)
	assert accounts_response.status_code == 200
	assert len(accounts_response.json()["items"]) >= 1

	projects_response = playspace_client.get("/playspace/admin/projects", headers=headers)
	assert projects_response.status_code == 200
	assert len(projects_response.json()["items"]) >= 1

	places_response = playspace_client.get("/playspace/admin/places", headers=headers)
	assert places_response.status_code == 200
	assert len(places_response.json()["items"]) >= 1

	auditors_response = playspace_client.get("/playspace/admin/auditors", headers=headers)
	assert auditors_response.status_code == 200
	assert len(auditors_response.json()["items"]) >= 1

	audits_response = playspace_client.get("/playspace/admin/audits", headers=headers)
	assert audits_response.status_code == 200
	assert len(audits_response.json()["items"]) >= 1

	system_response = playspace_client.get("/playspace/admin/system", headers=headers)
	assert system_response.status_code == 200
	assert system_response.json()["instrument_key"] == "pvua_v5_2"


def test_auditor_dashboard_endpoints(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""Exercise every auditor-facing Playspace dashboard endpoint."""

	auditor_token = _login_auditor(
		playspace_client,
		playspace_seed_snapshot.seeded_auditor_email,
	)
	headers = _bearer_headers(auditor_token)

	places_response = playspace_client.get("/playspace/auditor/me/places", headers=headers)
	assert places_response.status_code == 200
	assert len(places_response.json()["items"]) >= 1

	audits_response = playspace_client.get("/playspace/auditor/me/audits", headers=headers)
	assert audits_response.status_code == 200
	assert len(audits_response.json()["items"]) >= 1

	summary_response = playspace_client.get(
		"/playspace/auditor/me/dashboard-summary",
		headers=headers,
	)
	assert summary_response.status_code == 200
	assert summary_response.json()["total_assigned_places"] >= 1


def test_management_endpoints_cover_account_project_place_and_auditor_crud(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""Exercise every Playspace management endpoint."""

	manager_token = _login_manager(playspace_client)
	headers = _bearer_headers(manager_token)
	suffix = _unique_suffix()

	account_response = playspace_client.patch(
		f"/playspace/accounts/{playspace_seed_snapshot.manager_account_id}",
		headers=headers,
		json={"name": f"Updated Manager Account {suffix}"},
	)
	assert account_response.status_code == 200
	assert account_response.json()["id"] == playspace_seed_snapshot.manager_account_id

	project = _create_project(
		playspace_client,
		manager_token,
		suffix=suffix,
	)

	update_project_response = playspace_client.patch(
		f"/playspace/projects/{project['id']}",
		headers=headers,
		json={
			"overview": f"Updated project overview {suffix}",
			"place_types": ["public playspace", "school playspace"],
		},
	)
	assert update_project_response.status_code == 200
	assert "school playspace" in update_project_response.json()["place_types"]

	place = _create_place(
		playspace_client,
		manager_token,
		project_id=str(project["id"]),
		suffix=suffix,
	)

	update_place_response = playspace_client.patch(
		f"/playspace/places/{place['id']}",
		headers=headers,
		json={
			"name": f"Updated Endpoint Place {suffix}",
			"project_ids": [project["id"]],
			"country": "New Zealand",
		},
	)
	assert update_place_response.status_code == 200
	assert update_place_response.json()["id"] == place["id"]

	auditor_profile = {"id": playspace_seed_snapshot.seeded_auditor_profile_id}

	update_profile_response = playspace_client.patch(
		f"/playspace/auditor-profiles/{auditor_profile['id']}",
		headers=headers,
		json={
			"country": "New Zealand",
			"role": f"Updated Role {suffix}",
		},
	)
	assert update_profile_response.status_code == 200
	assert update_profile_response.json()["id"] == auditor_profile["id"]

	delete_place_response = playspace_client.delete(
		f"/playspace/places/{place['id']}",
		headers=headers,
	)
	assert delete_place_response.status_code == 204

	delete_project_response = playspace_client.delete(
		f"/playspace/projects/{project['id']}",
		headers=headers,
	)
	assert delete_project_response.status_code == 204

	delete_profile_response = playspace_client.delete(
		f"/playspace/auditor-profiles/{auditor_profile['id']}",
		headers=headers,
	)
	assert delete_profile_response.status_code == 204


def test_assignment_endpoints_cover_place_scoped_assignments(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""Exercise list/create/update/delete assignment routes for project–place rows."""

	suffix = _unique_suffix()
	manager_token = _login_manager(playspace_client)
	manager_headers = _bearer_headers(manager_token)
	project = _create_project(
		playspace_client,
		manager_token,
		suffix=suffix,
	)
	place_a = _create_place(
		playspace_client,
		manager_token,
		project_id=str(project["id"]),
		suffix=suffix,
	)
	place_b = _create_place(
		playspace_client,
		manager_token,
		project_id=str(project["id"]),
		suffix=f"{suffix}-b",
	)
	auditor_profile = _create_auditor_profile(
		playspace_client,
		manager_token,
		suffix=suffix,
	)

	list_empty_response = playspace_client.get(
		f"/playspace/auditor-profiles/{auditor_profile['id']}/assignments",
		headers=manager_headers,
	)
	assert list_empty_response.status_code == 200
	assert list_empty_response.json() == []

	create_assignment_response = playspace_client.post(
		f"/playspace/auditor-profiles/{auditor_profile['id']}/assignments",
		headers=manager_headers,
		json={
			"project_id": project["id"],
			"place_id": place_a["id"],
		},
	)
	assert create_assignment_response.status_code == 201, create_assignment_response.json()
	assignment = create_assignment_response.json()
	assert assignment["scope_type"] == "place"
	assert assignment["place_id"] == place_a["id"]

	update_assignment_response = playspace_client.patch(
		f"/playspace/auditor-profiles/{auditor_profile['id']}/assignments/{assignment['id']}",
		headers=manager_headers,
		json={
			"project_id": project["id"],
			"place_id": place_b["id"],
		},
	)
	assert update_assignment_response.status_code == 200
	assert update_assignment_response.json()["scope_type"] == "place"
	assert update_assignment_response.json()["place_id"] == place_b["id"]

	duplicate_assignment_response = playspace_client.post(
		f"/playspace/auditor-profiles/{auditor_profile['id']}/assignments",
		headers=manager_headers,
		json={
			"project_id": project["id"],
			"place_id": place_b["id"],
		},
	)
	assert duplicate_assignment_response.status_code == 409
	assert "already exists" in duplicate_assignment_response.json()["detail"]

	list_after_update_response = playspace_client.get(
		f"/playspace/auditor-profiles/{auditor_profile['id']}/assignments",
		headers=manager_headers,
	)
	assert list_after_update_response.status_code == 200
	assert len(list_after_update_response.json()) == 1

	delete_assignment_response = playspace_client.delete(
		f"/playspace/auditor-profiles/{auditor_profile['id']}/assignments/{assignment['id']}",
		headers=manager_headers,
	)
	assert delete_assignment_response.status_code == 204


def test_audit_execution_endpoints_cover_access_read_patch_and_submit(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""Exercise the full Playspace audit execution route set."""

	suffix = _unique_suffix()
	manager_token = _login_manager(playspace_client)
	manager_headers = _bearer_headers(manager_token)
	project = _create_project(
		playspace_client,
		manager_token,
		suffix=suffix,
	)
	place = _create_place(
		playspace_client,
		manager_token,
		project_id=str(project["id"]),
		suffix=suffix,
	)
	auditor_email = f"audit-exec-{suffix}@example.org"
	auditor_full_name = f"Audit Executor {suffix}"
	auditor_code = f"EXEC-{suffix.upper()}"

	# Manager creation path is canonical for tests that need a concrete profile ID.
	assignment_response = playspace_client.post(
		"/playspace/auditor-profiles",
		headers=manager_headers,
		json={
			"email": auditor_email,
			"full_name": auditor_full_name,
			"auditor_code": auditor_code,
			"country": "New Zealand",
			"role": "Audit Executor",
		},
	)
	assert assignment_response.status_code == 201
	auditor_profile_id = assignment_response.json()["id"]

	# Auditor account is created with the shared seed password.
	auditor_token = _login_auditor(playspace_client, auditor_email)
	auditor_headers = _bearer_headers(auditor_token)

	assign_to_place_response = playspace_client.post(
		f"/playspace/auditor-profiles/{auditor_profile_id}/assignments",
		headers=manager_headers,
		json={
			"project_id": project["id"],
			"place_id": place["id"],
		},
	)
	assert assign_to_place_response.status_code in (201, 409)

	access_response = playspace_client.post(
		f"/playspace/places/{place['id']}/audits/access",
		headers=auditor_headers,
		json={
			"project_id": project["id"],
			"execution_mode": "audit",
		},
	)
	assert access_response.status_code == 200
	audit_session = access_response.json()
	audit_id = audit_session["audit_id"]
	assert audit_session["project_id"] == project["id"]
	assert audit_session["schema_version"] == 1
	assert audit_session["revision"] == 1
	assert audit_session["aggregate"]["schema_version"] == 1
	assert audit_session["aggregate"]["revision"] == 1

	get_audit_response = playspace_client.get(
		f"/playspace/audits/{audit_id}",
		headers=auditor_headers,
	)
	assert get_audit_response.status_code == 200
	assert get_audit_response.json()["audit_id"] == audit_id

	patch_draft_response = playspace_client.patch(
		f"/playspace/audits/{audit_id}/draft",
		headers=auditor_headers,
		json={
			"expected_revision": audit_session["revision"],
			"meta": {"execution_mode": "survey"},
			"pre_audit": {"season": "summer"},
		},
	)
	assert patch_draft_response.status_code == 200
	assert patch_draft_response.json()["audit_id"] == audit_id
	assert patch_draft_response.json()["revision"] == 2

	stale_patch_response = playspace_client.patch(
		f"/playspace/audits/{audit_id}/draft",
		headers=auditor_headers,
		json={
			"expected_revision": 1,
			"pre_audit": {"season": "winter"},
		},
	)
	assert stale_patch_response.status_code == 409

	patch_place_draft_response = playspace_client.patch(
		f"/playspace/places/{place['id']}/audits/draft",
		headers=auditor_headers,
		params={"project_id": str(project["id"])},
		json={
			"expected_revision": patch_draft_response.json()["revision"],
			"aggregate": {
				"schema_version": 1,
				"meta": {"execution_mode": "both"},
				"pre_audit": {
					"place_size": "medium",
					"current_users_0_5": "none",
					"current_users_6_12": "a_few",
					"current_users_13_17": "a_few",
					"current_users_18_plus": "a_few",
					"playspace_busyness": "somewhat_busy",
					"season": "summer",
					"weather_conditions": [],
					"wind_conditions": "light_wind",
				},
				"sections": {},
			},
		},
	)
	assert patch_place_draft_response.status_code == 200
	assert patch_place_draft_response.json()["audit_id"] == audit_id
	assert patch_place_draft_response.json()["revision"] == 3

	refreshed_audit_response = playspace_client.get(
		f"/playspace/audits/{audit_id}",
		headers=auditor_headers,
	)
	assert refreshed_audit_response.status_code == 200
	assert refreshed_audit_response.json()["revision"] == 3
	assert refreshed_audit_response.json()["aggregate"]["meta"]["execution_mode"] == "both"

	submit_response = playspace_client.post(
		f"/playspace/audits/{audit_id}/submit",
		headers=auditor_headers,
	)
	assert submit_response.status_code == 400

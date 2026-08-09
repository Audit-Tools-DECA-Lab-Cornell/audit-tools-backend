"""Integration coverage for the full Playspace FastAPI route surface."""

from __future__ import annotations

import asyncio
import uuid

from fastapi.routing import APIRoute
import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from fastapi.testclient import TestClient

from app.main import app
from app.models import AuditorAssignment, AuditStatus, PlayspaceSubmission
from app.products import mobile_release_sources
from app.products.mobile_release_eas import clear_eas_release_cache
from app.products.mobile_release_models import MobileProduct
from tests.products.playspace.conftest import PlayspaceSeedSnapshot

MANAGER_EMAIL = "amelia.carter@example.org"
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


def _login_auditor(client: TestClient, email: str, password: str = SEED_PASSWORD) -> str:
	"""Login an auditor account and return a bearer token."""

	response = client.post(
		"/playspace/auth/login",
		json={"email": email, "password": password},
	)
	assert response.status_code == 200
	return response.json()["access_token"]


async def _load_direct_auditor_counts(
	session_factory: async_sessionmaker[AsyncSession],
	auditor_profile_id: str,
) -> tuple[int, int]:
	"""Return direct DB assignment/submitted-audit counts for one auditor."""

	auditor_id = uuid.UUID(auditor_profile_id)
	async with session_factory() as session:
		assignments_result = await session.execute(
			select(func.count(AuditorAssignment.id)).where(AuditorAssignment.auditor_profile_id == auditor_id)
		)
		completed_result = await session.execute(
			select(func.count(PlayspaceSubmission.id)).where(
				PlayspaceSubmission.auditor_profile_id == auditor_id,
				PlayspaceSubmission.status == AuditStatus.SUBMITTED,
			)
		)
	return int(assignments_result.scalar_one() or 0), int(completed_result.scalar_one() or 0)


def _signup_and_login_user(
	client: TestClient,
	email: str,
	full_name: str,
) -> str:
	"""Create a fresh authenticated playspace user and return a bearer token.

	Public signup only provisions managers (each gets their own organization
	account); auditors must be invited by a manager. Tests that just need a
	distinct authenticated identity - e.g. a second user to prove cross-user
	isolation - use this to mint one without touching the shared seeded users.
	"""

	signup_response = client.post(
		"/playspace/auth/signup",
		json={
			"email": email,
			"password": SEED_PASSWORD,
			"name": full_name,
			"account_type": "MANAGER",
		},
	)
	assert signup_response.status_code == 201, signup_response.text
	return signup_response.json()["access_token"]


def _route_inventory() -> set[tuple[str, str]]:
	"""Collect the concrete Playspace route methods and paths from the app."""

	inventory: set[tuple[str, str]] = set()
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
	payload = response.json()
	assert payload["temporary_password"]
	assert payload["temporary_password"] != SEED_PASSWORD
	return payload


def test_playspace_route_inventory_matches_expected_surface() -> None:
	"""Keep the endpoint coverage suite aligned with the real Playspace route tree."""

	expected_routes = {
		("POST", "/playspace/auth/signup"),
		("POST", "/playspace/auth/login"),
		("GET", "/playspace/auth/me"),
		("POST", "/playspace/auth/complete-profile"),
		("GET", "/playspace/auth/verify-email"),
		("POST", "/playspace/auth/resend-verification"),
		("POST", "/playspace/auth/request-access"),
		("POST", "/playspace/auth/reset-password"),
		("POST", "/playspace/auth/forgot-password"),
		("GET", "/playspace/auth/invite/{token}"),
		("POST", "/playspace/auth/invite/{token}/accept"),
		("GET", "/playspace/auth/manager-invites/{token}"),
		("POST", "/playspace/auth/manager-invites/{token}/accept"),
		("POST", "/playspace/manager-invites"),
		("GET", "/playspace/manager-invites"),
		("DELETE", "/playspace/manager-invites/{invite_id}"),
		("POST", "/playspace/manager-invites/{invite_id}/resend"),
		("GET", "/playspace/accounts/{account_id}"),
		("GET", "/playspace/accounts/{account_id}/manager-profiles"),
		("GET", "/playspace/accounts/{account_id}/projects"),
		("GET", "/playspace/accounts/{account_id}/auditors"),
		("GET", "/playspace/accounts/{account_id}/places"),
		("GET", "/playspace/accounts/{account_id}/audits"),
		("GET", "/playspace/accounts/{account_id}/export/projects/bundle"),
		("GET", "/playspace/accounts/{account_id}/export/places/bundle"),
		("GET", "/playspace/accounts/{account_id}/export/audits"),
		("GET", "/playspace/accounts/{account_id}/export/reports"),
		("GET", "/playspace/projects/{project_id}"),
		("GET", "/playspace/projects/{project_id}/stats"),
		("GET", "/playspace/projects/{project_id}/places"),
		("GET", "/playspace/places/{place_id}"),
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
		("POST", "/playspace/audits/{audit_id}/submit"),
		("POST", "/playspace/audits/{audit_id}/submit-intent"),
		("POST", "/playspace/audits/{audit_id}/notify-submit-failure"),
		("GET", "/playspace/auditor/me/places"),
		("GET", "/playspace/auditor/me/audits"),
		("GET", "/playspace/auditor/me/dashboard-summary"),
		("GET", "/playspace/admin/overview"),
		("GET", "/playspace/admin/accounts"),
		("GET", "/playspace/admin/projects"),
		("GET", "/playspace/admin/places"),
		("GET", "/playspace/admin/auditors"),
		("GET", "/playspace/admin/audits"),
		("GET", "/playspace/admin/export/reports"),
		("GET", "/playspace/admin/export/projects"),
		("GET", "/playspace/admin/export/projects/bundle"),
		("GET", "/playspace/admin/export/places"),
		("GET", "/playspace/admin/export/places/bundle"),
		("GET", "/playspace/admin/export/audits"),
		("GET", "/playspace/admin/system"),
		("GET", "/playspace/admin/bug-reports"),
		("PATCH", "/playspace/admin/bug-reports/{report_id}"),
		("GET", "/playspace/admin/known-issues"),
		("POST", "/playspace/admin/known-issues"),
		("PATCH", "/playspace/admin/known-issues/{issue_id}"),
		("DELETE", "/playspace/admin/known-issues/{issue_id}"),
		("GET", "/playspace/admin/instruments"),
		("POST", "/playspace/bulk-assignments"),
		("POST", "/playspace/admin/instruments"),
		("GET", "/playspace/instruments/active/{instrument_key}"),
		("PATCH", "/playspace/admin/instruments/{instrument_id}"),
		("DELETE", "/playspace/admin/instruments/{instrument_id}"),
		("GET", "/playspace/me"),
		("GET", "/playspace/me/auditor-profile"),
		("PATCH", "/playspace/me/auditor-profile"),
		("GET", "/playspace/me/manager-profile"),
		("PATCH", "/playspace/me/manager-profile"),
		("POST", "/playspace/me/change-password"),
		("POST", "/playspace/me/complete-onboarding"),
		("POST", "/playspace/me/complete-manager-onboarding"),
		("GET", "/playspace/me/account-deletion"),
		("POST", "/playspace/me/account-deletion"),
		("POST", "/playspace/me/manager-profile/primary-transfer"),
		("GET", "/playspace/instrument"),
		("GET", "/playspace/mobile-release-policy"),
		("POST", "/playspace/mobile-release-policy/eas-webhook"),
		("PATCH", "/playspace/accounts/{account_id}"),
		("POST", "/playspace/projects"),
		("PATCH", "/playspace/projects/{project_id}"),
		("DELETE", "/playspace/projects/{project_id}"),
		("POST", "/playspace/places"),
		("PATCH", "/playspace/places/{place_id}"),
		("DELETE", "/playspace/places/{place_id}"),
		("POST", "/playspace/places/{place_id}/place-reports"),
		("DELETE", "/playspace/places/{place_id}/place-reports/{report_index}"),
		("GET", "/playspace/known-issues/match"),
		("POST", "/playspace/bug-reports"),
		("GET", "/playspace/bug-reports/mine"),
		("GET", "/playspace/bug-reports/screenshot-upload-params"),
		("POST", "/playspace/auditor-profiles"),
		("PATCH", "/playspace/auditor-profiles/{auditor_profile_id}"),
		("DELETE", "/playspace/auditor-profiles/{auditor_profile_id}"),
		("GET", "/playspace/api/notifications"),
		("GET", "/playspace/api/notifications/unread/count"),
		("POST", "/playspace/api/notifications/read-all"),
		("POST", "/playspace/api/notifications/{notification_id}/read"),
		("POST", "/playspace/exports/notify-ready"),
	}

	assert _route_inventory() == expected_routes


def test_playspace_mobile_release_policy_is_public(monkeypatch: pytest.MonkeyPatch) -> None:
	async def empty_release(_: MobileProduct) -> None:
		return None

	monkeypatch.setattr(mobile_release_sources, "fetch_google_play_release", empty_release)
	monkeypatch.setattr(mobile_release_sources, "fetch_github_release", empty_release)
	clear_eas_release_cache()

	client = TestClient(app)
	response = client.get("/playspace/mobile-release-policy")

	assert response.status_code == 200
	body = response.json()
	assert body["product"] == "playspace"
	assert body["android"]["latest_version"] == "0.8.1"
	assert body["android"]["minimum_supported_version"] == "0.8.0"
	assert body["android"]["update_url"].startswith("https://play.google.com/")


def test_export_notify_ready_requires_manager_or_admin(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""The export completion-email endpoint is manager/admin only and best-effort.

	Email delivery is unconfigured in tests, so the underlying send is a no-op
	that never raises; the endpoint should still return 204 for authorized roles,
	reject unauthenticated callers, and validate the payload shape.
	"""

	del playspace_seed_snapshot  # Seeded accounts are loaded via the login helpers.

	payload = {
		"entity": "projects",
		"format": "xlsx",
		"audit_count": 42,
		"combined_report_count": 3,
		"had_failures": True,
	}

	admin_token = _login_admin(playspace_client)
	admin_response = playspace_client.post(
		"/playspace/exports/notify-ready",
		headers=_bearer_headers(admin_token),
		json=payload,
	)
	assert admin_response.status_code == 204

	manager_token = _login_manager(playspace_client)
	manager_response = playspace_client.post(
		"/playspace/exports/notify-ready",
		headers=_bearer_headers(manager_token),
		json={"entity": "audits", "format": "json", "audit_count": 1},
	)
	assert manager_response.status_code == 204

	# Unauthenticated callers are rejected before any work happens.
	unauth_response = playspace_client.post("/playspace/exports/notify-ready", json=payload)
	assert unauth_response.status_code in {401, 403}

	# An out-of-range enum value fails request validation.
	invalid_response = playspace_client.post(
		"/playspace/exports/notify-ready",
		headers=_bearer_headers(admin_token),
		json={"entity": "everything", "format": "xlsx", "audit_count": 1},
	)
	assert invalid_response.status_code == 422


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

	secondary_manager_email = f"secondary-manager-{_unique_suffix()}@example.org"
	create_manager_invite_response = playspace_client.post(
		"/playspace/manager-invites",
		headers=manager_auth_headers,
		json={"email": secondary_manager_email},
	)
	assert create_manager_invite_response.status_code == 201
	manager_invite_url = create_manager_invite_response.json()["invite_url"]
	manager_invite_token = manager_invite_url.rsplit("/", 1)[-1]

	accept_manager_invite_response = playspace_client.post(
		f"/playspace/auth/manager-invites/{manager_invite_token}/accept",
		json={"name": "Secondary Manager", "password": SEED_PASSWORD},
	)
	assert accept_manager_invite_response.status_code == 200
	assert accept_manager_invite_response.json()["user"]["account_type"] == "MANAGER"
	assert accept_manager_invite_response.json()["user"]["email"] == secondary_manager_email
	secondary_manager_token = accept_manager_invite_response.json()["access_token"]

	secondary_invite_attempt_response = playspace_client.post(
		"/playspace/manager-invites",
		headers=_bearer_headers(secondary_manager_token),
		json={"email": f"blocked-secondary-{_unique_suffix()}@example.org"},
	)
	assert secondary_invite_attempt_response.status_code == 403

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


def test_manager_invite_management_endpoints(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""Exercise the manager invite list, revoke, and resend endpoints.

	Covers:
	- Primary manager can list, create, resend, and revoke invites.
	- PENDING / ACCEPTED status is correctly derived.
	- Revoke returns 204; double-revoke returns 404.
	- Revoke and resend on an accepted invite return 400.
	- Secondary managers and auditors are denied access (403).
	"""

	manager_token = _login_manager(playspace_client)
	manager_auth = _bearer_headers(manager_token)
	suffix = _unique_suffix()

	# --- List: endpoint is accessible and returns a list ---
	list_initial_response = playspace_client.get(
		"/playspace/manager-invites",
		headers=manager_auth,
	)
	assert list_initial_response.status_code == 200
	assert isinstance(list_initial_response.json(), list)

	# --- Create an invite so we have something to manage ---
	invite_email = f"mgmt-invite-{suffix}@example.org"
	create_response = playspace_client.post(
		"/playspace/manager-invites",
		headers=manager_auth,
		json={"email": invite_email},
	)
	assert create_response.status_code == 201
	created = create_response.json()
	assert created["email"] == invite_email
	assert created["status"] == "PENDING"

	# --- List: the new invite appears with PENDING status ---
	list_response = playspace_client.get(
		"/playspace/manager-invites",
		headers=manager_auth,
	)
	assert list_response.status_code == 200
	invite_list = list_response.json()
	matching = [i for i in invite_list if i["email"] == invite_email]
	assert len(matching) == 1
	invite_id = matching[0]["id"]
	assert matching[0]["status"] == "PENDING"
	assert matching[0]["accepted_at"] is None

	# --- Resend: regenerates token without changing status ---
	resend_response = playspace_client.post(
		f"/playspace/manager-invites/{invite_id}/resend",
		headers=manager_auth,
	)
	assert resend_response.status_code == 200
	resent = resend_response.json()
	assert resent["id"] == invite_id
	assert resent["email"] == invite_email
	assert resent["status"] == "PENDING"

	# --- Revoke: deletes the invite and returns 204 ---
	revoke_response = playspace_client.delete(
		f"/playspace/manager-invites/{invite_id}",
		headers=manager_auth,
	)
	assert revoke_response.status_code == 204

	# --- List: invite is no longer present ---
	list_after_revoke = playspace_client.get(
		"/playspace/manager-invites",
		headers=manager_auth,
	)
	assert list_after_revoke.status_code == 200
	assert not any(i["id"] == invite_id for i in list_after_revoke.json())

	# --- Revoke again: returns 404 ---
	double_revoke_response = playspace_client.delete(
		f"/playspace/manager-invites/{invite_id}",
		headers=manager_auth,
	)
	assert double_revoke_response.status_code == 404

	# --- Accept an invite so we can test the accepted-state guards ---
	accept_email = f"mgmt-accept-{suffix}@example.org"
	create_for_accept = playspace_client.post(
		"/playspace/manager-invites",
		headers=manager_auth,
		json={"email": accept_email},
	)
	assert create_for_accept.status_code == 201
	invite_token = create_for_accept.json()["invite_url"].rsplit("/", 1)[-1]

	accept_response = playspace_client.post(
		f"/playspace/auth/manager-invites/{invite_token}/accept",
		json={"name": "Mgmt Test Manager", "password": SEED_PASSWORD},
	)
	assert accept_response.status_code == 200
	assert accept_response.json()["user"]["account_type"] == "MANAGER"

	# --- List: accepted invite has ACCEPTED status ---
	list_with_accepted = playspace_client.get(
		"/playspace/manager-invites",
		headers=manager_auth,
	)
	assert list_with_accepted.status_code == 200
	accepted_invite = next(
		(i for i in list_with_accepted.json() if i["email"] == accept_email),
		None,
	)
	assert accepted_invite is not None
	assert accepted_invite["status"] == "ACCEPTED"
	assert accepted_invite["accepted_at"] is not None
	accepted_invite_id = accepted_invite["id"]

	# --- Revoke accepted invite: 400 ---
	assert (
		playspace_client.delete(
			f"/playspace/manager-invites/{accepted_invite_id}",
			headers=manager_auth,
		).status_code
		== 400
	)

	# --- Resend accepted invite: 400 ---
	assert (
		playspace_client.post(
			f"/playspace/manager-invites/{accepted_invite_id}/resend",
			headers=manager_auth,
		).status_code
		== 400
	)

	# --- Role guard: secondary manager (just accepted) cannot use invite mgmt ---
	secondary_login = playspace_client.post(
		"/playspace/auth/login",
		json={"email": accept_email, "password": SEED_PASSWORD},
	)
	assert secondary_login.status_code == 200
	secondary_auth = _bearer_headers(secondary_login.json()["access_token"])

	assert playspace_client.get("/playspace/manager-invites", headers=secondary_auth).status_code == 403
	assert (
		playspace_client.delete(
			f"/playspace/manager-invites/{accepted_invite_id}",
			headers=secondary_auth,
		).status_code
		== 403
	)
	assert (
		playspace_client.post(
			f"/playspace/manager-invites/{accepted_invite_id}/resend",
			headers=secondary_auth,
		).status_code
		== 403
	)

	# --- Role guard: auditor cannot access invite management ---
	auditor_auth = _bearer_headers(_login_auditor(playspace_client, playspace_seed_snapshot.seeded_auditor_email))
	assert playspace_client.get("/playspace/manager-invites", headers=auditor_auth).status_code == 403
	assert (
		playspace_client.post(
			"/playspace/manager-invites",
			headers=auditor_auth,
			json={"email": f"auditor-invite-{suffix}@example.org"},
		).status_code
		== 403
	)


def test_manager_dashboard_endpoints(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
	playspace_test_session_factory: async_sessionmaker[AsyncSession],
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
	auditors_payload = auditors_response.json()
	assert len(auditors_payload) >= 1
	seeded_auditor = next(
		auditor for auditor in auditors_payload if auditor["id"] == playspace_seed_snapshot.seeded_auditor_profile_id
	)
	expected_assignments, expected_completed = asyncio.run(
		_load_direct_auditor_counts(
			playspace_test_session_factory,
			playspace_seed_snapshot.seeded_auditor_profile_id,
		)
	)
	assert seeded_auditor["assignments_count"] == expected_assignments
	assert seeded_auditor["completed_audits"] == expected_completed

	places_response = playspace_client.get(
		f"/playspace/accounts/{account_id}/places",
		headers=headers,
	)
	assert places_response.status_code == 200
	assert len(places_response.json()["items"]) >= 1
	assert "place_audit_status" in places_response.json()["items"][0]
	assert "overall_scores" in places_response.json()["items"][0]

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
	assert "audit_mean_scores" in place_history_response.json()


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

	instruments_response = playspace_client.get("/playspace/admin/instruments", headers=headers)
	assert instruments_response.status_code == 200
	active_instrument = next(item for item in instruments_response.json() if item["is_active"])

	active_delete_response = playspace_client.delete(
		f"/playspace/admin/instruments/{active_instrument['id']}",
		headers=headers,
	)
	assert active_delete_response.status_code == 409

	draft_payload = {
		"instrument_key": active_instrument["instrument_key"],
		"instrument_version": f"delete-test-{uuid.uuid4().hex[:8]}",
		"parent_instrument_id": active_instrument["id"],
		"content": active_instrument["content"],
	}
	create_draft_response = playspace_client.post(
		"/playspace/admin/instruments",
		params={"activate": "false"},
		json=draft_payload,
		headers=headers,
	)
	assert create_draft_response.status_code == 201
	created_draft = create_draft_response.json()
	assert created_draft["parent_instrument_id"] == active_instrument["id"]
	draft_id = created_draft["id"]

	delete_draft_response = playspace_client.delete(
		f"/playspace/admin/instruments/{draft_id}",
		headers=headers,
	)
	assert delete_draft_response.status_code == 204

	deleted_draft_response = playspace_client.delete(
		f"/playspace/admin/instruments/{draft_id}",
		headers=headers,
	)
	assert deleted_draft_response.status_code == 404


def test_admin_export_bundles(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""Admin project/place export bundles carry the full descendant hierarchy."""

	admin_token = _login_admin(playspace_client)
	headers = _bearer_headers(admin_token)
	project_id = playspace_seed_snapshot.urban_project_id
	place_id = playspace_seed_snapshot.riverside_place_id

	# ── Project bundle (Export selected → scoped to one project) ────────────────
	project_bundle_response = playspace_client.get(
		"/playspace/admin/export/projects/bundle",
		headers=headers,
		params={"project_id": project_id},
	)
	assert project_bundle_response.status_code == 200
	project_bundle = project_bundle_response.json()
	# B1 regression: the bundle includes descendant places/auditors/audits, not just
	# the parent project row with aggregate counts.
	assert len(project_bundle["projects"]) == 1
	assert project_bundle["projects"][0]["project_id"] == project_id
	assert len(project_bundle["places"]) >= 1
	assert len(project_bundle["audits"]) >= 1
	assert all(place["project_id"] == project_id for place in project_bundle["places"])
	assert all(audit["project_id"] == project_id for audit in project_bundle["audits"])

	# B2 regression: the project record exposes the split per-mode means and the
	# overall pair, matching the dashboard's overall_score_pair.
	project_record = project_bundle["projects"][0]
	assert "audit_mean_pv" in project_record
	assert "survey_mean_pv" in project_record
	assert "average_pv_score" in project_record

	# Privacy: admin auditor export is code-only - no email/PII columns.
	if project_bundle["auditors"]:
		auditor_record = project_bundle["auditors"][0]
		assert "auditor_code" in auditor_record
		assert "email" not in auditor_record
		assert "full_name" not in auditor_record

	# ── Audit export: raw audit exports include submitted audits only.
	audit_export_response = playspace_client.get(
		"/playspace/admin/export/audits",
		headers=headers,
		params={"status": "PAUSED"},
	)
	assert audit_export_response.status_code == 200
	audit_export_payload = audit_export_response.json()
	assert audit_export_payload["entity"] == "audits"
	assert len(audit_export_payload["records"]) >= 1
	assert all(record["status"] == "SUBMITTED" for record in audit_export_payload["records"])
	assert {
		"place_size",
		"current_users_0_5",
		"current_users_6_12",
		"current_users_13_17",
		"current_users_18_plus",
		"weather_conditions",
	} <= audit_export_payload["records"][0].keys()

	# ── Place bundle (Export selected → scoped to one place) ────────────────────
	place_bundle_response = playspace_client.get(
		"/playspace/admin/export/places/bundle",
		headers=headers,
		params={"place_id": place_id},
	)
	assert place_bundle_response.status_code == 200
	place_bundle = place_bundle_response.json()
	assert len(place_bundle["places"]) >= 1
	assert all(place["place_id"] == place_id for place in place_bundle["places"])
	# Project-description fields are joined onto the place rows.
	assert "project_overview" in place_bundle["places"][0]
	assert "project_name" in place_bundle["places"][0]
	# Submissions at the place are present.
	assert all(audit["place_id"] == place_id for audit in place_bundle["audits"])


def test_manager_export_bundles(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""Manager export bundles are account-scoped and include full auditor identity."""

	manager_token = _login_manager(playspace_client)
	headers = _bearer_headers(manager_token)
	account_id = playspace_seed_snapshot.manager_account_id
	project_id = playspace_seed_snapshot.urban_project_id

	# ── Scope: another account → 403 ────────────────────────────────────────────
	other_account_id = str(uuid.uuid4())
	cross_account_response = playspace_client.get(
		f"/playspace/accounts/{other_account_id}/export/projects/bundle",
		headers=headers,
	)
	assert cross_account_response.status_code == 403

	# ── Scope: admin token rejected on the manager surface ──────────────────────
	admin_headers = _bearer_headers(_login_admin(playspace_client))
	admin_on_manager_response = playspace_client.get(
		f"/playspace/accounts/{account_id}/export/projects/bundle",
		headers=admin_headers,
	)
	assert admin_on_manager_response.status_code == 403

	# ── Scope: auditor token rejected ───────────────────────────────────────────
	auditor_headers = _bearer_headers(_login_auditor(playspace_client, playspace_seed_snapshot.seeded_auditor_email))
	auditor_response = playspace_client.get(
		f"/playspace/accounts/{account_id}/export/projects/bundle",
		headers=auditor_headers,
	)
	assert auditor_response.status_code == 403

	# ── Depth: project bundle carries descendant places/auditors/audits ─────────
	project_bundle_response = playspace_client.get(
		f"/playspace/accounts/{account_id}/export/projects/bundle",
		headers=headers,
	)
	assert project_bundle_response.status_code == 200
	project_bundle = project_bundle_response.json()
	assert len(project_bundle["projects"]) >= 1
	assert len(project_bundle["places"]) >= 1
	assert len(project_bundle["audits"]) >= 1

	# Identity: auditor/audit records carry the full profile (manager requirement),
	# and account columns are absent (always the manager's own account).
	assert project_bundle["audits"][0]["auditor_full_name"] is not None
	assert "auditor_email" in project_bundle["audits"][0]
	if project_bundle["auditors"]:
		manager_auditor = project_bundle["auditors"][0]
		assert "full_name" in manager_auditor
		assert "email" in manager_auditor
		assert "account_id" not in manager_auditor

	# ── Audit export: raw audit exports include submitted audits only.
	audit_export_response = playspace_client.get(
		f"/playspace/accounts/{account_id}/export/audits",
		headers=headers,
		params={"status": "PAUSED"},
	)
	assert audit_export_response.status_code == 200
	audit_export_payload = audit_export_response.json()
	assert audit_export_payload["entity"] == "audits"
	assert len(audit_export_payload["records"]) >= 1
	assert all(record["status"] == "SUBMITTED" for record in audit_export_payload["records"])
	assert {
		"place_size",
		"current_users_0_5",
		"current_users_6_12",
		"current_users_13_17",
		"current_users_18_plus",
		"weather_conditions",
	} <= audit_export_payload["records"][0].keys()

	# ── Reports: SUBMITTED-only ─────────────────────────────────────────────────
	reports_response = playspace_client.get(
		f"/playspace/accounts/{account_id}/export/reports",
		headers=headers,
	)
	assert reports_response.status_code == 200
	reports_payload = reports_response.json()
	assert reports_payload["entity"] == "reports"
	assert all(record["status"] == "SUBMITTED" for record in reports_payload["records"])

	# ── Selection precedence: explicit project_id scopes the bundle ─────────────
	selected_bundle_response = playspace_client.get(
		f"/playspace/accounts/{account_id}/export/projects/bundle",
		headers=headers,
		params={"project_id": project_id},
	)
	assert selected_bundle_response.status_code == 200
	selected_bundle = selected_bundle_response.json()
	assert len(selected_bundle["projects"]) == 1
	assert selected_bundle["projects"][0]["project_id"] == project_id


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

	# Create a throwaway auditor profile for the update/delete coverage below.
	# Deleting unlinks the auditor (nulls the User + profile ``account_id``), so this
	# must never touch the shared seeded auditor that other tests authenticate as.
	create_profile_response = playspace_client.post(
		"/playspace/auditor-profiles",
		headers=headers,
		json={
			"email": f"crud.auditor.{suffix}@example.org",
			"full_name": f"CRUD Auditor {suffix}",
			"country": "New Zealand",
			"role": f"Field Auditor {suffix}",
		},
	)
	assert create_profile_response.status_code == 201, create_profile_response.text
	auditor_profile = {"id": create_profile_response.json()["id"]}

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
	created_auditor = assignment_response.json()
	auditor_profile_id = created_auditor["id"]
	temporary_password = created_auditor["temporary_password"]
	assert temporary_password
	assert temporary_password != SEED_PASSWORD

	seed_password_response = playspace_client.post(
		"/playspace/auth/login",
		json={"email": auditor_email, "password": SEED_PASSWORD},
	)
	assert seed_password_response.status_code == 401

	# Auditor account is created with a one-time random temporary password.
	auditor_token = _login_auditor(playspace_client, auditor_email, str(temporary_password))
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
	assert audit_session["execution_mode"] == "audit"
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
			"meta": {
				"execution_mode": "survey",
				"final_comments": "Surfaces were busiest near the swings at closing time.",
			},
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

	patch_aggregate_response = playspace_client.patch(
		f"/playspace/audits/{audit_id}/draft",
		headers=auditor_headers,
		json={
			"expected_revision": patch_draft_response.json()["revision"],
			"aggregate": {
				"schema_version": 1,
				"meta": {
					"execution_mode": "both",
					"final_comments": "Visibility was best from the north path after sunset.",
				},
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
	assert patch_aggregate_response.status_code == 200
	assert patch_aggregate_response.json()["audit_id"] == audit_id
	assert patch_aggregate_response.json()["revision"] == 3

	refreshed_audit_response = playspace_client.get(
		f"/playspace/audits/{audit_id}",
		headers=auditor_headers,
	)
	assert refreshed_audit_response.status_code == 200
	assert refreshed_audit_response.json()["revision"] == 3
	assert refreshed_audit_response.json()["aggregate"]["meta"]["execution_mode"] == "both"
	assert (
		refreshed_audit_response.json()["aggregate"]["meta"]["final_comments"]
		== "Visibility was best from the north path after sunset."
	)
	assert (
		refreshed_audit_response.json()["meta"]["final_comments"]
		== "Visibility was best from the north path after sunset."
	)

	submit_response = playspace_client.post(
		f"/playspace/audits/{audit_id}/submit",
		headers=auditor_headers,
	)
	assert submit_response.status_code == 400


def test_notify_submit_failure_endpoint(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""Exercise access-control and happy-path for POST /audits/{id}/notify-submit-failure.

	The email send function is patched so the test does not make real Brevo
	calls, but the full service path (auth, audit load, auditor guard) is
	exercised against the live test DB.
	"""

	suffix = _unique_suffix()
	manager_token = _login_manager(playspace_client)
	manager_headers = _bearer_headers(manager_token)

	# --- Create a fresh project / place / auditor and open an audit ---
	project = _create_project(playspace_client, manager_token, suffix=suffix)
	place = _create_place(playspace_client, manager_token, project_id=str(project["id"]), suffix=suffix)
	auditor_email = f"notify-fail-{suffix}@example.org"
	created_auditor = playspace_client.post(
		"/playspace/auditor-profiles",
		headers=manager_headers,
		json={
			"email": auditor_email,
			"full_name": f"Notify Fail Auditor {suffix}",
			"auditor_code": f"NF-{suffix.upper()}",
			"country": "New Zealand",
			"role": "Tester",
		},
	)
	assert created_auditor.status_code == 201
	auditor_profile_id = created_auditor.json()["id"]
	temporary_password = created_auditor.json()["temporary_password"]

	auditor_token = _login_auditor(playspace_client, auditor_email, str(temporary_password))
	auditor_headers = _bearer_headers(auditor_token)

	# Assign auditor to the place so they can open an audit.
	playspace_client.post(
		f"/playspace/auditor-profiles/{auditor_profile_id}/assignments",
		headers=manager_headers,
		json={"project_id": project["id"], "place_id": place["id"]},
	)

	access_response = playspace_client.post(
		f"/playspace/places/{place['id']}/audits/access",
		headers=auditor_headers,
		json={"project_id": project["id"]},
	)
	assert access_response.status_code == 200
	audit_id = access_response.json()["audit_id"]

	# --- Happy path: auditor calls the endpoint.
	# Brevo is not configured in the test environment, so send_audit_submit_failure_email
	# returns False silently - no network call, no exception raised.
	notify_response = playspace_client.post(
		f"/playspace/audits/{audit_id}/notify-submit-failure",
		headers=auditor_headers,
	)
	assert notify_response.status_code == 204
	assert notify_response.content == b""

	# --- Manager may NOT call this endpoint (auditor-only) ---
	manager_notify_response = playspace_client.post(
		f"/playspace/audits/{audit_id}/notify-submit-failure",
		headers=manager_headers,
	)
	assert manager_notify_response.status_code == 403

	# --- Unknown audit ID returns 404 ---
	missing_notify_response = playspace_client.post(
		f"/playspace/audits/{uuid.uuid4()}/notify-submit-failure",
		headers=auditor_headers,
	)
	assert missing_notify_response.status_code == 404

	# --- Unauthenticated request returns 401 or 403 ---
	unauthenticated_response = playspace_client.post(
		f"/playspace/audits/{audit_id}/notify-submit-failure",
	)
	assert unauthenticated_response.status_code in (401, 403)

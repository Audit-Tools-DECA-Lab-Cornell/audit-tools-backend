"""YEE manager workflow integration tests.

These cover manager authentication, public manager signup and the primary/
secondary organization rules, manager-invite management and limits, self-service
auditor profiles, and the protections that keep the seeded demo manager stable.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.auth as auth_module
from tests.products.yee._helpers import (
	SEED_MANAGER_EMAIL,
	SEED_PASSWORD,
	_bearer_headers,
	_create_legacy_manager_invite_for_existing_manager,
	_delete_user_by_email,
	_load_auditor_profile_by_user_email,
	_load_manager_profile_by_email,
	_load_manager_signup_snapshot,
	_login_auditor,
	_reconcile_demo_accounts,
	_signup_primary_manager,
)


def test_seeded_manager_can_login_to_manager_dashboard(yee_client: TestClient) -> None:
	"""The documented demo manager account authenticates as a manager."""

	response = yee_client.post("/yee/auth/login", json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD})
	assert response.status_code == 200, response.text
	assert response.json()["user"]["account_type"] == "MANAGER"
	assert response.json()["user"]["is_primary_manager"] is True
	assert response.json()["user"]["dashboard_path"] == "/dashboard"


def test_demo_manager_login_is_restored_if_user_row_drifts(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""Protected demo reconciliation recreates the manager auth row if it disappears."""

	asyncio.run(_delete_user_by_email(yee_test_session_factory, SEED_MANAGER_EMAIL))

	broken_login = yee_client.post("/yee/auth/login", json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD})
	assert broken_login.status_code == 401, broken_login.text

	summary = asyncio.run(_reconcile_demo_accounts(yee_test_session_factory))
	assert summary["users_created"] >= 1

	restored_login = yee_client.post("/yee/auth/login", json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD})
	assert restored_login.status_code == 200, restored_login.text
	assert restored_login.json()["user"]["account_type"] == "MANAGER"
	assert restored_login.json()["user"]["is_primary_manager"] is True


def test_manager_signup_creates_primary_manager_organization(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""Public YEE manager signup creates one real organization account immediately."""

	email = "manager.pending@example.org"
	before_user, before_count, _before_account_name = asyncio.run(
		_load_manager_signup_snapshot(yee_test_session_factory, email)
	)
	assert before_user is None

	response = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": email,
			"password": SEED_PASSWORD,
			"name": "Manager Pending",
			"organization": "Example YEE Partner Organization",
			"account_type": "MANAGER",
			"website": "",
		},
	)
	assert response.status_code == 201, response.text

	user, after_count, account_name = asyncio.run(_load_manager_signup_snapshot(yee_test_session_factory, email))
	assert user is not None
	assert user.account_type.value == "MANAGER"
	assert user.account_id is not None
	assert user.approved is True
	assert account_name == "Example YEE Partner Organization"
	assert after_count == before_count + 1
	assert user.profile_completed is False


def test_protected_demo_manager_signup_is_blocked(yee_client: TestClient) -> None:
	"""Public signup cannot overwrite the protected seeded demo manager account."""

	response = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": SEED_MANAGER_EMAIL,
			"password": "AnotherDemoPass123!",
			"name": "Override Demo Manager",
			"organization": "Override Demo Org",
			"account_type": "MANAGER",
			"website": "",
		},
	)
	assert response.status_code == 409, response.text
	assert response.json()["detail"] == "Protected demo accounts cannot be modified through public signup."

	login_response = yee_client.post(
		"/yee/auth/login",
		json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD},
	)
	assert login_response.status_code == 200, login_response.text


def test_demo_manager_seed_has_manager_profile(
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""The shared manager demo account should have a stable manager profile row."""

	profile = asyncio.run(_load_manager_profile_by_email(yee_test_session_factory, SEED_MANAGER_EMAIL))
	assert profile is not None
	assert profile.user_id is not None
	assert profile.is_primary is True


def test_primary_manager_profile_requires_phone_and_profession(yee_client: TestClient) -> None:
	"""Primary-manager onboarding enforces the richer profile requirements."""

	manager_login = yee_client.post("/yee/auth/login", json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD})
	assert manager_login.status_code == 200, manager_login.text
	manager_headers = _bearer_headers(manager_login.json()["access_token"])

	missing_phone = yee_client.put(
		"/yee/dashboard/manager-profile",
		headers=manager_headers,
		json={
			"full_name": "Dr. Farah Khan",
			"job_title": "Principal Investigator",
			"profession_disciplines": ["Public health"],
			"organization": "Youth Enabling Environments Collaborative",
			"phone_number": "",
		},
	)
	assert missing_phone.status_code == 400, missing_phone.text
	assert missing_phone.json()["detail"] == "Phone number is required for the primary manager."

	missing_profession = yee_client.put(
		"/yee/dashboard/manager-profile",
		headers=manager_headers,
		json={
			"full_name": "Dr. Farah Khan",
			"job_title": "Principal Investigator",
			"profession_disciplines": [],
			"organization": "Youth Enabling Environments Collaborative",
			"phone_number": "+1 607 555 0147",
		},
	)
	assert missing_profession.status_code == 400, missing_profession.text
	assert missing_profession.json()["detail"] == "Profession / discipline is required."


def test_secondary_manager_must_confirm_before_creating_new_organization(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""A secondary manager cannot create a new org without the explicit confirmation flag."""

	def _capture_manager_invite_email(**_: str | None) -> bool:
		return True

	monkeypatch.setattr(auth_module, "send_manager_invite_email", _capture_manager_invite_email)
	import app.dashboard_router as dashboard_router_module

	monkeypatch.setattr(dashboard_router_module, "send_manager_invite_email", _capture_manager_invite_email)

	manager_headers = _signup_primary_manager(yee_client, yee_test_session_factory)["headers"]

	create_response = yee_client.post(
		"/yee/dashboard/manager-invites",
		headers=manager_headers,
		json={"full_name": "Secondary Confirm", "email": "secondary-confirm@example.org"},
	)
	assert create_response.status_code == 201, create_response.text
	invite_token = create_response.json()["invite_url"].rsplit("/", 1)[-1]

	accept_response = yee_client.post(
		f"/yee/auth/manager-invites/{invite_token}/accept",
		json={"name": "Secondary Confirm", "password": SEED_PASSWORD},
	)
	assert accept_response.status_code == 200, accept_response.text

	signup_without_confirm = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": "secondary-confirm@example.org",
			"password": SEED_PASSWORD,
			"name": "Secondary Confirm",
			"organization": "Brand New Secondary Org",
			"account_type": "MANAGER",
			"website": "",
		},
	)
	assert signup_without_confirm.status_code == 409, signup_without_confirm.text
	assert (
		"Creating a new organization will remove you from that organization" in signup_without_confirm.json()["detail"]
	)


def test_secondary_manager_can_create_new_organization_after_confirmation(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""Confirmed secondary-manager signup unlinks the old org and creates a new primary org."""

	def _capture_manager_invite_email(**_: str | None) -> bool:
		return True

	monkeypatch.setattr(auth_module, "send_manager_invite_email", _capture_manager_invite_email)
	import app.dashboard_router as dashboard_router_module

	monkeypatch.setattr(dashboard_router_module, "send_manager_invite_email", _capture_manager_invite_email)

	manager_headers = _signup_primary_manager(yee_client, yee_test_session_factory)["headers"]

	email = "secondary-confirmed@example.org"
	create_response = yee_client.post(
		"/yee/dashboard/manager-invites",
		headers=manager_headers,
		json={"full_name": "Secondary Confirmed", "email": email},
	)
	assert create_response.status_code == 201, create_response.text
	invite_token = create_response.json()["invite_url"].rsplit("/", 1)[-1]

	accept_response = yee_client.post(
		f"/yee/auth/manager-invites/{invite_token}/accept",
		json={"name": "Secondary Confirmed", "password": SEED_PASSWORD},
	)
	assert accept_response.status_code == 200, accept_response.text

	signup_with_confirm = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": email,
			"password": SEED_PASSWORD,
			"name": "Secondary Confirmed",
			"organization": "Secondary Spinoff Org",
			"account_type": "MANAGER",
			"confirm_new_organization": True,
			"website": "",
		},
	)
	assert signup_with_confirm.status_code == 201, signup_with_confirm.text

	user, _count, account_name = asyncio.run(_load_manager_signup_snapshot(yee_test_session_factory, email))
	assert user is not None
	assert account_name == "Secondary Spinoff Org"
	assert user.profile_completed is False


def test_primary_manager_can_manage_secondary_manager_invites(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""Primary YEE managers can manage manager invites; secondary managers cannot."""

	sent_invites: list[dict[str, str | None]] = []

	def _capture_manager_invite_email(
		*,
		to_email: str,
		invite_url: str,
		organization_name: str | None = None,
		invited_by_name: str | None = None,
	) -> bool:
		sent_invites.append(
			{
				"to_email": to_email,
				"invite_url": invite_url,
				"organization_name": organization_name,
				"invited_by_name": invited_by_name,
			}
		)
		return True

	monkeypatch.setattr(auth_module, "send_manager_invite_email", _capture_manager_invite_email)
	import app.dashboard_router as dashboard_router_module

	monkeypatch.setattr(
		dashboard_router_module,
		"send_manager_invite_email",
		_capture_manager_invite_email,
	)

	manager = _signup_primary_manager(yee_client, yee_test_session_factory)
	manager_headers = manager["headers"]

	initial_list = yee_client.get("/yee/dashboard/manager-invites", headers=manager_headers)
	assert initial_list.status_code == 200, initial_list.text
	assert isinstance(initial_list.json(), list)

	invite_email = "secondary-manager-yee@example.org"
	create_response = yee_client.post(
		"/yee/dashboard/manager-invites",
		headers=manager_headers,
		json={"full_name": "Secondary Manager", "email": invite_email},
	)
	assert create_response.status_code == 201, create_response.text
	created = create_response.json()
	assert created["email"] == invite_email
	assert created["status"] == "PENDING"
	assert sent_invites[-1]["to_email"] == invite_email
	assert sent_invites[-1]["organization_name"] == manager["organization"]
	invite_token = created["invite_url"].rsplit("/", 1)[-1]

	list_response = yee_client.get("/yee/dashboard/manager-invites", headers=manager_headers)
	assert list_response.status_code == 200, list_response.text
	matching_invite = next((item for item in list_response.json() if item["email"] == invite_email), None)
	assert matching_invite is not None
	assert matching_invite["status"] == "PENDING"
	invite_id = matching_invite["id"]

	resend_response = yee_client.post(
		f"/yee/dashboard/manager-invites/{invite_id}/resend",
		headers=manager_headers,
	)
	assert resend_response.status_code == 200, resend_response.text
	assert resend_response.json()["id"] == invite_id
	assert resend_response.json()["status"] == "PENDING"

	# Resending rotates the invite token, so the auditor accepts with the link
	# from the most recent email rather than the original create-time token.
	latest_invite_url = sent_invites[-1]["invite_url"]
	assert latest_invite_url is not None
	invite_token = latest_invite_url.rsplit("/", 1)[-1]
	accept_response = yee_client.post(
		f"/yee/auth/manager-invites/{invite_token}/accept",
		json={"name": "Secondary Manager", "password": SEED_PASSWORD},
	)
	assert accept_response.status_code == 200, accept_response.text
	assert accept_response.json()["user"]["account_type"] == "MANAGER"
	assert accept_response.json()["user"]["email"] == invite_email
	assert accept_response.json()["user"]["is_primary_manager"] is False
	profile = asyncio.run(_load_manager_profile_by_email(yee_test_session_factory, invite_email))
	assert profile is not None
	assert profile.full_name == "Secondary Manager"

	accepted_list = yee_client.get("/yee/dashboard/manager-invites", headers=manager_headers)
	assert accepted_list.status_code == 200, accepted_list.text
	accepted_invite = next((item for item in accepted_list.json() if item["id"] == invite_id), None)
	assert accepted_invite is not None
	assert accepted_invite["status"] == "ACCEPTED"
	assert accepted_invite["accepted_at"] is not None

	secondary_login = yee_client.post(
		"/yee/auth/login",
		json={"email": invite_email, "password": SEED_PASSWORD},
	)
	assert secondary_login.status_code == 200, secondary_login.text
	assert secondary_login.json()["user"]["is_primary_manager"] is False
	assert secondary_login.json()["user"]["has_auditor_profile"] is False
	secondary_headers = _bearer_headers(secondary_login.json()["access_token"])
	assert yee_client.get("/yee/dashboard/manager-invites", headers=secondary_headers).status_code == 403
	secondary_manager_list = yee_client.get("/yee/dashboard/managers", headers=secondary_headers)
	assert secondary_manager_list.status_code == 200, secondary_manager_list.text
	assert len(secondary_manager_list.json()) >= 2

	auditor_headers = _bearer_headers(_login_auditor(yee_client))
	assert yee_client.get("/yee/dashboard/manager-invites", headers=auditor_headers).status_code == 403

	assert (
		yee_client.post(
			f"/yee/dashboard/manager-invites/{invite_id}/resend",
			headers=manager_headers,
		).status_code
		== 400
	)
	assert (
		yee_client.delete(
			f"/yee/dashboard/manager-invites/{invite_id}",
			headers=manager_headers,
		).status_code
		== 400
	)

	revoke_email = "revokable-secondary-yee@example.org"
	revoke_create = yee_client.post(
		"/yee/dashboard/manager-invites",
		headers=manager_headers,
		json={"full_name": "Revokable Secondary", "email": revoke_email},
	)
	assert revoke_create.status_code == 201, revoke_create.text
	revoke_id = revoke_create.json()["id"]

	revoke_response = yee_client.delete(
		f"/yee/dashboard/manager-invites/{revoke_id}",
		headers=manager_headers,
	)
	assert revoke_response.status_code == 204

	post_revoke_list = yee_client.get("/yee/dashboard/manager-invites", headers=manager_headers)
	assert post_revoke_list.status_code == 200, post_revoke_list.text
	assert all(item["id"] != revoke_id for item in post_revoke_list.json())


def test_primary_manager_invite_limit_is_five_secondary_managers(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""Primary managers cannot create more than five secondary-manager slots."""

	def _capture_manager_invite_email(**_: str | None) -> bool:
		return True

	monkeypatch.setattr(auth_module, "send_manager_invite_email", _capture_manager_invite_email)
	import app.dashboard_router as dashboard_router_module

	monkeypatch.setattr(dashboard_router_module, "send_manager_invite_email", _capture_manager_invite_email)

	manager_headers = _signup_primary_manager(yee_client, yee_test_session_factory)["headers"]

	# A fresh org starts with zero secondary slots, so five invites fill the cap.
	for index in range(5):
		response = yee_client.post(
			"/yee/dashboard/manager-invites",
			headers=manager_headers,
			json={
				"full_name": f"Extra Secondary {index}",
				"email": f"extra-secondary-{index}@example.org",
			},
		)
		assert response.status_code == 201, response.text

	overflow = yee_client.post(
		"/yee/dashboard/manager-invites",
		headers=manager_headers,
		json={"full_name": "Overflow Secondary", "email": "overflow-secondary@example.org"},
	)
	assert overflow.status_code == 409, overflow.text
	assert overflow.json()["detail"] == "A primary manager can invite up to 5 additional managers."


def test_manager_can_create_auditor_profile_for_self(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""Managers can create an auditor profile for themselves to use auditor routes."""

	manager_login = yee_client.post("/yee/auth/login", json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD})
	assert manager_login.status_code == 200, manager_login.text
	manager_headers = _bearer_headers(manager_login.json()["access_token"])

	create_response = yee_client.post("/yee/dashboard/my-auditor-profile", headers=manager_headers)
	assert create_response.status_code == 201, create_response.text
	assert create_response.json()["auditor_id"].startswith("AUD")

	auditor_profile = asyncio.run(_load_auditor_profile_by_user_email(yee_test_session_factory, SEED_MANAGER_EMAIL))
	assert auditor_profile is not None
	session_response = yee_client.get("/yee/auth/me", headers=manager_headers)
	assert session_response.status_code == 200, session_response.text
	assert session_response.json()["user"]["has_auditor_profile"] is True
	assert session_response.json()["user"]["auditor_dashboard_path"] == "/my-dashboard"
	my_audits = yee_client.get("/yee/my-audits", headers=manager_headers)
	assert my_audits.status_code == 200, my_audits.text


def test_primary_manager_can_remove_secondary_manager(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""Primary managers can remove a secondary manager from the organization."""

	def _capture_manager_invite_email(**_: str | None) -> bool:
		return True

	monkeypatch.setattr(auth_module, "send_manager_invite_email", _capture_manager_invite_email)
	import app.dashboard_router as dashboard_router_module

	monkeypatch.setattr(dashboard_router_module, "send_manager_invite_email", _capture_manager_invite_email)

	manager_headers = _signup_primary_manager(yee_client, yee_test_session_factory)["headers"]

	email = "removable-secondary@example.org"
	create_response = yee_client.post(
		"/yee/dashboard/manager-invites",
		headers=manager_headers,
		json={"full_name": "Removable Secondary", "email": email},
	)
	assert create_response.status_code == 201, create_response.text
	invite_token = create_response.json()["invite_url"].rsplit("/", 1)[-1]

	accept_response = yee_client.post(
		f"/yee/auth/manager-invites/{invite_token}/accept",
		json={"name": "Removable Secondary", "password": SEED_PASSWORD},
	)
	assert accept_response.status_code == 200, accept_response.text

	profile = asyncio.run(_load_manager_profile_by_email(yee_test_session_factory, email))
	assert profile is not None

	remove_response = yee_client.delete(f"/yee/dashboard/managers/{profile.id}", headers=manager_headers)
	assert remove_response.status_code == 204, remove_response.text
	assert asyncio.run(_load_manager_profile_by_email(yee_test_session_factory, email)) is None


def test_existing_manager_invite_acceptance_cannot_overwrite_existing_manager_password(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""A stray legacy invite cannot hijack an existing manager's credentials."""

	legacy_token = "legacy-manager-demo-token"
	asyncio.run(
		_create_legacy_manager_invite_for_existing_manager(
			yee_test_session_factory,
			email=SEED_MANAGER_EMAIL,
			account_id="11111111-1111-4111-8111-111111111111",
			invited_by_user_id="dddddddd-dddd-4ddd-8ddd-ddddddddddd1",
			token=legacy_token,
		)
	)

	accept_response = yee_client.post(
		f"/yee/auth/manager-invites/{legacy_token}/accept",
		json={"name": "Manager Demo", "password": "DifferentPass123!"},
	)
	assert accept_response.status_code == 409, accept_response.text
	assert accept_response.json()["detail"] == "Protected demo accounts cannot be repurposed through manager invites."

	login = yee_client.post("/yee/auth/login", json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD})
	assert login.status_code == 200, login.text

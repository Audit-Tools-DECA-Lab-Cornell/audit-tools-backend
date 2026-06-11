"""YEE endpoint integration tests.

These verify the YEE routes work against the per-product YEE schema produced by
the `yee` Alembic branch (shared core tables + `yee_audit_submissions`, and no
Playspace tables). They exercise the seeded auditor flow end to end:
instrument → audit-state → draft → submit → list → fetch.
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.auth as auth_module
from app.models import Account, Instrument, User
from app.seed import YEE_PLACE_COMMONS_ID, YEE_PLACE_PLAZA_ID, _build_yee_entities

# Matches the deterministic YEE seed (see app/seed.py).
SEED_AUDITOR_EMAIL = "auditor-demo-1@yee.local"
SEED_MANAGER_EMAIL = "manager-demo@yee.local"
SEED_AUDITOR_THREE_EMAIL = "auditor-demo-3@yee.local"
SEED_PASSWORD = "DemoPass123!"


def _bearer_headers(access_token: str) -> dict[str, str]:
	"""Build bearer auth headers for session-backed authorization."""

	return {"Authorization": f"bearer {access_token}"}


def _login_auditor(client: TestClient, email: str = SEED_AUDITOR_EMAIL, password: str = SEED_PASSWORD) -> str:
	"""Login a seeded YEE auditor account and return a bearer token."""

	response = client.post("/yee/auth/login", json={"email": email, "password": password})
	assert response.status_code == 200, response.text
	return response.json()["access_token"]


async def _load_manager_signup_snapshot(
	session_factory: async_sessionmaker[AsyncSession],
	email: str,
) -> tuple[User | None, int, str | None]:
	"""Return the signed-up user, current account count, and linked account name."""

	async with session_factory() as session:
		user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
		account_count = int((await session.execute(select(func.count(Account.id)))).scalar_one() or 0)
		account_name = None
		if user is not None and user.account_id is not None:
			account = await session.get(Account, user.account_id)
			account_name = account.name if account is not None else None
	return user, account_count, account_name


def test_yee_status_is_isolated(yee_client: TestClient) -> None:
	"""The YEE namespace status stub responds without touching Playspace."""

	response = yee_client.get("/yee/status")
	assert response.status_code == 200, response.text
	assert response.json()["product"] == "yee"


def test_yee_instrument_available(yee_client: TestClient) -> None:
	"""The instrument endpoint returns scoring metadata (no DB dependency)."""

	response = yee_client.get("/yee/instrument")
	assert response.status_code == 200, response.text
	assert isinstance(response.json(), dict)


def test_seeded_auditor_can_login(yee_client: TestClient) -> None:
	"""A seeded YEE auditor authenticates against the rebuilt YEE schema."""

	token = _login_auditor(yee_client)
	assert token


def test_seeded_manager_can_login_to_manager_dashboard(yee_client: TestClient) -> None:
	"""The documented demo manager account authenticates as a manager."""

	response = yee_client.post("/yee/auth/login", json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD})
	assert response.status_code == 200, response.text
	assert response.json()["user"]["account_type"] == "MANAGER"
	assert response.json()["user"]["is_primary_manager"] is True
	assert response.json()["user"]["dashboard_path"] == "/dashboard"


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


def test_audit_state_starts_not_started(yee_client: TestClient) -> None:
	"""An assigned-but-unstarted place reports NOT_STARTED."""

	token = _login_auditor(yee_client)
	response = yee_client.get(
		f"/yee/places/{YEE_PLACE_PLAZA_ID}/audit-state",
		headers=_bearer_headers(token),
	)
	assert response.status_code == 200, response.text
	assert response.json()["status"] == "NOT_STARTED"


def test_seeded_in_progress_audit_reports_draft_state(yee_client: TestClient) -> None:
	"""Seeded in-progress audits remain resumable even with fallback seed keys."""

	token = _login_auditor(yee_client, email=SEED_AUDITOR_THREE_EMAIL)
	response = yee_client.get(
		f"/yee/places/{YEE_PLACE_COMMONS_ID}/audit-state",
		headers=_bearer_headers(token),
	)
	assert response.status_code == 200, response.text
	assert response.json()["status"] == "DRAFT"
	assert response.json()["audit_id"] is not None


def test_seeded_in_progress_audit_can_be_saved_again(yee_client: TestClient) -> None:
	"""Auditor 3 can update the seeded Commons draft without tripping the save path."""

	token = _login_auditor(yee_client, email=SEED_AUDITOR_THREE_EMAIL)
	response = yee_client.put(
		f"/yee/places/{YEE_PLACE_COMMONS_ID}/draft",
		headers=_bearer_headers(token),
		json={
			"participant_info": {"total_minutes": 24},
			"responses": {
				"QID22": "3",
				"QID24": "1",
			},
		},
	)
	assert response.status_code == 200, response.text
	assert response.json()["status"] == "DRAFT"
	assert response.json()["audit_id"] is not None
	assert response.json()["participant_info"]["total_minutes"] == 24
	assert response.json()["responses"]["QID22"] == "3"


def test_password_reset_flow_updates_password(yee_client: TestClient, monkeypatch) -> None:
	"""A verified YEE user can request a reset link and log in with the new password."""

	captured_reset_url: dict[str, str] = {}

	def _capture_reset_email(*, to_email: str, reset_url: str) -> bool:
		captured_reset_url["to_email"] = to_email
		captured_reset_url["reset_url"] = reset_url
		return True

	monkeypatch.setattr(auth_module, "send_password_reset_email", _capture_reset_email)

	forgot = yee_client.post(
		"/yee/auth/forgot-password",
		json={"email": SEED_MANAGER_EMAIL, "website": ""},
		headers={"X-Frontend-Origin": "http://localhost:3000"},
	)
	assert forgot.status_code == 200, forgot.text
	assert captured_reset_url["to_email"] == SEED_MANAGER_EMAIL

	token = parse_qs(urlparse(captured_reset_url["reset_url"]).query)["token"][0]
	new_password = "EvenBetterPass123!"
	reset = yee_client.post(
		"/yee/auth/reset-password",
		json={"token": token, "password": new_password, "website": ""},
	)
	assert reset.status_code == 200, reset.text

	login = yee_client.post("/yee/auth/login", json={"email": SEED_MANAGER_EMAIL, "password": new_password})
	assert login.status_code == 200, login.text
	assert login.json()["user"]["account_type"] == "MANAGER"


def test_primary_manager_can_manage_secondary_manager_invites(
	yee_client: TestClient,
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

	manager_login = yee_client.post(
		"/yee/auth/login",
		json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD},
	)
	assert manager_login.status_code == 200, manager_login.text
	manager_headers = _bearer_headers(manager_login.json()["access_token"])

	initial_list = yee_client.get("/yee/dashboard/manager-invites", headers=manager_headers)
	assert initial_list.status_code == 200, initial_list.text
	assert isinstance(initial_list.json(), list)

	invite_email = "secondary-manager-yee@example.org"
	create_response = yee_client.post(
		"/yee/dashboard/manager-invites",
		headers=manager_headers,
		json={"email": invite_email},
	)
	assert create_response.status_code == 201, create_response.text
	created = create_response.json()
	assert created["email"] == invite_email
	assert created["status"] == "PENDING"
	assert sent_invites[-1]["to_email"] == invite_email
	assert sent_invites[-1]["organization_name"] == "Youth Enabling Environments Collaborative"
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

	accept_response = yee_client.post(
		f"/yee/auth/manager-invites/{invite_token}/accept",
		json={"name": "Secondary Manager", "password": SEED_PASSWORD},
	)
	assert accept_response.status_code == 200, accept_response.text
	assert accept_response.json()["user"]["account_type"] == "MANAGER"
	assert accept_response.json()["user"]["email"] == invite_email
	assert accept_response.json()["user"]["is_primary_manager"] is False

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
	secondary_headers = _bearer_headers(secondary_login.json()["access_token"])
	assert yee_client.get("/yee/dashboard/manager-invites", headers=secondary_headers).status_code == 403

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
		json={"email": revoke_email},
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


def test_yee_draft_submit_flow_uses_yee_audit_submissions(yee_client: TestClient) -> None:
	"""Full flow: save a draft, submit, then read it back via list + detail.

	This is the regression guard for the previously-missing
	``yee_audit_submissions`` table: submit writes one row and the list/detail
	endpoints read it back.
	"""

	token = _login_auditor(yee_client)
	headers = _bearer_headers(token)
	place_path = f"/yee/places/{YEE_PLACE_PLAZA_ID}"
	responses_payload = {"QID22": "3"}

	# Save a backend draft (creates an Audit row with instrument_key="yee").
	draft = yee_client.put(
		f"{place_path}/draft",
		headers=headers,
		json={"participant_info": {"total_minutes": 12}, "responses": responses_payload},
	)
	assert draft.status_code == 200, draft.text
	assert draft.json()["status"] == "DRAFT"

	# Submit the audit (creates exactly one yee_audit_submissions row).
	submit = yee_client.post(
		"/yee/audits",
		headers=headers,
		json={
			"place_id": str(YEE_PLACE_PLAZA_ID),
			"participant_info": {"total_minutes": 12},
			"responses": responses_payload,
		},
	)
	assert submit.status_code == 201, submit.text
	submission_id = submit.json()["id"]

	# A second submit for the same place is rejected.
	duplicate = yee_client.post(
		"/yee/audits",
		headers=headers,
		json={"place_id": str(YEE_PLACE_PLAZA_ID), "responses": responses_payload},
	)
	assert duplicate.status_code == 409, duplicate.text

	# The submission appears in the auditor's list.
	listing = yee_client.get("/yee/my-audits", headers=headers)
	assert listing.status_code == 200, listing.text
	assert any(item["id"] == submission_id for item in listing.json())

	# And is fetchable by id.
	detail = yee_client.get(f"/yee/audits/{submission_id}", headers=headers)
	assert detail.status_code == 200, detail.text
	assert detail.json()["place_id"] == str(YEE_PLACE_PLAZA_ID)

	# audit-state now reports the submitted record.
	state = yee_client.get(f"{place_path}/audit-state", headers=headers)
	assert state.status_code == 200, state.text
	assert state.json()["status"] == "SUBMITTED"


def test_build_yee_entities_instrument_is_active_root_version() -> None:
	"""The seeded YEE instrument is the active root of version history."""

	entities = _build_yee_entities()
	instruments = [entity for entity in entities if isinstance(entity, Instrument)]

	assert len(instruments) >= 1
	assert any(instrument.is_active is True and instrument.parent_instrument_id is None for instrument in instruments)

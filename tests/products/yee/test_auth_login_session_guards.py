"""Login negatives, /me session checks, and representative route guards.

Coverage targets:
  FLOW A — login negatives (wrong password, unknown email, unverified email,
           honeypot, unapproved user behaviour)
  FLOW F — /me with valid/missing/garbage token; logout (skipped — no route);
           representative route guards (auditor blocked from manager route,
           unauthenticated blocked)

Reuses conftest ``yee_client`` + ``yee_test_session_factory`` and all
``_helpers`` utilities.  Each test that creates data uses ``_unique_suffix``
to keep the session-scoped database clean.
"""

from __future__ import annotations

import asyncio

from tests.products.yee._helpers import (
	SEED_MANAGER_EMAIL,
	SEED_PASSWORD,
	_bearer_headers,
	_login_auditor,
	_unique_suffix,
	_verify_user_email,
)


# ---------------------------------------------------------------------------
# FLOW A — login negatives
# ---------------------------------------------------------------------------


def test_login_wrong_password_returns_401(yee_client):
	"""POST /yee/auth/login with a valid seeded email but wrong password -> 401."""
	resp = yee_client.post(
		"/yee/auth/login",
		json={"email": SEED_MANAGER_EMAIL, "password": "TotallyWrongPassword99!"},
	)
	assert resp.status_code == 401
	assert resp.json()["detail"] == "Invalid email or password."


def test_login_unknown_email_returns_401(yee_client):
	"""POST /yee/auth/login with an email that does not exist -> 401."""
	suffix = _unique_suffix()
	resp = yee_client.post(
		"/yee/auth/login",
		json={"email": f"nonexistent-{suffix}@nowhere.test", "password": SEED_PASSWORD},
	)
	assert resp.status_code == 401
	assert resp.json()["detail"] == "Invalid email or password."


def test_login_unverified_email_returns_403(yee_client, yee_test_session_factory, monkeypatch):
	"""A freshly signed-up (unverified) user cannot log in -> 403.

	Signs up a manager (which creates an unverified user), then attempts login
	without calling _verify_user_email.
	"""
	# Suppress the real verification email send so signup doesn't fail on SMTP.
	sent_emails: list = []
	monkeypatch.setattr(
		"app.auth.send_verification_email",
		lambda **kwargs: sent_emails.append(kwargs),
	)

	suffix = _unique_suffix()
	email = f"unverified-{suffix}@example.org"
	signup_resp = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": email,
			"password": SEED_PASSWORD,
			"name": f"Unverified User {suffix}",
			"organization": f"Org {suffix}",
			"account_type": "MANAGER",
			"website": "",
		},
	)
	assert signup_resp.status_code == 201, signup_resp.text

	# Attempt login without verifying email.
	login_resp = yee_client.post(
		"/yee/auth/login",
		json={"email": email, "password": SEED_PASSWORD},
	)
	assert login_resp.status_code == 403
	assert login_resp.json()["detail"] == "Email is not verified."


def test_login_honeypot_returns_400(yee_client):
	"""POST /yee/auth/login with the ``website`` honeypot field set -> 400."""
	resp = yee_client.post(
		"/yee/auth/login",
		json={
			"email": SEED_MANAGER_EMAIL,
			"password": SEED_PASSWORD,
			"website": "x",
		},
	)
	assert resp.status_code == 400
	assert resp.json()["detail"] == "Spam check failed."


def test_login_unapproved_user_gets_token_with_waiting_approval(
	yee_client,
	yee_test_session_factory,
	monkeypatch,
):
	"""An unapproved user with verified email can still log in (200).

	The login route does NOT gate on ``approved``.  Instead the ``next_step``
	field signals ``WAITING_APPROVAL`` and ``approved`` is False, which the
	client uses to render the waiting screen.

	To create this state we sign up a manager (approved=True by default for
	managers), verify the email, then flip ``approved`` to False in the DB
	before logging in.
	"""
	from sqlalchemy import select
	from app.models import User

	sent_emails: list = []
	monkeypatch.setattr(
		"app.auth.send_verification_email",
		lambda **kwargs: sent_emails.append(kwargs),
	)

	suffix = _unique_suffix()
	email = f"unapproved-{suffix}@example.org"
	signup_resp = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": email,
			"password": SEED_PASSWORD,
			"name": f"Unapproved {suffix}",
			"organization": f"Org {suffix}",
			"account_type": "MANAGER",
			"website": "",
		},
	)
	assert signup_resp.status_code == 201, signup_resp.text

	# Verify email, then mark user as unapproved.
	asyncio.run(_verify_user_email(yee_test_session_factory, email))

	async def _set_unapproved(sf, email):
		async with sf() as session:
			user = (await session.execute(select(User).where(User.email == email))).scalar_one()
			user.approved = False
			user.approved_at = None
			await session.commit()

	asyncio.run(_set_unapproved(yee_test_session_factory, email))

	# Login should succeed — the route does not gate on approved.
	login_resp = yee_client.post(
		"/yee/auth/login",
		json={"email": email, "password": SEED_PASSWORD},
	)
	assert login_resp.status_code == 200, login_resp.text
	body = login_resp.json()
	assert body["user"]["approved"] is False
	assert body["user"]["next_step"] == "WAITING_APPROVAL"
	assert "access_token" in body


# ---------------------------------------------------------------------------
# FLOW F — /me session checks
# ---------------------------------------------------------------------------


def test_me_valid_token_returns_200_with_user_shape(yee_client):
	"""GET /yee/auth/me with a valid bearer token returns 200 + expected shape."""
	token = _login_auditor(yee_client)
	resp = yee_client.get("/yee/auth/me", headers=_bearer_headers(token))
	assert resp.status_code == 200

	body = resp.json()
	assert "user" in body
	user = body["user"]

	# AuthUser required fields.
	assert "id" in user
	assert "email" in user
	assert "account_type" in user
	# account_type is returned uppercase (enum value).
	assert user["account_type"] == user["account_type"].upper()
	assert user["account_type"] in {"ADMIN", "MANAGER", "AUDITOR"}
	assert "email_verified" in user
	assert "approved" in user
	assert "profile_completed" in user
	assert "next_step" in user
	assert "dashboard_path" in user
	assert "is_primary_manager" in user
	assert "has_auditor_profile" in user


def test_me_no_authorization_header_returns_401(yee_client):
	"""GET /yee/auth/me with no Authorization header -> 401."""
	resp = yee_client.get("/yee/auth/me")
	assert resp.status_code == 401
	assert resp.json()["detail"] == "Authentication required."


def test_me_garbage_bearer_token_returns_401(yee_client):
	"""GET /yee/auth/me with a malformed bearer token -> 401."""
	resp = yee_client.get(
		"/yee/auth/me",
		headers={"Authorization": "bearer totally-not-a-valid-jwt"},
	)
	assert resp.status_code == 401
	# The detail could be "Invalid or expired access token." or
	# "Invalid access token payload." depending on the decode path.
	assert "access token" in resp.json()["detail"].lower() or "authentication" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# FLOW F — logout (SKIPPED — no /yee/auth/logout route exists)
# ---------------------------------------------------------------------------
# The backend has NO logout endpoint.  Logout is handled client-side by
# clearing the stored token (cookie / secure-store).  No test written.


# ---------------------------------------------------------------------------
# FLOW F — representative route guards
# ---------------------------------------------------------------------------


def test_auditor_token_on_manager_route_returns_403(yee_client):
	"""An AUDITOR hitting GET /yee/dashboard/projects -> 403.

	The route uses ``get_current_user`` (bearer auth) then
	``_require_manager_or_admin`` which rejects AUDITOR with 403.
	"""
	auditor_token = _login_auditor(yee_client)
	resp = yee_client.get(
		"/yee/dashboard/projects",
		headers=_bearer_headers(auditor_token),
	)
	assert resp.status_code == 403
	assert resp.json()["detail"] == "Manager or admin access is required."


def test_no_token_on_manager_route_returns_401(yee_client):
	"""GET /yee/dashboard/projects with no Authorization header -> 401.

	``get_current_user`` fires before the role check, so the response is 401.
	"""
	resp = yee_client.get("/yee/dashboard/projects")
	assert resp.status_code == 401
	assert resp.json()["detail"] == "Authentication required."


def test_manager_token_on_manager_route_succeeds(yee_client):
	"""A seeded MANAGER can access GET /yee/dashboard/projects -> 200.

	Positive control to confirm the guard allows managers through.
	"""
	login_resp = yee_client.post(
		"/yee/auth/login",
		json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD},
	)
	assert login_resp.status_code == 200
	token = login_resp.json()["access_token"]

	resp = yee_client.get(
		"/yee/dashboard/projects",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 200

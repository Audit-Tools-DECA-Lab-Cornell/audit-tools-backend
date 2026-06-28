"""YEE auth verification, signup edge-cases, and manager complete-profile tests.

Covers:
- FLOW B: signup edge cases (duplicate email, missing fields, honeypot, auditor
  self-signup behaviour, password validation).
- FLOW C: email verification and resend-verification (zero prior coverage).
- FLOW D: manager complete-profile happy path (not duplicating the validation
  negatives already in test_manager_workflows.py).
"""

from __future__ import annotations

import asyncio
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.auth as auth_module
from tests.products.yee._helpers import (
	SEED_MANAGER_EMAIL,
	SEED_PASSWORD,
	_bearer_headers,
	_unique_suffix,
	_verify_user_email,
)


# ---------------------------------------------------------------------------
# FLOW B -- signup edge cases
# ---------------------------------------------------------------------------


def test_duplicate_verified_email_signup_returns_409(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""A second signup attempt for a verified non-demo email returns 409."""

	sent: list[dict[str, str]] = []

	def _capture_verification_email(*, to_email: str, verify_url: str) -> bool:
		sent.append({"to_email": to_email, "verify_url": verify_url})
		return True

	monkeypatch.setattr(auth_module, "send_verification_email", _capture_verification_email)

	suffix = _unique_suffix()
	email = f"dupverif-{suffix}@example.org"

	# Create and verify user
	first = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": email,
			"password": SEED_PASSWORD,
			"name": f"Dup Verified {suffix}",
			"organization": f"Dup Verified Org {suffix}",
			"account_type": "MANAGER",
			"website": "",
		},
	)
	assert first.status_code == 201, first.text
	asyncio.run(_verify_user_email(yee_test_session_factory, email))

	# Second signup as a primary manager who already leads an org -> 409
	dup = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": email,
			"password": SEED_PASSWORD,
			"name": f"Dup Verified {suffix}",
			"organization": f"Another Org {suffix}",
			"account_type": "MANAGER",
			"website": "",
		},
	)
	assert dup.status_code == 409, dup.text
	assert "already leads" in dup.json()["detail"].lower() or "already exists" in dup.json()["detail"].lower()


def test_signup_missing_required_fields_returns_422(yee_client: TestClient) -> None:
	"""Omitting the required `email` or `password` fields yields a 422 from Pydantic."""

	# Missing email entirely
	no_email = yee_client.post(
		"/yee/auth/signup",
		json={
			"password": SEED_PASSWORD,
			"name": "No Email User",
			"organization": "No Email Org",
			"account_type": "MANAGER",
		},
	)
	assert no_email.status_code == 422, no_email.text

	# Missing password entirely
	no_password = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": "nopw@example.org",
			"name": "No Password User",
			"organization": "No Password Org",
			"account_type": "MANAGER",
		},
	)
	assert no_password.status_code == 422, no_password.text


def test_signup_password_too_short_returns_422(yee_client: TestClient) -> None:
	"""Password shorter than 8 characters is rejected by Pydantic min_length."""

	resp = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": "shortpw@example.org",
			"password": "Ab1!",
			"name": "Short PW",
			"organization": "Short PW Org",
			"account_type": "MANAGER",
			"website": "",
		},
	)
	# Pydantic min_length=8 triggers 422 before the route body runs
	assert resp.status_code == 422, resp.text


def test_signup_honeypot_website_field_returns_400(
	yee_client: TestClient,
	monkeypatch,
) -> None:
	"""Setting the honeypot `website` field on signup yields 400 'Spam check failed.'"""

	sent: list[dict[str, str]] = []

	def _capture_verification_email(*, to_email: str, verify_url: str) -> bool:
		sent.append({"to_email": to_email, "verify_url": verify_url})
		return True

	monkeypatch.setattr(auth_module, "send_verification_email", _capture_verification_email)

	suffix = _unique_suffix()
	resp = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": f"honeypot-{suffix}@example.org",
			"password": SEED_PASSWORD,
			"name": "Bot User",
			"organization": "Bot Org",
			"account_type": "MANAGER",
			"website": "http://spam.example.com",
		},
	)
	assert resp.status_code == 400, resp.text
	assert resp.json()["detail"] == "Spam check failed."
	# No verification email should have been sent
	assert sent == []


def test_signup_manager_without_organization_returns_400(
	yee_client: TestClient,
	monkeypatch,
) -> None:
	"""Manager signup requires an organization name; omitting it yields 400."""

	sent: list[dict[str, str]] = []

	def _capture_verification_email(*, to_email: str, verify_url: str) -> bool:
		sent.append({"to_email": to_email, "verify_url": verify_url})
		return True

	monkeypatch.setattr(auth_module, "send_verification_email", _capture_verification_email)

	suffix = _unique_suffix()
	resp = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": f"noorg-{suffix}@example.org",
			"password": SEED_PASSWORD,
			"name": "No Org Manager",
			"account_type": "MANAGER",
			"website": "",
		},
	)
	assert resp.status_code == 400, resp.text
	assert resp.json()["detail"] == "Organization name is required for manager signup."


def test_signup_admin_account_type_returns_403(yee_client: TestClient) -> None:
	"""Admin accounts cannot be created through public signup."""

	suffix = _unique_suffix()
	resp = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": f"admin-{suffix}@example.org",
			"password": SEED_PASSWORD,
			"name": "Wannabe Admin",
			"organization": "Admin Org",
			"account_type": "ADMIN",
			"website": "",
		},
	)
	assert resp.status_code == 403, resp.text
	assert resp.json()["detail"] == "Admin accounts cannot be created through public signup."


def test_auditor_self_signup_creates_unapproved_user(
	yee_client: TestClient,
	monkeypatch,
) -> None:
	"""YEE signup with account_type=AUDITOR creates an unapproved user with no account link.

	The YEE path does not block AUDITOR signups outright (unlike Playspace which
	returns 403). Instead it creates an unverified, unapproved user with
	account_id=None. The user cannot log in until verified and approved.
	"""

	sent: list[dict[str, str]] = []

	def _capture_verification_email(*, to_email: str, verify_url: str) -> bool:
		sent.append({"to_email": to_email, "verify_url": verify_url})
		return True

	monkeypatch.setattr(auth_module, "send_verification_email", _capture_verification_email)

	suffix = _unique_suffix()
	email = f"auditor-self-{suffix}@example.org"
	resp = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": email,
			"password": SEED_PASSWORD,
			"name": f"Auditor Self {suffix}",
			"account_type": "AUDITOR",
			"website": "",
		},
	)
	# The YEE path creates the user and sends verification email -> 201
	assert resp.status_code == 201, resp.text
	assert resp.json()["next_step"] == "VERIFY_EMAIL"
	assert resp.json()["email_verification_required"] is True
	# A verification email was sent
	assert len(sent) == 1
	assert sent[0]["to_email"] == email


# ---------------------------------------------------------------------------
# FLOW C -- email verification + resend-verification
# ---------------------------------------------------------------------------


def test_verify_email_with_captured_token(
	yee_client: TestClient,
	monkeypatch,
) -> None:
	"""Sign up a fresh manager, capture the verification token, verify -> 200."""

	sent: list[dict[str, str]] = []

	def _capture_verification_email(*, to_email: str, verify_url: str) -> bool:
		sent.append({"to_email": to_email, "verify_url": verify_url})
		return True

	monkeypatch.setattr(auth_module, "send_verification_email", _capture_verification_email)

	suffix = _unique_suffix()
	email = f"verify-{suffix}@example.org"

	signup = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": email,
			"password": SEED_PASSWORD,
			"name": f"Verify User {suffix}",
			"organization": f"Verify Org {suffix}",
			"account_type": "MANAGER",
			"website": "",
		},
	)
	assert signup.status_code == 201, signup.text
	assert len(sent) == 1

	# Extract token from the captured verify_url
	verify_url = sent[0]["verify_url"]
	token = parse_qs(urlparse(verify_url).query)["token"][0]

	# Verify the email via GET /yee/auth/verify-email?token=<token>
	verify = yee_client.get(f"/yee/auth/verify-email?token={token}")
	assert verify.status_code == 200, verify.text
	assert verify.json()["message"] == "Email verified successfully."

	# After verification, login should succeed (email_verified=True now)
	login = yee_client.post("/yee/auth/login", json={"email": email, "password": SEED_PASSWORD})
	assert login.status_code == 200, login.text
	assert login.json()["user"]["email_verified"] is True
	assert login.json()["user"]["account_type"] == "MANAGER"
	# next_step should advance past VERIFY_EMAIL
	assert login.json()["user"]["next_step"] != "VERIFY_EMAIL"


def test_verify_email_idempotent_for_already_verified(
	yee_client: TestClient,
	monkeypatch,
) -> None:
	"""Verifying with the same valid token twice returns 200 'Email already verified.'"""

	sent: list[dict[str, str]] = []

	def _capture_verification_email(*, to_email: str, verify_url: str) -> bool:
		sent.append({"to_email": to_email, "verify_url": verify_url})
		return True

	monkeypatch.setattr(auth_module, "send_verification_email", _capture_verification_email)

	suffix = _unique_suffix()
	email = f"idempotent-{suffix}@example.org"

	signup = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": email,
			"password": SEED_PASSWORD,
			"name": f"Idempotent User {suffix}",
			"organization": f"Idempotent Org {suffix}",
			"account_type": "MANAGER",
			"website": "",
		},
	)
	assert signup.status_code == 201, signup.text
	token = parse_qs(urlparse(sent[0]["verify_url"]).query)["token"][0]

	# First verify
	first = yee_client.get(f"/yee/auth/verify-email?token={token}")
	assert first.status_code == 200, first.text
	assert first.json()["message"] == "Email verified successfully."

	# Second verify (idempotent)
	second = yee_client.get(f"/yee/auth/verify-email?token={token}")
	assert second.status_code == 200, second.text
	assert second.json()["message"] == "Email already verified."


def test_verify_email_invalid_token_returns_400(yee_client: TestClient) -> None:
	"""A garbage verification token yields 400 'Invalid verification token.'"""

	resp = yee_client.get("/yee/auth/verify-email?token=totallyinvalidgarbagetoken12345")
	assert resp.status_code == 400, resp.text
	assert resp.json()["detail"] == "Invalid verification token."


def test_resend_verification_for_unverified_user(
	yee_client: TestClient,
	monkeypatch,
) -> None:
	"""Resending verification for an unverified user sends a new email."""

	sent: list[dict[str, str]] = []

	def _capture_verification_email(*, to_email: str, verify_url: str) -> bool:
		sent.append({"to_email": to_email, "verify_url": verify_url})
		return True

	monkeypatch.setattr(auth_module, "send_verification_email", _capture_verification_email)

	suffix = _unique_suffix()
	email = f"resend-{suffix}@example.org"

	# Sign up (triggers first verification email)
	signup = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": email,
			"password": SEED_PASSWORD,
			"name": f"Resend User {suffix}",
			"organization": f"Resend Org {suffix}",
			"account_type": "MANAGER",
			"website": "",
		},
	)
	assert signup.status_code == 201, signup.text
	assert len(sent) == 1

	# Resend verification
	resend = yee_client.post(
		"/yee/auth/resend-verification",
		json={"email": email, "website": ""},
	)
	assert resend.status_code == 200, resend.text
	assert resend.json()["message"] == "If your email exists, a verification link has been sent."

	# The capture list should have grown
	assert len(sent) == 2
	assert sent[1]["to_email"] == email


def test_resend_verification_for_already_verified_user(
	yee_client: TestClient,
	monkeypatch,
) -> None:
	"""Resending verification for an already-verified user returns 200 but sends no email."""

	sent: list[dict[str, str]] = []

	def _capture_verification_email(*, to_email: str, verify_url: str) -> bool:
		sent.append({"to_email": to_email, "verify_url": verify_url})
		return True

	monkeypatch.setattr(auth_module, "send_verification_email", _capture_verification_email)

	# The seeded manager is already verified
	resend = yee_client.post(
		"/yee/auth/resend-verification",
		json={"email": SEED_MANAGER_EMAIL, "website": ""},
	)
	assert resend.status_code == 200, resend.text
	assert resend.json()["message"] == "If your email exists, a verification link has been sent."
	# No email sent because user is already verified
	assert sent == []


def test_resend_verification_for_unknown_email(
	yee_client: TestClient,
	monkeypatch,
) -> None:
	"""Resending verification for an unknown email returns 200 (timing-safe) with no email sent."""

	sent: list[dict[str, str]] = []

	def _capture_verification_email(*, to_email: str, verify_url: str) -> bool:
		sent.append({"to_email": to_email, "verify_url": verify_url})
		return True

	monkeypatch.setattr(auth_module, "send_verification_email", _capture_verification_email)

	resend = yee_client.post(
		"/yee/auth/resend-verification",
		json={"email": "nonexistent-user@example.org", "website": ""},
	)
	assert resend.status_code == 200, resend.text
	assert resend.json()["message"] == "If your email exists, a verification link has been sent."
	assert sent == []


def test_resend_verification_new_token_works(
	yee_client: TestClient,
	monkeypatch,
) -> None:
	"""After resend, the new verification token works and the user can log in."""

	sent: list[dict[str, str]] = []

	def _capture_verification_email(*, to_email: str, verify_url: str) -> bool:
		sent.append({"to_email": to_email, "verify_url": verify_url})
		return True

	monkeypatch.setattr(auth_module, "send_verification_email", _capture_verification_email)

	suffix = _unique_suffix()
	email = f"resendnew-{suffix}@example.org"

	# Sign up
	signup = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": email,
			"password": SEED_PASSWORD,
			"name": f"Resend New {suffix}",
			"organization": f"Resend New Org {suffix}",
			"account_type": "MANAGER",
			"website": "",
		},
	)
	assert signup.status_code == 201, signup.text

	# Resend verification
	resend = yee_client.post(
		"/yee/auth/resend-verification",
		json={"email": email, "website": ""},
	)
	assert resend.status_code == 200, resend.text
	assert len(sent) == 2

	# Use the NEW token (from the resend, index 1) to verify
	new_token = parse_qs(urlparse(sent[1]["verify_url"]).query)["token"][0]
	verify = yee_client.get(f"/yee/auth/verify-email?token={new_token}")
	assert verify.status_code == 200, verify.text
	assert verify.json()["message"] == "Email verified successfully."

	# Login now works
	login = yee_client.post("/yee/auth/login", json={"email": email, "password": SEED_PASSWORD})
	assert login.status_code == 200, login.text
	assert login.json()["user"]["email_verified"] is True


# ---------------------------------------------------------------------------
# FLOW D -- manager complete-profile happy path
# ---------------------------------------------------------------------------


def test_manager_complete_profile_happy_path(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""A verified primary manager completes their profile with all required fields.

	After completion: profile_completed=True, next_step=DASHBOARD.
	This test does NOT duplicate the validation-negative cases already covered
	in test_manager_workflows.py (missing phone, empty profession).
	"""

	sent: list[dict[str, str]] = []

	def _capture_verification_email(*, to_email: str, verify_url: str) -> bool:
		sent.append({"to_email": to_email, "verify_url": verify_url})
		return True

	monkeypatch.setattr(auth_module, "send_verification_email", _capture_verification_email)

	suffix = _unique_suffix()
	email = f"profile-{suffix}@example.org"
	org_name = f"Profile Org {suffix}"

	# Sign up
	signup = yee_client.post(
		"/yee/auth/signup",
		json={
			"email": email,
			"password": SEED_PASSWORD,
			"name": f"Profile Manager {suffix}",
			"organization": org_name,
			"account_type": "MANAGER",
			"website": "",
		},
	)
	assert signup.status_code == 201, signup.text

	# Verify email
	asyncio.run(_verify_user_email(yee_test_session_factory, email))

	# Log in
	login = yee_client.post("/yee/auth/login", json={"email": email, "password": SEED_PASSWORD})
	assert login.status_code == 200, login.text
	user_data = login.json()["user"]
	assert user_data["profile_completed"] is False
	assert user_data["next_step"] == "COMPLETE_PROFILE"
	headers = _bearer_headers(login.json()["access_token"])

	# Complete profile with all required fields
	complete = yee_client.post(
		"/yee/auth/complete-profile",
		headers=headers,
		json={
			"name": f"Profile Manager {suffix}",
			"job_title": "Senior Researcher",
			"profession_disciplines": ["Urban Planning", "Public Health"],
			"organization": org_name,
			"phone_number": "+1 555 867 5309",
		},
	)
	assert complete.status_code == 200, complete.text
	result = complete.json()["user"]
	assert result["profile_completed"] is True
	assert result["next_step"] == "DASHBOARD"
	assert result["email_verified"] is True
	assert result["account_type"] == "MANAGER"

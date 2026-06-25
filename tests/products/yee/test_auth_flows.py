"""YEE self-service auth flow integration tests.

These pin the password-reset path for verified YEE users and confirm that the
protected seeded demo accounts cannot drift through self-service reset.
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
	_create_verified_manager_user,
)


def test_password_reset_flow_updates_password(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""A verified YEE user can request a reset link and log in with the new password."""

	reset_email = "resettable-manager@example.org"
	asyncio.run(
		_create_verified_manager_user(
			yee_test_session_factory,
			email=reset_email,
			password=SEED_PASSWORD,
			name="Resettable Manager",
		)
	)
	captured_reset_url: dict[str, str] = {}

	def _capture_reset_email(*, to_email: str, reset_url: str) -> bool:
		captured_reset_url["to_email"] = to_email
		captured_reset_url["reset_url"] = reset_url
		return True

	monkeypatch.setattr(auth_module, "send_password_reset_email", _capture_reset_email)

	forgot = yee_client.post(
		"/yee/auth/forgot-password",
		json={"email": reset_email, "website": ""},
		headers={"X-Frontend-Origin": "http://localhost:3000"},
	)
	assert forgot.status_code == 200, forgot.text
	assert captured_reset_url["to_email"] == reset_email

	token = parse_qs(urlparse(captured_reset_url["reset_url"]).query)["token"][0]
	new_password = "EvenBetterPass123!"
	reset = yee_client.post(
		"/yee/auth/reset-password",
		json={"token": token, "password": new_password, "website": ""},
	)
	assert reset.status_code == 200, reset.text

	login = yee_client.post("/yee/auth/login", json={"email": reset_email, "password": new_password})
	assert login.status_code == 200, login.text
	assert login.json()["user"]["account_type"] == "MANAGER"


def test_protected_demo_password_reset_is_blocked(yee_client: TestClient, monkeypatch) -> None:
	"""Protected seeded demo accounts do not drift through self-service reset."""

	sent_reset_emails: list[dict[str, str]] = []

	def _capture_reset_email(*, to_email: str, reset_url: str) -> bool:
		sent_reset_emails.append({"to_email": to_email, "reset_url": reset_url})
		return True

	monkeypatch.setattr(auth_module, "send_password_reset_email", _capture_reset_email)

	forgot = yee_client.post(
		"/yee/auth/forgot-password",
		json={"email": SEED_MANAGER_EMAIL, "website": ""},
		headers={"X-Frontend-Origin": "http://localhost:3000"},
	)
	assert forgot.status_code == 200, forgot.text
	assert sent_reset_emails == []

	login = yee_client.post("/yee/auth/login", json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD})
	assert login.status_code == 200, login.text

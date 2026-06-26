"""YEE auditor-invite end-to-end integration tests.

These cover the manager-initiated auditor invite path: a primary manager mints
an auditor invite from the dashboard, and the invitee accepts it by submitting a
full name and password. The acceptance step previously raised a 500 (the new
``Auditor`` row was created without its required ``full_name``/``email``), which
surfaced on the frontend as the JSON parse error
``Unexpected token 'I', "Internal S"... is not valid JSON``. These tests pin the
fixed behaviour so a regression fails loudly here instead of in the browser.
"""

from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.dashboard_router as dashboard_router_module
from app.models import Auditor, AuditorInvite, User
from tests.products.yee._helpers import (
	SEED_PASSWORD,
	_signup_primary_manager,
	_unique_suffix,
)


def _create_auditor_invite(
	client: TestClient,
	manager_headers: dict[str, str],
	monkeypatch,
	*,
	email: str,
) -> str:
	"""Mint an auditor invite from the dashboard and return its raw token."""

	captured: list[dict[str, str]] = []

	def _capture_auditor_invite_email(*, to_email: str, invite_url: str) -> bool:
		captured.append({"to_email": to_email, "invite_url": invite_url})
		return True

	monkeypatch.setattr(
		dashboard_router_module,
		"send_auditor_invite_email",
		_capture_auditor_invite_email,
	)

	create_response = client.post(
		"/yee/dashboard/auditor-invites",
		headers=manager_headers,
		json={"email": email},
	)
	assert create_response.status_code == 200, create_response.text
	body = create_response.json()
	assert body["email"] == email
	assert body["status"] == "Pending acceptance"
	# The emailed link and the API response must point at the same token.
	assert captured[-1]["to_email"] == email
	assert captured[-1]["invite_url"] == body["invite_url"]
	return body["invite_url"].rsplit("/", 1)[-1]


def test_manager_invite_creates_pending_auditor_invite(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""A primary manager can mint an auditor invite link from the dashboard."""

	manager = _signup_primary_manager(yee_client, yee_test_session_factory)
	invite_email = f"invited-auditor-{_unique_suffix()}@example.org"

	token = _create_auditor_invite(
		yee_client,
		manager["headers"],
		monkeypatch,
		email=invite_email,
	)
	assert token

	# The invite preview is readable before acceptance and reflects the org.
	preview = yee_client.get(f"/yee/auth/invite/{token}")
	assert preview.status_code == 200, preview.text
	preview_body = preview.json()
	assert preview_body["email"] == invite_email
	assert preview_body["organization"] == manager["organization"]
	assert preview_body["accepted"] is False


def test_auditor_invite_acceptance_creates_linked_auditor(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""Accepting an auditor invite returns a session and persists the auditor.

	This is the exact path that previously 500'd: the invite has no pre-existing
	auditor profile, so acceptance must create one with the submitted full name.
	"""

	manager = _signup_primary_manager(yee_client, yee_test_session_factory)
	invite_email = f"invited-auditor-{_unique_suffix()}@example.org"
	token = _create_auditor_invite(
		yee_client,
		manager["headers"],
		monkeypatch,
		email=invite_email,
	)

	full_name = "Invited Auditor"
	accept = yee_client.post(
		f"/yee/auth/invite/{token}/accept",
		json={"name": full_name, "password": SEED_PASSWORD},
	)
	assert accept.status_code == 200, accept.text
	accept_body = accept.json()
	assert accept_body["access_token"]
	assert accept_body["user"]["email"] == invite_email
	assert accept_body["user"]["account_type"] == "AUDITOR"
	assert accept_body["user"]["has_auditor_profile"] is True

	# The persisted auditor row carries the required full_name/email and is linked
	# to both the invitee user and the inviting manager's organization account.
	auditor, invite = asyncio.run(_load_auditor_and_invite_by_email(yee_test_session_factory, invite_email))
	assert auditor is not None
	assert auditor.full_name == full_name
	assert auditor.email == invite_email
	assert auditor.user_id is not None
	assert auditor.account_id is not None
	assert invite is not None
	assert invite.accepted_at is not None
	assert invite.auditor_id == auditor.id

	# The freshly created auditor can log in with the password they just set.
	login = yee_client.post(
		"/yee/auth/login",
		json={"email": invite_email, "password": SEED_PASSWORD},
	)
	assert login.status_code == 200, login.text
	assert login.json()["user"]["account_type"] == "AUDITOR"


def test_auditor_invite_acceptance_is_single_use(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""An accepted auditor invite cannot be replayed."""

	manager = _signup_primary_manager(yee_client, yee_test_session_factory)
	invite_email = f"invited-auditor-{_unique_suffix()}@example.org"
	token = _create_auditor_invite(
		yee_client,
		manager["headers"],
		monkeypatch,
		email=invite_email,
	)

	first = yee_client.post(
		f"/yee/auth/invite/{token}/accept",
		json={"name": "Invited Auditor", "password": SEED_PASSWORD},
	)
	assert first.status_code == 200, first.text

	replay = yee_client.post(
		f"/yee/auth/invite/{token}/accept",
		json={"name": "Invited Auditor", "password": SEED_PASSWORD},
	)
	assert replay.status_code == 400, replay.text
	assert replay.json()["detail"] == "Invite has already been accepted."


def test_auditor_invite_acceptance_rejects_manager_email(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""An auditor invite cannot repurpose an existing manager account."""

	manager = _signup_primary_manager(yee_client, yee_test_session_factory)
	token = _create_auditor_invite(
		yee_client,
		manager["headers"],
		monkeypatch,
		email=manager["email"],
	)

	accept = yee_client.post(
		f"/yee/auth/invite/{token}/accept",
		json={"name": "Should Fail", "password": SEED_PASSWORD},
	)
	assert accept.status_code == 409, accept.text
	assert accept.json()["detail"] == "This email is already used by a manager account."


async def _load_auditor_and_invite_by_email(
	session_factory: async_sessionmaker[AsyncSession],
	email: str,
) -> tuple[Auditor | None, AuditorInvite | None]:
	"""Fetch the auditor profile and invite row tied to one invited email."""

	async with session_factory() as session:
		user = (await session.execute(select(User).where(User.email == email))).scalar_one_or_none()
		auditor = None
		if user is not None:
			auditor = (await session.execute(select(Auditor).where(Auditor.user_id == user.id))).scalar_one_or_none()
		invite = (await session.execute(select(AuditorInvite).where(AuditorInvite.email == email))).scalar_one_or_none()
	return auditor, invite

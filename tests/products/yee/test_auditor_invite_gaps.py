"""YEE auditor-invite gap coverage (FLOW G/H gaps).

Covers the auditor-invite dashboard gaps NOT already tested in
``test_auditor_invite_flow.py`` (which covers: create, preview, accept,
single-use guard, manager-email rejection, and post-accept complete-profile).

Gap tests added here:

- POST /yee/dashboard/auditor-invites — auditor caller -> 403
- POST /yee/dashboard/auditor-invites — no token -> 401
- Expired-token preview -> 400 via DB patching of ``expires_at``
- Expired-token accept -> 400

Skipped tests (with reasons):

- GET /yee/dashboard/auditor-invites (list pending auditor invites):
  NO SUCH ROUTE exists in dashboard_router.py. Only POST create exists.
- POST /yee/dashboard/auditor-invites/{id}/resend (resend auditor invite):
  NO SUCH ROUTE exists. Only manager-invite resend is implemented.
- DELETE /yee/dashboard/auditor-invites/{id} (revoke pending auditor invite):
  NO SUCH ROUTE exists. Only manager-invite revoke is implemented.

The backend currently only supports creating auditor invites from the dashboard.
Listing, resending, and revoking auditor invites are not yet implemented.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

import app.dashboard_router as dashboard_router_module
from app.models import AuditorInvite
from tests.products.yee._helpers import (
	SEED_PASSWORD,
	_bearer_headers,
	_login_auditor,
	_signup_primary_manager,
	_unique_suffix,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mint_auditor_invite(
	client: TestClient,
	manager_headers: dict[str, str],
	monkeypatch,
	*,
	email: str,
) -> tuple[str, str]:
	"""Create an auditor invite and return (token, invite_id).

	Monkeypatches the email sender so no real email is dispatched.
	"""
	captured: list[dict[str, str]] = []

	def _capture(*, to_email: str, invite_url: str) -> bool:
		captured.append({"to_email": to_email, "invite_url": invite_url})
		return True

	monkeypatch.setattr(dashboard_router_module, "send_auditor_invite_email", _capture)

	resp = client.post(
		"/yee/dashboard/auditor-invites",
		headers=manager_headers,
		json={"email": email},
	)
	assert resp.status_code == 200, resp.text
	body = resp.json()
	assert body["email"] == email
	assert body["status"] == "Pending acceptance"
	assert captured[-1]["to_email"] == email

	token = captured[-1]["invite_url"].rsplit("/", 1)[-1]
	invite_id = body["id"]
	return token, invite_id


async def _expire_auditor_invite(
	session_factory: async_sessionmaker[AsyncSession],
	invite_id: str,
) -> None:
	"""Set the invite's ``expires_at`` to 1 day in the past to simulate expiry."""

	import uuid as _uuid

	async with session_factory() as session:
		invite = (
			await session.execute(select(AuditorInvite).where(AuditorInvite.id == _uuid.UUID(invite_id)))
		).scalar_one_or_none()
		assert invite is not None, f"AuditorInvite {invite_id} not found"
		invite.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
		await session.commit()


# ---------------------------------------------------------------------------
# POST /yee/dashboard/auditor-invites — authz gaps
# ---------------------------------------------------------------------------


def test_auditor_cannot_create_auditor_invite(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""An auditor token on POST /yee/dashboard/auditor-invites -> 403."""

	token = _login_auditor(yee_client)
	resp = yee_client.post(
		"/yee/dashboard/auditor-invites",
		headers=_bearer_headers(token),
		json={"email": "should-fail@example.org"},
	)
	assert resp.status_code == 403


def test_no_token_create_auditor_invite_returns_401(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""POST /yee/dashboard/auditor-invites without auth -> 401."""

	resp = yee_client.post(
		"/yee/dashboard/auditor-invites",
		json={"email": "no-auth@example.org"},
	)
	assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Expired-token preview and accept
# ---------------------------------------------------------------------------


def test_expired_auditor_invite_preview_returns_400(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""GET /yee/auth/invite/{token} returns 400 when the invite has expired.

	We create a valid invite, then patch its ``expires_at`` to the past via
	the session_factory (mirroring the DB-write pattern used in _helpers for
	legacy manager invites).
	"""

	manager = _signup_primary_manager(yee_client, yee_test_session_factory)
	invite_email = f"expire-preview-{_unique_suffix()}@example.org"

	token, invite_id = _mint_auditor_invite(
		yee_client,
		manager["headers"],
		monkeypatch,
		email=invite_email,
	)

	# Verify the invite works before expiry
	preview_ok = yee_client.get(f"/yee/auth/invite/{token}")
	assert preview_ok.status_code == 200

	# Expire the invite in the DB
	asyncio.run(_expire_auditor_invite(yee_test_session_factory, invite_id))

	# Preview should now fail
	preview_expired = yee_client.get(f"/yee/auth/invite/{token}")
	assert preview_expired.status_code == 400, preview_expired.text


def test_expired_auditor_invite_accept_returns_400(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""POST /yee/auth/invite/{token}/accept returns 400 when the invite has expired.

	Same DB-patching approach: create a valid invite, expire it, then try to accept.
	"""

	manager = _signup_primary_manager(yee_client, yee_test_session_factory)
	invite_email = f"expire-accept-{_unique_suffix()}@example.org"

	token, invite_id = _mint_auditor_invite(
		yee_client,
		manager["headers"],
		monkeypatch,
		email=invite_email,
	)

	# Expire the invite
	asyncio.run(_expire_auditor_invite(yee_test_session_factory, invite_id))

	# Accept should fail
	accept = yee_client.post(
		f"/yee/auth/invite/{token}/accept",
		json={"name": "Expired Auditor", "password": SEED_PASSWORD},
	)
	assert accept.status_code == 400, accept.text


# ---------------------------------------------------------------------------
# Auditor invite email send monkeypatch verification
# ---------------------------------------------------------------------------


def test_auditor_invite_email_is_sent_via_monkeypatch(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""Creating an auditor invite calls ``send_auditor_invite_email`` with the correct args.

	This verifies the monkeypatch target name is correct and the email function
	is called with both ``to_email`` and ``invite_url`` keyword arguments.
	"""

	captured: list[dict[str, str]] = []

	def _capture(*, to_email: str, invite_url: str) -> bool:
		captured.append({"to_email": to_email, "invite_url": invite_url})
		return True

	monkeypatch.setattr(dashboard_router_module, "send_auditor_invite_email", _capture)

	manager = _signup_primary_manager(yee_client, yee_test_session_factory)
	invite_email = f"email-verify-{_unique_suffix()}@example.org"

	resp = yee_client.post(
		"/yee/dashboard/auditor-invites",
		headers=manager["headers"],
		json={"email": invite_email},
	)
	assert resp.status_code == 200
	assert len(captured) >= 1
	assert captured[-1]["to_email"] == invite_email
	assert "/invite/" in captured[-1]["invite_url"]


# ---------------------------------------------------------------------------
# Multiple invites to the same email
# ---------------------------------------------------------------------------


def test_second_auditor_invite_to_same_email_creates_new_invite(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""A manager can create multiple auditor invites to the same email (each gets a new row).

	Unlike manager invites, auditor invites do not enforce "one active invite
	per email" at the route level. Each POST creates a new AuditorInvite row.
	"""

	captured: list[dict[str, str]] = []

	def _capture(*, to_email: str, invite_url: str) -> bool:
		captured.append({"to_email": to_email, "invite_url": invite_url})
		return True

	monkeypatch.setattr(dashboard_router_module, "send_auditor_invite_email", _capture)

	manager = _signup_primary_manager(yee_client, yee_test_session_factory)
	invite_email = f"multi-invite-{_unique_suffix()}@example.org"

	resp1 = yee_client.post(
		"/yee/dashboard/auditor-invites",
		headers=manager["headers"],
		json={"email": invite_email},
	)
	assert resp1.status_code == 200
	id1 = resp1.json()["id"]

	resp2 = yee_client.post(
		"/yee/dashboard/auditor-invites",
		headers=manager["headers"],
		json={"email": invite_email},
	)
	assert resp2.status_code == 200
	id2 = resp2.json()["id"]

	# Both invites exist with different IDs
	assert id1 != id2

"""Tests for the offline submit-durability backend contract.

Covers the submit-intent beacon endpoint, idempotent submit replay, and the
never-arrived detector job (``notify_stalled_submissions``).
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import AuditStatus, PlayspaceSubmission
from app.products.playspace.services import PlayspaceAuditService
from tests.products.playspace.conftest import PlayspaceSeedSnapshot
from tests.products.playspace.test_api_endpoints import (
	SEED_PASSWORD,
	_bearer_headers,
	_create_place,
	_create_project,
	_login_auditor,
	_login_manager,
	_unique_suffix,
)


def _open_fresh_audit(client: TestClient, manager_token: str, suffix: str) -> tuple[str, dict[str, str]]:
	"""Create a project/place/auditor, assign, and open an in-progress audit.

	Returns the audit id and the owning auditor's bearer headers.
	"""

	manager_headers = _bearer_headers(manager_token)
	project = _create_project(client, manager_token, suffix=suffix)
	place = _create_place(client, manager_token, project_id=str(project["id"]), suffix=suffix)

	auditor_email = f"durability-{suffix}@example.org"
	created_auditor = client.post(
		"/playspace/auditor-profiles",
		headers=manager_headers,
		json={
			"email": auditor_email,
			"full_name": f"Durability Auditor {suffix}",
			"auditor_code": f"DUR-{suffix.upper()}",
			"country": "New Zealand",
			"role": "Tester",
		},
	)
	assert created_auditor.status_code == 201
	auditor_profile_id = created_auditor.json()["id"]
	temporary_password = created_auditor.json()["temporary_password"]

	auditor_headers = _bearer_headers(_login_auditor(client, auditor_email, str(temporary_password)))

	client.post(
		f"/playspace/auditor-profiles/{auditor_profile_id}/assignments",
		headers=manager_headers,
		json={"project_id": project["id"], "place_id": place["id"]},
	)

	access_response = client.post(
		f"/playspace/places/{place['id']}/audits/access",
		headers=auditor_headers,
		json={"project_id": project["id"]},
	)
	assert access_response.status_code == 200
	return access_response.json()["audit_id"], auditor_headers


async def _read_submit_fields(
	session_factory: async_sessionmaker[AsyncSession],
	audit_id: str,
) -> dict[str, object]:
	"""Read the submit-intent tracking columns for one audit."""

	async with session_factory() as session:
		result = await session.execute(select(PlayspaceSubmission).where(PlayspaceSubmission.id == uuid.UUID(audit_id)))
		audit = result.scalar_one()
		return {
			"status": audit.status,
			"submit_intended_at": audit.submit_intended_at,
			"submit_intent_client_at": audit.submit_intent_client_at,
			"submit_stall_notified_at": audit.submit_stall_notified_at,
			"submit_idempotency_key": audit.submit_idempotency_key,
		}


async def _mutate_audit(
	session_factory: async_sessionmaker[AsyncSession],
	audit_id: str,
	**fields: object,
) -> None:
	"""Set arbitrary columns on one audit (white-box test setup)."""

	async with session_factory() as session:
		result = await session.execute(select(PlayspaceSubmission).where(PlayspaceSubmission.id == uuid.UUID(audit_id)))
		audit = result.scalar_one()
		for key, value in fields.items():
			setattr(audit, key, value)
		await session.commit()


def test_submit_intent_beacon_records_and_guards(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
	playspace_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""The beacon stamps intent once, is auditor-only, and handles unknown ids."""

	suffix = _unique_suffix()
	manager_token = _login_manager(playspace_client)
	audit_id, auditor_headers = _open_fresh_audit(playspace_client, manager_token, suffix)

	client_intended_at = "2026-06-12T09:30:00+00:00"
	first = playspace_client.post(
		f"/playspace/audits/{audit_id}/submit-intent",
		headers=auditor_headers,
		json={"client_intended_at": client_intended_at},
	)
	assert first.status_code == 204
	assert first.content == b""

	fields = asyncio.run(_read_submit_fields(playspace_test_session_factory, audit_id))
	assert fields["submit_intended_at"] is not None
	assert fields["submit_intent_client_at"] is not None
	stamped_intent = fields["submit_intended_at"]

	# Repeat beacon: server intent timestamp is preserved (stamped once).
	second = playspace_client.post(
		f"/playspace/audits/{audit_id}/submit-intent",
		headers=auditor_headers,
		json={},
	)
	assert second.status_code == 204
	fields_after = asyncio.run(_read_submit_fields(playspace_test_session_factory, audit_id))
	assert fields_after["submit_intended_at"] == stamped_intent

	# Manager may not call the auditor-only beacon.
	manager_headers = _bearer_headers(manager_token)
	manager_attempt = playspace_client.post(
		f"/playspace/audits/{audit_id}/submit-intent",
		headers=manager_headers,
		json={},
	)
	assert manager_attempt.status_code == 403

	# Unknown audit id is a 404.
	missing = playspace_client.post(
		f"/playspace/audits/{uuid.uuid4()}/submit-intent",
		headers=auditor_headers,
		json={},
	)
	assert missing.status_code == 404

	# Unauthenticated request is rejected.
	unauth = playspace_client.post(f"/playspace/audits/{audit_id}/submit-intent", json={})
	assert unauth.status_code in (401, 403)


def test_submit_idempotent_replay_returns_session(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
	playspace_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""A replayed submit with the stored key returns 200 instead of a 409."""

	audit_id = playspace_seed_snapshot.riverside_submitted_audit_id
	auditor_headers = _bearer_headers(
		_login_auditor(playspace_client, playspace_seed_snapshot.seeded_auditor_email, SEED_PASSWORD)
	)

	# The seeded audit is already SUBMITTED; store a known idempotency key as if
	# the original successful submit had carried it.
	replay_key = f"replay-{uuid.uuid4().hex[:12]}"
	asyncio.run(_mutate_audit(playspace_test_session_factory, audit_id, submit_idempotency_key=replay_key))

	# Replay with the matching key returns the submitted session, not a conflict.
	replay = playspace_client.post(
		f"/playspace/audits/{audit_id}/submit",
		headers=auditor_headers,
		json={"idempotency_key": replay_key},
	)
	assert replay.status_code == 200
	body = replay.json()
	assert body["audit_id"] == audit_id
	assert body["status"] == "SUBMITTED"

	# A non-matching key keeps the protective 409 (already submitted).
	mismatched = playspace_client.post(
		f"/playspace/audits/{audit_id}/submit",
		headers=auditor_headers,
		json={"idempotency_key": "some-other-key"},
	)
	assert mismatched.status_code == 409

	# No key at all also keeps the 409.
	no_key = playspace_client.post(
		f"/playspace/audits/{audit_id}/submit",
		headers=auditor_headers,
		json={},
	)
	assert no_key.status_code == 409


def test_notify_stalled_submissions_detector(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
	playspace_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""The detector emails stalled intents once, respects the renotify window, and skips submitted audits."""

	suffix = _unique_suffix()
	manager_token = _login_manager(playspace_client)
	audit_id, _auditor_headers = _open_fresh_audit(playspace_client, manager_token, suffix)

	# Record an intent two hours in the past so the audit is considered stalled.
	intent_time = datetime.now(timezone.utc) - timedelta(hours=2)
	asyncio.run(_mutate_audit(playspace_test_session_factory, audit_id, submit_intended_at=intent_time))

	sent_emails: list[dict[str, object]] = []

	def _fake_send(**kwargs: object) -> bool:
		sent_emails.append(kwargs)
		return True

	monkeypatch.setattr(
		"app.email_service.send_email.send_audit_submit_failure_email",
		_fake_send,
	)

	async def _run_detector(*, stall_threshold: timedelta, renotify_after: timedelta) -> list[uuid.UUID]:
		async with playspace_test_session_factory() as session:
			service = PlayspaceAuditService(session)
			return await service.notify_stalled_submissions(
				stall_threshold=stall_threshold,
				renotify_after=renotify_after,
			)

	# First sweep notifies the stalled audit exactly once.
	notified = asyncio.run(_run_detector(stall_threshold=timedelta(hours=1), renotify_after=timedelta(hours=24)))
	assert uuid.UUID(audit_id) in notified
	assert len(sent_emails) == 1
	fields = asyncio.run(_read_submit_fields(playspace_test_session_factory, audit_id))
	assert fields["submit_stall_notified_at"] is not None

	# Second sweep within the renotify window does not email again.
	notified_again = asyncio.run(_run_detector(stall_threshold=timedelta(hours=1), renotify_after=timedelta(hours=24)))
	assert uuid.UUID(audit_id) not in notified_again
	assert len(sent_emails) == 1

	# A submitted audit is never notified, even with an old intent and the
	# notification cooldown cleared.
	asyncio.run(
		_mutate_audit(
			playspace_test_session_factory,
			audit_id,
			status=AuditStatus.SUBMITTED,
			submit_stall_notified_at=None,
		)
	)
	notified_after_submit = asyncio.run(
		_run_detector(stall_threshold=timedelta(hours=1), renotify_after=timedelta(seconds=0))
	)
	assert uuid.UUID(audit_id) not in notified_after_submit
	assert len(sent_emails) == 1


def test_notify_stalled_submissions_keeps_row_eligible_on_email_failure(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
	playspace_test_session_factory: async_sessionmaker[AsyncSession],
	monkeypatch,
) -> None:
	"""A failed delivery must not stamp/suppress the row; it stays eligible to retry."""

	suffix = _unique_suffix()
	manager_token = _login_manager(playspace_client)
	audit_id, _auditor_headers = _open_fresh_audit(playspace_client, manager_token, suffix)

	intent_time = datetime.now(timezone.utc) - timedelta(hours=2)
	asyncio.run(_mutate_audit(playspace_test_session_factory, audit_id, submit_intended_at=intent_time))

	# The email helper returns False (not raising) on a delivery failure.
	delivery_succeeds = {"value": False}

	def _fake_send(**_kwargs: object) -> bool:
		return delivery_succeeds["value"]

	monkeypatch.setattr("app.email_service.send_email.send_audit_submit_failure_email", _fake_send)

	async def _run_detector() -> list[uuid.UUID]:
		async with playspace_test_session_factory() as session:
			service = PlayspaceAuditService(session)
			return await service.notify_stalled_submissions(
				stall_threshold=timedelta(hours=1),
				renotify_after=timedelta(hours=24),
			)

	# Failed delivery: not reported as notified and not stamped (stays eligible).
	failed = asyncio.run(_run_detector())
	assert uuid.UUID(audit_id) not in failed
	fields = asyncio.run(_read_submit_fields(playspace_test_session_factory, audit_id))
	assert fields["submit_stall_notified_at"] is None

	# Once delivery succeeds, the same still-eligible row is notified and stamped.
	delivery_succeeds["value"] = True
	delivered = asyncio.run(_run_detector())
	assert uuid.UUID(audit_id) in delivered
	fields_after = asyncio.run(_read_submit_fields(playspace_test_session_factory, audit_id))
	assert fields_after["submit_stall_notified_at"] is not None

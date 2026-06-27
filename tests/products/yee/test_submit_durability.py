"""Offline-safe submit durability for the YEE final-submit boundary.

YEE drafts live on the device; the backend only has to be durable at final
submit. These tests pin that contract:

* a first submit stores its idempotency key and writes exactly one row;
* a replay carrying the same key returns the already-submitted record (200)
  instead of a 409, so an ambiguous network failure cannot lose the result;
* a replay with a different key, or no key, keeps the protective conflict;
* duplicate ``yee_audit_submissions`` rows for one ``(auditor, place)`` are
  impossible at the database level, even under a race that bypasses the route;
* the manager/admin reads that depend on a submission keep working afterwards.

The suite reuses the deterministic YEE seed and skips automatically when
``TEST_DATABASE_URL_YEE`` is not configured (see ``conftest.py``).
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import YeeAuditSubmission
from app.seed import (
	YEE_AUDITOR_PROFILE_01_ID,
	YEE_PLACE_GREEN_ID,
	YEE_PLACE_LIBRARY_ID,
)
from tests.products.yee._helpers import _bearer_headers, _login_auditor


def test_submit_idempotent_replay_and_conflict_matrix(yee_client: TestClient) -> None:
	"""Same-key replay returns the submission; different/no key stays a 409."""

	headers = _bearer_headers(_login_auditor(yee_client))
	# A place auditor 1 is assigned to but has not submitted, so the first submit
	# below starts from a clean slot (the seeded Hub audit is already submitted).
	place_id = str(YEE_PLACE_GREEN_ID)
	responses_payload = {"QID22": "3"}
	idempotency_key = f"yee-idem-{uuid.uuid4().hex[:12]}"

	# First submit persists exactly one row and records the idempotency key.
	first = yee_client.post(
		"/yee/audits",
		headers=headers,
		json={
			"place_id": place_id,
			"participant_info": {"total_minutes": 10},
			"responses": responses_payload,
			"idempotency_key": idempotency_key,
		},
	)
	assert first.status_code == 201, first.text
	submission_id = first.json()["id"]

	# Replay with the SAME key returns the existing submission, not a conflict.
	replay = yee_client.post(
		"/yee/audits",
		headers=headers,
		json={
			"place_id": place_id,
			"responses": responses_payload,
			"idempotency_key": idempotency_key,
		},
	)
	assert replay.status_code == 200, replay.text
	assert replay.json()["id"] == submission_id

	# Replay with a DIFFERENT key keeps the protective conflict.
	mismatched = yee_client.post(
		"/yee/audits",
		headers=headers,
		json={
			"place_id": place_id,
			"responses": responses_payload,
			"idempotency_key": "some-other-key",
		},
	)
	assert mismatched.status_code == 409, mismatched.text

	# Replay with NO key also keeps the protective conflict.
	no_key = yee_client.post(
		"/yee/audits",
		headers=headers,
		json={"place_id": place_id, "responses": responses_payload},
	)
	assert no_key.status_code == 409, no_key.text

	# Exactly one submission exists for this auditor/place across all replays.
	listing = yee_client.get("/yee/my-audits", headers=headers)
	assert listing.status_code == 200, listing.text
	place_submissions = [item for item in listing.json() if item["place_id"] == place_id]
	assert len(place_submissions) == 1

	# Reads that managers/admins depend on still resolve after submit.
	state = yee_client.get(f"/yee/places/{place_id}/audit-state", headers=headers)
	assert state.status_code == 200, state.text
	assert state.json()["status"] == "SUBMITTED"

	detail = yee_client.get(f"/yee/audits/{submission_id}", headers=headers)
	assert detail.status_code == 200, detail.text
	assert detail.json()["id"] == submission_id


def test_duplicate_submission_rows_blocked_at_database_level(
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""A second row for one ``(auditor, place)`` violates a uniqueness guard.

	This is the race/retry backstop behind the route-level check: even a direct
	insert that skips the API cannot create two submissions for the same pair.
	"""

	async def _attempt_duplicate_insert() -> None:
		async with yee_test_session_factory() as session:
			for _ in range(2):
				session.add(
					YeeAuditSubmission(
						auditor_id=YEE_AUDITOR_PROFILE_01_ID,
						place_id=YEE_PLACE_LIBRARY_ID,
						participant_info_json={},
						responses_json={"QID22": "3"},
						section_scores_json={},
						total_score=0,
					)
				)
			# The unique constraint fires on the second INSERT during flush; the
			# surrounding transaction is rolled back, so nothing persists.
			await session.flush()

	with pytest.raises(IntegrityError):
		asyncio.run(_attempt_duplicate_insert())

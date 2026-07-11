"""Integration coverage for the YEE internal bug-reporting API.

Exercises the three client-facing endpoints against the per-product YEE schema:
file a report (with entity-scope verification), request signed screenshot-upload
params, and match published known issues. The YEE product has no admin surface
yet, so known-issue rows are inserted directly through the session factory.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import KnownIssue, KnownIssueStatus
from app.seed import YEE_PLACE_HUB_ID, YEE_SUBMISSION_HUB_ID
from tests.products.yee._helpers import (
	SEED_AUDITOR_EMAIL,
	_bearer_headers,
	_login_auditor,
)


async def _insert_known_issue(
	session_factory: async_sessionmaker[AsyncSession],
	*,
	title: str,
	symptoms: str,
	is_published: bool,
) -> None:
	"""Insert one known-issue row (no admin endpoint exists for YEE yet)."""

	async with session_factory() as session:
		session.add(
			KnownIssue(
				title=title,
				symptoms=symptoms,
				status=KnownIssueStatus.OPEN,
				tags=["submit"],
				surfaces=["mobile"],
				is_published=is_published,
			)
		)
		await session.commit()


######################################################################################
#################################### Bug Reports #####################################
######################################################################################


def test_auditor_can_file_bug_report(yee_client: TestClient) -> None:
	"""A seeded auditor files a report; reporter identity is snapshotted."""

	headers = _bearer_headers(_login_auditor(yee_client))

	payload = {
		"surface": "mobile",
		"title": "Scale option not tappable",
		"description": "Tapping the third scale option does nothing on the audit screen.",
		"severity": "major",
		"context": {
			"app_version": "2.0.0",
			"screen": "audit/execute",
			"network_online": True,
			"sync_phase": "idle",
		},
	}
	response = yee_client.post("/yee/bug-reports", json=payload, headers=headers)
	assert response.status_code == 201, response.text
	body = response.json()

	assert body["status"] == "new"
	assert body["surface"] == "mobile"
	assert body["severity"] == "major"
	assert body["reporter_role"] == "auditor"
	assert body["reporter_email"] == SEED_AUDITOR_EMAIL
	# Allow-listed context survives; nothing else is invented.
	assert body["context"]["app_version"] == "2.0.0"
	assert body["context"]["screen"] == "audit/execute"


def test_report_persists_owned_yee_submission_ref(yee_client: TestClient) -> None:
	"""An auditor's reference to their own YEE submission is trusted and stored.

	Uses the seeded Hub submission (owned by this auditor) instead of creating
	one: submitting here used to consume the seeded Green slot that
	``test_submit_durability`` needs untouched, making the suite order-dependent.
	"""

	headers = _bearer_headers(_login_auditor(yee_client))
	submission_id = str(YEE_SUBMISSION_HUB_ID)

	response = yee_client.post(
		"/yee/bug-reports",
		headers=headers,
		json={
			"surface": "mobile",
			"title": "Submit confirmation missing",
			"description": "No confirmation after submitting the Hub audit.",
			"severity": "minor",
			"place_id": str(YEE_PLACE_HUB_ID),
			"yee_submission_id": submission_id,
		},
	)
	assert response.status_code == 201, response.text
	body = response.json()
	# The auditor owns this submission and place, so both references are persisted.
	assert body["yee_submission_id"] == submission_id
	assert body["place_id"] == str(YEE_PLACE_HUB_ID)


def test_unknown_entity_reference_is_dropped(yee_client: TestClient) -> None:
	"""A reference to a non-existent submission is not persisted as a FK."""

	headers = _bearer_headers(_login_auditor(yee_client))

	response = yee_client.post(
		"/yee/bug-reports",
		headers=headers,
		json={
			"surface": "mobile",
			"title": "Spurious submission reference",
			"description": "Filed with a submission id that does not exist.",
			"severity": "minor",
			"yee_submission_id": str(uuid.uuid4()),
		},
	)
	assert response.status_code == 201, response.text
	assert response.json()["yee_submission_id"] is None


def test_context_rejects_unknown_fields(yee_client: TestClient) -> None:
	"""The diagnostic context is a strict allow-list to keep sensitive data out."""

	headers = _bearer_headers(_login_auditor(yee_client))

	response = yee_client.post(
		"/yee/bug-reports",
		headers=headers,
		json={
			"surface": "mobile",
			"title": "Should be rejected",
			"description": "Context carries a disallowed field.",
			"severity": "minor",
			"context": {"app_version": "2.0.0", "auth_token": "secret-should-not-be-allowed"},
		},
	)
	assert response.status_code == 422, response.text


######################################################################################
#################################### Known Issues ####################################
######################################################################################


def test_known_issue_match_returns_published_only(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""Published known issues are matched for reporters; drafts are not."""

	asyncio.run(
		_insert_known_issue(
			yee_test_session_factory,
			title="Submit button freeze on mobile",
			symptoms="Submit spins forever on the final comments screen.",
			is_published=True,
		)
	)
	asyncio.run(
		_insert_known_issue(
			yee_test_session_factory,
			title="Submit button freeze draft (unpublished)",
			symptoms="Submit spins forever on the final comments screen.",
			is_published=False,
		)
	)

	headers = _bearer_headers(_login_auditor(yee_client))
	response = yee_client.get("/yee/known-issues/match?q=submit+freeze", headers=headers)
	assert response.status_code == 200, response.text

	titles = {row["title"] for row in response.json()}
	assert "Submit button freeze on mobile" in titles
	# The unpublished draft must never be surfaced.
	assert "Submit button freeze draft (unpublished)" not in titles


######################################################################################
################################## Auth / Screenshot #################################
######################################################################################


def test_screenshot_upload_params_require_auth_and_return_signed_shape(yee_client: TestClient) -> None:
	"""The signing endpoint needs auth and, when configured, returns signed params."""

	# Unauthenticated callers cannot obtain upload params.
	assert yee_client.get("/yee/bug-reports/screenshot-upload-params").status_code in (401, 403)

	headers = _bearer_headers(_login_auditor(yee_client))
	response = yee_client.get("/yee/bug-reports/screenshot-upload-params", headers=headers)
	# 503 when Cloudinary creds are absent (e.g. CI); 200 with signed params when configured.
	assert response.status_code in (200, 503), response.text
	if response.status_code == 200:
		body = response.json()
		assert set(body) >= {"cloud_name", "api_key", "timestamp", "signature", "folder"}
		assert body["folder"] == "bug-reports"
		# The API secret must never be returned to the client.
		assert "api_secret" not in body


def test_unauthenticated_requests_are_rejected(yee_client: TestClient) -> None:
	"""Filing and matching both require authentication."""

	assert yee_client.post(
		"/yee/bug-reports",
		json={"surface": "mobile", "title": "x", "description": "y", "severity": "minor"},
	).status_code in (401, 403)
	assert yee_client.get("/yee/known-issues/match?q=test").status_code in (401, 403)

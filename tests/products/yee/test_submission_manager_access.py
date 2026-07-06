"""Manager access to individual YEE submissions (GET /yee/audits/{submission_id}).

The submission detail route is owner-gated for auditors, but managers may read
any submission whose place sits in a project owned by their account — the same
scope the dashboard reports expose — without needing an auditor profile.

Seeded fixture used: ``YEE_SUBMISSION_HUB_ID`` (cccc...ccc1), AUD001's Hub
submission in the Baseline project under account ``1111...1111``, owned by the
seeded manager ``manager-demo@yee.local``.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.seed import (
	YEE_PLACE_HUB_ID,
	YEE_PROJECT_CORE_ID,
	YEE_SUBMISSION_HUB_ID,
)
from tests.products.yee._helpers import (
	SEED_AUDITOR_THREE_EMAIL,
	SEED_MANAGER_EMAIL,
	SEED_PASSWORD,
	_bearer_headers,
	_login_auditor,
	_signup_primary_manager,
)

SEED_ADMIN_EMAIL = "admin-demo@yee.local"

SUBMISSION_URL = f"/yee/audits/{YEE_SUBMISSION_HUB_ID}"


def _login(client: TestClient, email: str) -> dict[str, str]:
	response = client.post("/yee/auth/login", json={"email": email, "password": SEED_PASSWORD})
	assert response.status_code == 200, response.text
	return _bearer_headers(response.json()["access_token"])


def test_same_org_manager_can_read_submission_detail(yee_client: TestClient) -> None:
	"""A manager reads any submission in their org's projects — no auditor profile needed."""

	headers = _login(yee_client, SEED_MANAGER_EMAIL)
	response = yee_client.get(SUBMISSION_URL, headers=headers)
	assert response.status_code == 200, response.text

	body = response.json()
	assert body["id"] == str(YEE_SUBMISSION_HUB_ID)
	assert body["place_id"] == str(YEE_PLACE_HUB_ID)
	assert body["place_name"] is not None
	assert body["auditor_id"] is not None
	assert body["auditor_generated_id"] is not None
	assert body["submitted_at"] is not None
	assert isinstance(body["participant_info"], dict)
	assert isinstance(body["responses"], dict)
	score = body["score"]
	assert isinstance(score["total_score"], int)
	assert isinstance(score["section_scores"], dict)
	assert isinstance(score["canonical_score"], dict)
	assert score["total_raw_score"] == score["canonical_score"]["raw"]["total_score"]
	assert score["total_raw_maximum"] == 125
	assert score["total_weighted_score"] == score["canonical_score"]["weighted"]["total_weighted_score"]
	assert score["total_weighted_maximum"] > 0
	assert score["raw_domain_scores"] == score["canonical_score"]["raw"]["domain_scores"]
	assert score["weighted_domain_scores"] == score["canonical_score"]["weighted"]["weighted_domain_scores"]


def test_foreign_org_manager_gets_403(yee_client, yee_test_session_factory) -> None:
	"""A manager from another organization cannot read the submission."""

	headers = _signup_primary_manager(yee_client, yee_test_session_factory)["headers"]
	response = yee_client.get(SUBMISSION_URL, headers=headers)
	assert response.status_code == 403, response.text
	assert "access" in response.json()["detail"].lower()


def test_non_owner_auditor_still_gets_403(yee_client: TestClient) -> None:
	"""Regression pin: auditors stay owner-gated on the shared detail route."""

	headers = _bearer_headers(_login_auditor(yee_client, email=SEED_AUDITOR_THREE_EMAIL))
	response = yee_client.get(SUBMISSION_URL, headers=headers)
	assert response.status_code == 403, response.text


def test_admin_can_read_submission_detail(yee_client: TestClient) -> None:
	"""Regression pin: admins keep unrestricted read access."""

	headers = _login(yee_client, SEED_ADMIN_EMAIL)
	response = yee_client.get(SUBMISSION_URL, headers=headers)
	assert response.status_code == 200, response.text
	assert response.json()["id"] == str(YEE_SUBMISSION_HUB_ID)


def test_dual_role_manager_can_read_own_submission(yee_client: TestClient) -> None:
	"""A manager with a self auditor profile reads their own submission (owner path)."""

	headers = _login(yee_client, SEED_MANAGER_EMAIL)

	# Idempotent: returns the existing profile when one was already created.
	profile = yee_client.post("/yee/dashboard/my-auditor-profile", headers=headers)
	assert profile.status_code == 201, profile.text
	auditor_profile_id = profile.json()["id"]

	assignment = yee_client.post(
		"/yee/dashboard/assignments",
		headers=headers,
		json={
			"project_id": str(YEE_PROJECT_CORE_ID),
			"auditor_ids": [auditor_profile_id],
			"place_ids": [str(YEE_PLACE_HUB_ID)],
		},
	)
	assert assignment.status_code in (200, 201), assignment.text

	submit = yee_client.post(
		"/yee/audits",
		headers=headers,
		json={
			"place_id": str(YEE_PLACE_HUB_ID),
			"participant_info": {"total_minutes": 9},
			"responses": {"QID22": "3"},
		},
	)
	assert submit.status_code == 201, submit.text
	submission_id = submit.json()["id"]

	detail = yee_client.get(f"/yee/audits/{submission_id}", headers=headers)
	assert detail.status_code == 200, detail.text
	assert detail.json()["id"] == submission_id
	assert detail.json()["place_id"] == str(YEE_PLACE_HUB_ID)

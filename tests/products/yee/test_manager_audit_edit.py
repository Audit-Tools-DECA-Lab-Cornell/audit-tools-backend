"""Manager audit edit (FLOW P) -- zero prior coverage.

Tests for GET and PATCH /yee/dashboard/audits/{audit_id}/edit, which let a
manager (or admin) review and modify a submitted audit.

The edit route reads from the ``Audit`` table joined to
``YeeAuditSubmission``. It does NOT require a live submission row -- if the
Audit is SUBMITTED but the submission is missing, ``_repair_missing_yee_submission``
creates one on the fly. Seeded SUBMITTED audits now have matching submission
rows (seed fix), so we use the seeded audit directly.

Audit used: ``YEE_AUDIT_HUB_ID`` (bbbb...bbb1) -- AUD001's SUBMITTED Hub
audit, belonging to Baseline project under account ``1111...1111``. The seeded
manager ``manager-demo@yee.local`` owns that account and can access it.

ManagerAuditEditState fields (from ``app/products/yee/schemas/dashboard.py``):
    audit_id, submission_id, place_id, place_name, auditor_id,
    auditor_generated_id, submitted_at, participant_info, responses,
    score (DashboardScoreResult with total_score, section_scores,
           category_scores, matched_scored_answers)

ManagerAuditEditRequest fields:
    submission_id (optional), participant_info, responses, resubmit (bool)

Authorization:
    _require_manager_or_admin(user) gates both routes -> 403 for AUDITOR.
    The service then checks project.account_id == manager_account_id (or admin).
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.seed import YEE_AUDIT_HUB_ID
from tests.products.yee._helpers import (
	SEED_AUDITOR_EMAIL,
	SEED_MANAGER_EMAIL,
	SEED_PASSWORD,
	_bearer_headers,
	_login_auditor,
)

EDIT_URL = f"/yee/dashboard/audits/{YEE_AUDIT_HUB_ID}/edit"


def _login_manager(client: TestClient) -> str:
	"""Login the seeded manager and return the access token."""
	resp = client.post(
		"/yee/auth/login",
		json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD},
	)
	assert resp.status_code == 200, resp.text
	return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# GET /yee/dashboard/audits/{audit_id}/edit
# ---------------------------------------------------------------------------


def test_get_edit_state_as_manager_returns_200(yee_client: TestClient) -> None:
	"""GET edit state for a seeded SUBMITTED audit as the owning manager -> 200.

	Asserts the full ManagerAuditEditState shape.
	"""

	headers = _bearer_headers(_login_manager(yee_client))
	resp = yee_client.get(EDIT_URL, headers=headers)
	assert resp.status_code == 200, resp.text

	body = resp.json()
	# Required top-level fields from ManagerAuditEditState
	assert body["audit_id"] == str(YEE_AUDIT_HUB_ID)
	assert body["place_id"] is not None
	assert body["place_name"] is not None
	assert body["auditor_id"] is not None
	assert body["auditor_generated_id"] is not None
	assert body["submitted_at"] is not None
	assert isinstance(body["participant_info"], dict)
	assert isinstance(body["responses"], dict)

	# submission_id should be present for a SUBMITTED audit with a submission row
	assert body["submission_id"] is not None

	# DashboardScoreResult nested object
	score = body["score"]
	assert isinstance(score["total_score"], int)
	assert isinstance(score["section_scores"], dict)
	assert isinstance(score["category_scores"], dict)
	assert isinstance(score["matched_scored_answers"], int)


def test_get_edit_state_as_auditor_returns_403(yee_client: TestClient) -> None:
	"""GET edit state as AUDITOR -> 403 (manager or admin access required)."""

	headers = _bearer_headers(_login_auditor(yee_client))
	resp = yee_client.get(EDIT_URL, headers=headers)
	assert resp.status_code == 403, resp.text
	assert "manager or admin" in resp.json()["detail"].lower()


def test_get_edit_state_unknown_audit_returns_404(yee_client: TestClient) -> None:
	"""GET edit state for a non-existent audit ID -> 404."""

	headers = _bearer_headers(_login_manager(yee_client))
	fake_id = uuid.uuid4()
	resp = yee_client.get(
		f"/yee/dashboard/audits/{fake_id}/edit",
		headers=headers,
	)
	assert resp.status_code == 404, resp.text
	assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# PATCH /yee/dashboard/audits/{audit_id}/edit
# ---------------------------------------------------------------------------


def test_patch_edit_state_as_manager_updates_responses(yee_client: TestClient) -> None:
	"""PATCH edit state as manager -> 200 and the change is reflected on GET.

	We send a new ``responses`` dict and ``participant_info``, then GET the
	edit state again to verify the update took effect.
	"""

	headers = _bearer_headers(_login_manager(yee_client))

	# First, GET the current state to capture the submission_id.
	get_resp = yee_client.get(EDIT_URL, headers=headers)
	assert get_resp.status_code == 200, get_resp.text
	current_state = get_resp.json()
	submission_id = current_state["submission_id"]

	# PATCH with updated responses
	updated_responses = {"QID22": "2"}
	updated_participant_info = {"total_minutes": 99, "visit_frequency": "once-or-twice-a-week"}
	patch_resp = yee_client.patch(
		EDIT_URL,
		headers=headers,
		json={
			"submission_id": submission_id,
			"participant_info": updated_participant_info,
			"responses": updated_responses,
			"resubmit": False,
		},
	)
	assert patch_resp.status_code == 200, patch_resp.text
	patched = patch_resp.json()

	# The response reflects the update.
	assert patched["responses"] == updated_responses
	assert patched["participant_info"]["total_minutes"] == 99

	# DashboardScoreResult is recomputed from the new responses.
	assert isinstance(patched["score"]["total_score"], int)
	assert isinstance(patched["score"]["matched_scored_answers"], int)

	# A subsequent GET confirms persistence.
	verify_resp = yee_client.get(EDIT_URL, headers=headers)
	assert verify_resp.status_code == 200, verify_resp.text
	verified = verify_resp.json()
	assert verified["responses"] == updated_responses
	assert verified["participant_info"]["total_minutes"] == 99


def test_patch_edit_state_as_auditor_returns_403(yee_client: TestClient) -> None:
	"""PATCH edit state as AUDITOR -> 403."""

	headers = _bearer_headers(_login_auditor(yee_client))
	resp = yee_client.patch(
		EDIT_URL,
		headers=headers,
		json={
			"participant_info": {},
			"responses": {"QID22": "1"},
		},
	)
	assert resp.status_code == 403, resp.text
	assert "manager or admin" in resp.json()["detail"].lower()


def test_patch_edit_state_unknown_audit_returns_404(yee_client: TestClient) -> None:
	"""PATCH edit state for a non-existent audit -> 404."""

	headers = _bearer_headers(_login_manager(yee_client))
	fake_id = uuid.uuid4()
	resp = yee_client.patch(
		f"/yee/dashboard/audits/{fake_id}/edit",
		headers=headers,
		json={
			"participant_info": {},
			"responses": {"QID22": "1"},
		},
	)
	assert resp.status_code == 404, resp.text
	assert "not found" in resp.json()["detail"].lower()

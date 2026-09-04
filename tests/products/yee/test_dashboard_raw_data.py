"""YEE dashboard raw-data export integration tests (Flow R).

Covers GET /yee/dashboard/raw-data with manager, admin, auditor, and
unauthenticated callers.  Validates the bare-array response shape against
the real RawDataExportRow model from ``app.products.yee.schemas.dashboard``.

NOTE: No notify-ready or export-format routes exist in the current source
(``dashboard_router.py``).  Those are skipped and noted here.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from tests.products.yee._helpers import (
	SEED_MANAGER_EMAIL,
	SEED_PASSWORD,
	_bearer_headers,
	_login_auditor,
)

SEED_ADMIN_EMAIL = "admin-demo@yee.local"

RAW_DATA_URL = "/yee/dashboard/raw-data"

# Every field from the real RawDataExportRow pydantic model.
_RAW_DATA_FIELDS = {
	"audit_id",
	"auditor_generated_id",
	"organization",
	"place_id",
	"place_name",
	"project_id",
	"project_name",
	"date",
	"submitted_at",
	"start_time",
	"finish_time",
	"total_minutes",
	"visit_frequency",
	"season",
	"weather",
	"comments",
	"raw_access",
	"raw_activity_spaces",
	"raw_amenities",
	"raw_experience_of_space",
	"raw_aesthetics_and_care",
	"raw_use_and_usability",
	"weighted_access",
	"weighted_activity_spaces",
	"weighted_amenities",
	"weighted_experience_of_space",
	"weighted_aesthetics_and_care",
	"weighted_use_and_usability",
	"total_raw_score",
	"total_raw_maximum",
	"total_weighted_score",
	"total_weighted_maximum",
	"domain_weights",
	"canonical_score",
	"responses",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login_manager(client: TestClient) -> str:
	resp = client.post("/yee/auth/login", json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD})
	assert resp.status_code == 200, resp.text
	return resp.json()["access_token"]


def _login_admin(client: TestClient) -> str:
	resp = client.post("/yee/auth/login", json={"email": SEED_ADMIN_EMAIL, "password": SEED_PASSWORD})
	assert resp.status_code == 200, resp.text
	return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# GET /yee/dashboard/raw-data — happy paths
# ---------------------------------------------------------------------------


def test_manager_can_list_raw_data(yee_client: TestClient) -> None:
	"""Manager gets 200 with a bare array of RawDataExportRow items."""

	token = _login_manager(yee_client)
	resp = yee_client.get(RAW_DATA_URL, headers=_bearer_headers(token))
	assert resp.status_code == 200, resp.text

	data = resp.json()
	assert isinstance(data, list)

	for row in data:
		assert _RAW_DATA_FIELDS.issubset(row.keys()), f"Missing keys in row: {_RAW_DATA_FIELDS - row.keys()}"
		# Type spot-checks on key fields
		assert isinstance(row["audit_id"], str)
		assert isinstance(row["total_raw_score"], int)
		assert row["total_raw_maximum"] == 122
		assert isinstance(row["total_weighted_score"], (int, float))
		assert isinstance(row["total_weighted_maximum"], (int, float))
		assert isinstance(row["domain_weights"], dict)
		assert isinstance(row["responses"], dict)
		assert isinstance(row["total_minutes"], int)


def test_admin_can_list_raw_data(yee_client: TestClient) -> None:
	"""Admin also gets 200 on raw-data export."""

	token = _login_admin(yee_client)
	resp = yee_client.get(RAW_DATA_URL, headers=_bearer_headers(token))
	assert resp.status_code == 200, resp.text

	data = resp.json()
	assert isinstance(data, list)
	for row in data:
		assert "audit_id" in row
		assert "place_name" in row
		assert "project_name" in row


def test_manager_raw_data_contains_seeded_submitted_audits(yee_client: TestClient) -> None:
	"""Raw data export includes rows for the seeded submitted audits.

	The seed has 3 submitted audits, so we expect at least some rows.
	Each row should have meaningful score and metadata fields.
	"""

	token = _login_manager(yee_client)
	resp = yee_client.get(RAW_DATA_URL, headers=_bearer_headers(token))
	assert resp.status_code == 200, resp.text

	data = resp.json()
	# At least 1 row should exist from the seeded submitted audits
	assert len(data) >= 1, "Expected at least one raw data export row from seeded audits"

	# Verify domain score fields are populated integers on a sample row
	sample = data[0]
	for domain_field in [
		"raw_access",
		"raw_activity_spaces",
		"raw_amenities",
		"raw_experience_of_space",
		"raw_aesthetics_and_care",
		"raw_use_and_usability",
	]:
		assert isinstance(sample[domain_field], int), f"{domain_field} should be int"

	for weighted_field in [
		"weighted_access",
		"weighted_activity_spaces",
		"weighted_amenities",
		"weighted_experience_of_space",
		"weighted_aesthetics_and_care",
		"weighted_use_and_usability",
	]:
		assert isinstance(sample[weighted_field], (int, float)), f"{weighted_field} should be numeric"


# ---------------------------------------------------------------------------
# Authz: auditor is forbidden
# ---------------------------------------------------------------------------


def test_auditor_cannot_access_raw_data(yee_client: TestClient) -> None:
	"""Auditor gets 403 on raw-data export (manager/admin only)."""

	token = _login_auditor(yee_client)
	resp = yee_client.get(RAW_DATA_URL, headers=_bearer_headers(token))
	assert resp.status_code == 403, resp.text
	assert "Manager or admin access is required" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Authz: unauthenticated
# ---------------------------------------------------------------------------


def test_unauthenticated_cannot_access_raw_data(yee_client: TestClient) -> None:
	"""No bearer token -> 401."""

	resp = yee_client.get(RAW_DATA_URL)
	assert resp.status_code == 401, resp.text

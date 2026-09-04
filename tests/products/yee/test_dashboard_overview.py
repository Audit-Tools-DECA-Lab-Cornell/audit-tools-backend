"""YEE dashboard overview integration tests (Flow U).

Covers GET /yee/dashboard/overview with manager, admin, auditor, and
unauthenticated callers.  Validates the DashboardOverviewResponse shape
including metrics[], recent_activity[], latest_audits[], and
organization_summaries[].  Also asserts AuditListItem shape on
latest_audits entries.
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

OVERVIEW_URL = "/yee/dashboard/overview"

# DashboardMetricResponse fields
_METRIC_FIELDS = {"title", "value", "description", "trend"}

# AuditListItem fields (from dashboard_router.py)
_AUDIT_LIST_ITEM_FIELDS = {
	"id",
	"submission_id",
	"project_id",
	"project_name",
	"place_id",
	"place",
	"auditor",
	"date",
	"submitted_at",
	"score",
	"total_raw_score",
	"total_raw_maximum",
	"total_weighted_score",
	"total_weighted_maximum",
	"domain_weights",
	"status",
}

# OrganizationSummaryItem fields
_ORG_SUMMARY_FIELDS = {"organization", "users", "projects", "places", "audits"}


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
# GET /yee/dashboard/overview — manager happy path
# ---------------------------------------------------------------------------


def test_manager_can_get_overview(yee_client: TestClient) -> None:
	"""Manager gets 200 with the full DashboardOverviewResponse shape."""

	token = _login_manager(yee_client)
	resp = yee_client.get(OVERVIEW_URL, headers=_bearer_headers(token))
	assert resp.status_code == 200, resp.text

	data = resp.json()

	# Top-level keys
	assert "metrics" in data
	assert "recent_activity" in data
	assert "latest_audits" in data
	assert "organization_summaries" in data

	# metrics is a list of DashboardMetricResponse
	assert isinstance(data["metrics"], list)
	assert len(data["metrics"]) == 4, "Overview should return exactly 4 metric cards"
	for metric in data["metrics"]:
		assert _METRIC_FIELDS.issubset(metric.keys()), f"Missing metric keys: {_METRIC_FIELDS - metric.keys()}"
		assert isinstance(metric["title"], str)
		assert isinstance(metric["value"], str)

	# Verify the 4 expected metric titles
	titles = {m["title"] for m in data["metrics"]}
	assert titles == {"Projects", "Places", "Auditors", "Completed Audits"}

	# recent_activity is a list of strings
	assert isinstance(data["recent_activity"], list)
	for activity in data["recent_activity"]:
		assert isinstance(activity, str)

	# latest_audits is a list of AuditListItem
	assert isinstance(data["latest_audits"], list)

	# organization_summaries is a list (empty for managers, populated for admins)
	assert isinstance(data["organization_summaries"], list)


def test_manager_overview_latest_audits_shape(yee_client: TestClient) -> None:
	"""Each item in latest_audits matches the AuditListItem model."""

	token = _login_manager(yee_client)
	resp = yee_client.get(OVERVIEW_URL, headers=_bearer_headers(token))
	assert resp.status_code == 200, resp.text

	audits = resp.json()["latest_audits"]
	for audit_item in audits:
		assert _AUDIT_LIST_ITEM_FIELDS.issubset(audit_item.keys()), (
			f"Missing AuditListItem keys: {_AUDIT_LIST_ITEM_FIELDS - audit_item.keys()}"
		)
		assert isinstance(audit_item["id"], str)
		assert isinstance(audit_item["project_id"], str)
		assert isinstance(audit_item["project_name"], str)
		assert isinstance(audit_item["place_id"], str)
		assert isinstance(audit_item["place"], str)
		assert isinstance(audit_item["auditor"], str)
		assert isinstance(audit_item["date"], str)
		assert isinstance(audit_item["score"], int)
		assert isinstance(audit_item["total_raw_score"], int)
		assert isinstance(audit_item["total_weighted_score"], (int, float))
		assert isinstance(audit_item["domain_weights"], dict)
		assert audit_item["status"] in {"Submitted", "Draft"}
		if audit_item["status"] == "Submitted":
			assert audit_item["total_raw_maximum"] == 122
			assert isinstance(audit_item["total_weighted_maximum"], (int, float))


def test_manager_overview_summaries_empty(yee_client: TestClient) -> None:
	"""Manager overview returns empty organization_summaries (admin-only feature)."""

	token = _login_manager(yee_client)
	resp = yee_client.get(OVERVIEW_URL, headers=_bearer_headers(token))
	assert resp.status_code == 200, resp.text

	data = resp.json()
	assert data["organization_summaries"] == []


# ---------------------------------------------------------------------------
# GET /yee/dashboard/overview — admin happy path
# ---------------------------------------------------------------------------


def test_admin_can_get_overview(yee_client: TestClient) -> None:
	"""Admin gets 200 and sees organization_summaries populated."""

	token = _login_admin(yee_client)
	resp = yee_client.get(OVERVIEW_URL, headers=_bearer_headers(token))
	assert resp.status_code == 200, resp.text

	data = resp.json()
	assert isinstance(data["metrics"], list)
	assert len(data["metrics"]) == 4
	assert isinstance(data["latest_audits"], list)

	# Admin sees organization summaries
	summaries = data["organization_summaries"]
	assert isinstance(summaries, list)
	# The seed has at least 1 account, so admin should see at least 1 summary
	assert len(summaries) >= 1, "Admin overview should include organization summaries"

	for summary in summaries:
		assert _ORG_SUMMARY_FIELDS.issubset(summary.keys()), (
			f"Missing org summary keys: {_ORG_SUMMARY_FIELDS - summary.keys()}"
		)
		assert isinstance(summary["organization"], str)
		assert isinstance(summary["users"], int)
		assert isinstance(summary["projects"], int)
		assert isinstance(summary["places"], int)
		assert isinstance(summary["audits"], int)


# ---------------------------------------------------------------------------
# Authz: auditor is forbidden
# ---------------------------------------------------------------------------


def test_auditor_cannot_access_overview(yee_client: TestClient) -> None:
	"""Auditor gets 403 on dashboard overview (manager/admin only)."""

	token = _login_auditor(yee_client)
	resp = yee_client.get(OVERVIEW_URL, headers=_bearer_headers(token))
	assert resp.status_code == 403, resp.text
	assert "Manager or admin access is required" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Authz: unauthenticated
# ---------------------------------------------------------------------------


def test_unauthenticated_cannot_access_overview(yee_client: TestClient) -> None:
	"""No bearer token -> 401."""

	resp = yee_client.get(OVERVIEW_URL)
	assert resp.status_code == 401, resp.text

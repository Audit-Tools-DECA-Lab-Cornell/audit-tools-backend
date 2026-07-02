"""YEE dashboard reports integration tests (Flow Q).

Covers GET /yee/dashboard/reports/place-comparisons with manager, admin,
auditor, and unauthenticated callers.  Validates the bare-array response
shape against the real PlaceComparisonGroup / PlaceComparisonAuditItem
models from ``app.products.yee.schemas.dashboard``.

Additional report routes (final comments, question notes, report detail)
do NOT exist in the current source — they are noted as absent, not tested.
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

PLACE_COMPARISONS_URL = "/yee/dashboard/reports/place-comparisons"

# Expected fields on each PlaceComparisonAuditItem (from the schema).
_AUDIT_ITEM_FIELDS = {
	"audit_id",
	"auditor_id",
	"place_id",
	"place_name",
	"project_id",
	"project_name",
	"date",
	"total_raw_score",
	"total_weighted_score",
	"domain_weights",
	"raw_domain_scores",
	"weighted_domain_scores",
	"canonical_score",
}

_GROUP_FIELDS = {"place_id", "place_name", "project_id", "project_name", "audits"}


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
# GET /yee/dashboard/reports/place-comparisons — happy paths
# ---------------------------------------------------------------------------


def test_manager_can_list_place_comparisons(yee_client: TestClient) -> None:
	"""Manager gets 200 with a bare array of PlaceComparisonGroup items."""

	token = _login_manager(yee_client)
	resp = yee_client.get(PLACE_COMPARISONS_URL, headers=_bearer_headers(token))
	assert resp.status_code == 200, resp.text

	data = resp.json()
	assert isinstance(data, list)

	# Validate group shape on every returned item
	for group in data:
		assert _GROUP_FIELDS.issubset(group.keys()), f"Missing keys in group: {_GROUP_FIELDS - group.keys()}"
		assert isinstance(group["audits"], list)

		# If there are nested audit items, validate their shape
		for audit_item in group["audits"]:
			assert _AUDIT_ITEM_FIELDS.issubset(audit_item.keys()), (
				f"Missing keys in audit item: {_AUDIT_ITEM_FIELDS - audit_item.keys()}"
			)
			assert isinstance(audit_item["total_raw_score"], int)
			assert isinstance(audit_item["total_weighted_score"], (int, float))
			assert isinstance(audit_item["domain_weights"], dict)
			assert isinstance(audit_item["raw_domain_scores"], dict)
			assert isinstance(audit_item["weighted_domain_scores"], dict)


def test_admin_can_list_place_comparisons(yee_client: TestClient) -> None:
	"""Admin also gets 200 on place-comparisons."""

	token = _login_admin(yee_client)
	resp = yee_client.get(PLACE_COMPARISONS_URL, headers=_bearer_headers(token))
	assert resp.status_code == 200, resp.text

	data = resp.json()
	assert isinstance(data, list)
	# Admin sees all orgs, so the list should be at least as large as manager's
	for group in data:
		assert "place_id" in group
		assert "audits" in group


def test_manager_place_comparisons_contain_seeded_submitted_audits(yee_client: TestClient) -> None:
	"""Manager response includes groups for places with submitted audits.

	The seed has 3 submitted audits across different places, so we expect
	at least some groups with non-empty audit lists.
	"""

	token = _login_manager(yee_client)
	resp = yee_client.get(PLACE_COMPARISONS_URL, headers=_bearer_headers(token))
	assert resp.status_code == 200, resp.text

	data = resp.json()
	groups_with_audits = [g for g in data if len(g["audits"]) > 0]
	# The seed has at least 3 submitted audits, so at least 1 group should have audits
	assert len(groups_with_audits) >= 1, "Expected at least one group with submitted audit items"

	# Verify score fields on a real audit item
	sample_item = groups_with_audits[0]["audits"][0]
	assert sample_item["total_raw_score"] >= 0
	assert isinstance(sample_item["date"], str)
	assert len(sample_item["date"]) > 0


# ---------------------------------------------------------------------------
# Authz: auditor is forbidden
# ---------------------------------------------------------------------------


def test_auditor_cannot_access_place_comparisons(yee_client: TestClient) -> None:
	"""Auditor gets 403 on manager/admin report routes (cross-auditor isolation)."""

	token = _login_auditor(yee_client)
	resp = yee_client.get(PLACE_COMPARISONS_URL, headers=_bearer_headers(token))
	assert resp.status_code == 403, resp.text
	assert "Manager or admin access is required" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# Authz: unauthenticated
# ---------------------------------------------------------------------------


def test_unauthenticated_cannot_access_place_comparisons(yee_client: TestClient) -> None:
	"""No bearer token -> 401."""

	resp = yee_client.get(PLACE_COMPARISONS_URL)
	assert resp.status_code == 401, resp.text

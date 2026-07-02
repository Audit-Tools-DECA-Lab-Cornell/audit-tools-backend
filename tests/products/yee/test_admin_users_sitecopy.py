"""YEE admin users and site-copy integration tests (Flow T).

Covers:
- GET ``/yee/dashboard/users`` — ADMIN-only user list (bare array of
  ``UserListItem`` with ``role`` field). MANAGER 403, AUDITOR 403,
  unauthenticated 401.
- Site-copy routes under ``/yee/admin/site-copy`` and ``/yee/site-copy``:
  public GET (returns dict), admin list (GET, bare array of
  ``SiteCopyVersionResponse``), admin create (POST 201), admin update
  (PATCH). All admin site-copy routes use ``_require_admin`` (ADMIN only).

Note: there is NO DELETE route for site-copy — only instruments have DELETE.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.products.yee._helpers import (
	SEED_MANAGER_EMAIL,
	SEED_PASSWORD,
	_bearer_headers,
	_login_auditor,
	_unique_suffix,
)

SEED_ADMIN_EMAIL = "admin-demo@yee.local"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _login_admin(client: TestClient) -> str:
	"""Login the seeded admin and return a bearer token."""
	resp = client.post(
		"/yee/auth/login",
		json={"email": SEED_ADMIN_EMAIL, "password": SEED_PASSWORD},
	)
	assert resp.status_code == 200, resp.text
	return resp.json()["access_token"]


def _login_manager(client: TestClient) -> str:
	"""Login the seeded manager and return a bearer token."""
	resp = client.post(
		"/yee/auth/login",
		json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD},
	)
	assert resp.status_code == 200, resp.text
	return resp.json()["access_token"]


# ===========================================================================
# GET /yee/dashboard/users — admin-only user list
# ===========================================================================


def test_admin_can_list_users(yee_client: TestClient) -> None:
	"""Admin sees all users as a bare JSON array of UserListItem objects."""

	token = _login_admin(yee_client)
	resp = yee_client.get(
		"/yee/dashboard/users",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 200, resp.text
	data = resp.json()
	assert isinstance(data, list)
	assert len(data) >= 7  # at least the 7 seeded users

	# Validate UserListItem shape on each item
	required_fields = {
		"id",
		"name",
		"email",
		"role",
		"organization",
		"status",
		"approved",
		"email_verified",
		"profile_completed",
		"contact_info",
		"project_assignments",
	}
	first = data[0]
	for field in required_fields:
		assert field in first, f"Missing field {field} in UserListItem"

	# The 'role' field must be a valid AccountType value
	valid_roles = {"ADMIN", "MANAGER", "AUDITOR"}
	for item in data:
		assert item["role"] in valid_roles, f"Unexpected role: {item['role']}"

	# The admin user itself should be in the list
	admin_items = [u for u in data if u["role"] == "ADMIN"]
	assert len(admin_items) >= 1

	# Check that account_id field is present (nullable)
	assert "account_id" in first


def test_admin_user_list_contains_seeded_roles(yee_client: TestClient) -> None:
	"""The user list includes MANAGER, ADMIN, and AUDITOR role users."""

	token = _login_admin(yee_client)
	resp = yee_client.get(
		"/yee/dashboard/users",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 200, resp.text
	data = resp.json()
	roles_present = {item["role"] for item in data}
	assert "ADMIN" in roles_present
	assert "MANAGER" in roles_present
	assert "AUDITOR" in roles_present


def test_admin_user_list_masks_auditor_email(yee_client: TestClient) -> None:
	"""Admin view masks auditor email (set to empty string) and uses auditor code for name."""

	token = _login_admin(yee_client)
	resp = yee_client.get(
		"/yee/dashboard/users",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 200, resp.text
	data = resp.json()
	auditor_items = [u for u in data if u["role"] == "AUDITOR"]
	assert len(auditor_items) >= 1
	for auditor in auditor_items:
		# Admin view masks auditor email to empty string
		assert auditor["email"] == ""
		# Name should be auditor code (AUD-xxx format)
		assert auditor["name"].startswith("AUD-") or auditor["name"].startswith("AUD")


def test_manager_cannot_list_users(yee_client: TestClient) -> None:
	"""MANAGER gets 403 on admin user list (ADMIN-only ``_require_admin``)."""

	token = _login_manager(yee_client)
	resp = yee_client.get(
		"/yee/dashboard/users",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 403, resp.text
	assert "Admin access is required" in resp.json()["detail"]


def test_auditor_cannot_list_users(yee_client: TestClient) -> None:
	"""AUDITOR gets 403 on admin user list (ADMIN-only ``_require_admin``)."""

	token = _login_auditor(yee_client)
	resp = yee_client.get(
		"/yee/dashboard/users",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 403, resp.text
	assert "Admin access is required" in resp.json()["detail"]


def test_unauthenticated_cannot_list_users(yee_client: TestClient) -> None:
	"""No token -> 401 on admin user list."""

	resp = yee_client.get("/yee/dashboard/users")
	assert resp.status_code == 401, resp.text


# ===========================================================================
# GET /yee/site-copy — public site-copy read
# ===========================================================================


def test_public_site_copy_returns_dict(yee_client: TestClient) -> None:
	"""Public GET /yee/site-copy returns a dict (possibly empty if none seeded)."""

	resp = yee_client.get("/yee/site-copy")
	assert resp.status_code == 200, resp.text
	data = resp.json()
	assert isinstance(data, dict)


# ===========================================================================
# GET /yee/admin/site-copy — admin list
# ===========================================================================


def test_admin_can_list_site_copy_versions(yee_client: TestClient) -> None:
	"""Admin sees site-copy versions as a bare JSON array."""

	token = _login_admin(yee_client)
	resp = yee_client.get(
		"/yee/admin/site-copy",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 200, resp.text
	data = resp.json()
	assert isinstance(data, list)
	# May be empty if no site-copy has been created yet
	if len(data) > 0:
		first = data[0]
		for field in ("id", "instrument_key", "instrument_version", "is_active", "content", "created_at", "updated_at"):
			assert field in first, f"Missing field {field} in SiteCopyVersionResponse"


def test_manager_cannot_list_site_copy_versions(yee_client: TestClient) -> None:
	"""MANAGER gets 403 on admin site-copy list (ADMIN-only route)."""

	token = _login_manager(yee_client)
	resp = yee_client.get(
		"/yee/admin/site-copy",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 403, resp.text
	assert "Admin access is required" in resp.json()["detail"]


def test_auditor_cannot_list_site_copy_versions(yee_client: TestClient) -> None:
	"""AUDITOR gets 403 on admin site-copy list (ADMIN-only route)."""

	token = _login_auditor(yee_client)
	resp = yee_client.get(
		"/yee/admin/site-copy",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 403, resp.text
	assert "Admin access is required" in resp.json()["detail"]


def test_unauthenticated_cannot_list_site_copy_versions(yee_client: TestClient) -> None:
	"""No token -> 401 on admin site-copy list."""

	resp = yee_client.get("/yee/admin/site-copy")
	assert resp.status_code == 401, resp.text


# ===========================================================================
# POST /yee/admin/site-copy — create version (201)
# ===========================================================================


def test_admin_can_create_site_copy_version(yee_client: TestClient) -> None:
	"""Admin can create a site-copy version with real fields (201)."""

	token = _login_admin(yee_client)
	suffix = _unique_suffix()
	content = {
		"hero_title": f"Welcome to YEE {suffix}",
		"hero_subtitle": "Youth Enabling Environments",
		"footer_text": "All rights reserved.",
	}
	resp = yee_client.post(
		"/yee/admin/site-copy",
		params={"activate": "true"},
		headers=_bearer_headers(token),
		json={
			"instrument_version": f"copy-v-{suffix}",
			"content": content,
		},
	)
	assert resp.status_code == 201, resp.text
	data = resp.json()
	assert data["instrument_version"] == f"copy-v-{suffix}"
	assert data["is_active"] is True
	assert data["instrument_key"] == "yee_site_copy"
	assert data["content"] == content
	for field in ("id", "created_at", "updated_at"):
		assert field in data

	# After creating, public GET should return this content
	pub_resp = yee_client.get("/yee/site-copy")
	assert pub_resp.status_code == 200
	assert pub_resp.json() == content


def test_admin_can_create_inactive_site_copy(yee_client: TestClient) -> None:
	"""Admin can create a site-copy version with activate=False."""

	token = _login_admin(yee_client)
	suffix = _unique_suffix()
	resp = yee_client.post(
		"/yee/admin/site-copy",
		params={"activate": "false"},
		headers=_bearer_headers(token),
		json={
			"instrument_version": f"copy-inactive-{suffix}",
			"content": {"draft": True},
		},
	)
	assert resp.status_code == 201, resp.text
	data = resp.json()
	assert data["is_active"] is False


def test_manager_cannot_create_site_copy(yee_client: TestClient) -> None:
	"""MANAGER gets 403 creating site-copy (ADMIN-only)."""

	token = _login_manager(yee_client)
	resp = yee_client.post(
		"/yee/admin/site-copy",
		headers=_bearer_headers(token),
		json={
			"instrument_version": "blocked",
			"content": {},
		},
	)
	assert resp.status_code == 403, resp.text
	assert "Admin access is required" in resp.json()["detail"]


# ===========================================================================
# PATCH /yee/admin/site-copy/{copy_id} — update / activate
# ===========================================================================


def test_admin_can_update_site_copy_status(yee_client: TestClient) -> None:
	"""Admin can PATCH a site-copy version to change its active status."""

	token = _login_admin(yee_client)
	suffix = _unique_suffix()

	# Create inactive
	create_resp = yee_client.post(
		"/yee/admin/site-copy",
		params={"activate": "false"},
		headers=_bearer_headers(token),
		json={
			"instrument_version": f"copy-patch-{suffix}",
			"content": {"patched": True},
		},
	)
	assert create_resp.status_code == 201, create_resp.text
	copy_id = create_resp.json()["id"]

	# Activate via PATCH
	patch_resp = yee_client.patch(
		f"/yee/admin/site-copy/{copy_id}",
		headers=_bearer_headers(token),
		json={"is_active": True},
	)
	assert patch_resp.status_code == 200, patch_resp.text
	assert patch_resp.json()["is_active"] is True
	assert patch_resp.json()["id"] == copy_id
	assert patch_resp.json()["instrument_key"] == "yee_site_copy"


def test_patch_nonexistent_site_copy_returns_404(yee_client: TestClient) -> None:
	"""PATCH on a non-existent site-copy ID returns 404."""

	token = _login_admin(yee_client)
	fake_id = uuid.uuid4()
	resp = yee_client.patch(
		f"/yee/admin/site-copy/{fake_id}",
		headers=_bearer_headers(token),
		json={"is_active": True},
	)
	assert resp.status_code == 404, resp.text
	assert "Site copy version not found" in resp.json()["detail"]


def test_manager_cannot_patch_site_copy(yee_client: TestClient) -> None:
	"""MANAGER gets 403 patching site-copy (ADMIN-only)."""

	token = _login_manager(yee_client)
	fake_id = uuid.uuid4()
	resp = yee_client.patch(
		f"/yee/admin/site-copy/{fake_id}",
		headers=_bearer_headers(token),
		json={"is_active": True},
	)
	assert resp.status_code == 403, resp.text
	assert "Admin access is required" in resp.json()["detail"]


def test_auditor_cannot_patch_site_copy(yee_client: TestClient) -> None:
	"""AUDITOR gets 403 patching site-copy (ADMIN-only)."""

	token = _login_auditor(yee_client)
	fake_id = uuid.uuid4()
	resp = yee_client.patch(
		f"/yee/admin/site-copy/{fake_id}",
		headers=_bearer_headers(token),
		json={"is_active": True},
	)
	assert resp.status_code == 403, resp.text
	assert "Admin access is required" in resp.json()["detail"]

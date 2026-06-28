"""YEE admin instrument version management integration tests (Flow S).

Covers the admin instrument routes under ``/yee/admin/instruments``:
list versions, create (POST 201), activate (PATCH), delete, and authz
gating (ADMIN-only via ``_require_admin``).

Key real-source semantics:
- All ``/yee/admin/instruments*`` routes use ``_require_admin`` (ADMIN only).
- DELETE returns **200** ``{"deleted": true, "instrument_id": "<uuid>"}``
  for inactive versions.
- DELETE of the **active** version raises **400**
  "The active instrument version cannot be deleted."
- DELETE of a non-existent ID raises **404** "Instrument not found".
- POST create returns **201** with ``YeeInstrumentVersionResponse``.
- PATCH activate returns 200 with ``YeeInstrumentVersionResponse``.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.seed import YEE_INSTRUMENT_ID
from tests.products.yee._helpers import (
	SEED_AUDITOR_EMAIL,
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


def _minimal_instrument_content() -> dict:
	"""Return the smallest valid YeeInstrumentResponse-shaped dict.

	``YeeInstrumentCreateRequest.content`` is validated through
	``YeeInstrumentResponse.model_validate`` in the route handler,
	so it must contain at minimum the required fields.
	"""
	return {
		"survey_name": "Test Instrument",
		"version": "test-1",
		"scoring_items": [],
	}


# ---------------------------------------------------------------------------
# GET /yee/admin/instruments — list versions
# ---------------------------------------------------------------------------


def test_admin_can_list_instrument_versions(yee_client: TestClient) -> None:
	"""Admin sees at least the seeded active instrument in the list."""

	token = _login_admin(yee_client)
	resp = yee_client.get(
		"/yee/admin/instruments",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 200, resp.text
	data = resp.json()
	assert isinstance(data, list)
	assert len(data) >= 1
	# Each item matches YeeInstrumentVersionResponse shape
	first = data[0]
	for field in ("id", "instrument_key", "instrument_version", "is_active", "content", "created_at", "updated_at"):
		assert field in first, f"Missing field {field} in response"
	# The seeded instrument should be present
	ids = [item["id"] for item in data]
	assert str(YEE_INSTRUMENT_ID) in ids


def test_manager_cannot_list_instrument_versions(yee_client: TestClient) -> None:
	"""MANAGER gets 403 on admin instrument list (ADMIN-only route)."""

	token = _login_manager(yee_client)
	resp = yee_client.get(
		"/yee/admin/instruments",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 403, resp.text
	assert "Admin access is required" in resp.json()["detail"]


def test_auditor_cannot_list_instrument_versions(yee_client: TestClient) -> None:
	"""AUDITOR gets 403 on admin instrument list (ADMIN-only route)."""

	token = _login_auditor(yee_client)
	resp = yee_client.get(
		"/yee/admin/instruments",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 403, resp.text
	assert "Admin access is required" in resp.json()["detail"]


def test_unauthenticated_cannot_list_instrument_versions(yee_client: TestClient) -> None:
	"""No token -> 401 on admin instrument list."""

	resp = yee_client.get("/yee/admin/instruments")
	assert resp.status_code == 401, resp.text


# ---------------------------------------------------------------------------
# POST /yee/admin/instruments — create draft version
# ---------------------------------------------------------------------------


def test_admin_can_create_instrument_version(yee_client: TestClient) -> None:
	"""Admin can create a new instrument version (201) with activate=False."""

	token = _login_admin(yee_client)
	suffix = _unique_suffix()
	resp = yee_client.post(
		"/yee/admin/instruments",
		params={"activate": "false"},
		headers=_bearer_headers(token),
		json={
			"instrument_key": "yee",
			"instrument_version": f"test-draft-{suffix}",
			"content": _minimal_instrument_content(),
		},
	)
	assert resp.status_code == 201, resp.text
	data = resp.json()
	assert data["instrument_version"] == f"test-draft-{suffix}"
	assert data["is_active"] is False
	assert data["instrument_key"] == "yee"
	for field in ("id", "content", "created_at", "updated_at"):
		assert field in data

	# Clean up: delete the draft so it does not pollute other tests
	draft_id = data["id"]
	del_resp = yee_client.delete(
		f"/yee/admin/instruments/{draft_id}",
		headers=_bearer_headers(token),
	)
	assert del_resp.status_code == 200, del_resp.text


def test_manager_cannot_create_instrument_version(yee_client: TestClient) -> None:
	"""MANAGER gets 403 creating an instrument (ADMIN-only)."""

	token = _login_manager(yee_client)
	resp = yee_client.post(
		"/yee/admin/instruments",
		headers=_bearer_headers(token),
		json={
			"instrument_key": "yee",
			"instrument_version": "blocked",
			"content": _minimal_instrument_content(),
		},
	)
	assert resp.status_code == 403, resp.text
	assert "Admin access is required" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# POST /yee/admin/instruments — create with activate=True (duplicate)
# ---------------------------------------------------------------------------


def test_admin_can_create_and_activate_instrument_version(yee_client: TestClient) -> None:
	"""Creating with activate=True deactivates the previous active version.

	After activation the new version has is_active=True. We then restore
	the original seeded instrument to active so downstream tests are unaffected.
	"""

	token = _login_admin(yee_client)
	suffix = _unique_suffix()

	# Create activated version
	resp = yee_client.post(
		"/yee/admin/instruments",
		params={"activate": "true"},
		headers=_bearer_headers(token),
		json={
			"instrument_key": "yee",
			"instrument_version": f"test-active-{suffix}",
			"content": _minimal_instrument_content(),
		},
	)
	assert resp.status_code == 201, resp.text
	data = resp.json()
	new_id = data["id"]
	assert data["is_active"] is True

	# Restore seeded instrument to active
	restore = yee_client.patch(
		f"/yee/admin/instruments/{YEE_INSTRUMENT_ID}",
		headers=_bearer_headers(token),
		json={"is_active": True},
	)
	assert restore.status_code == 200, restore.text
	assert restore.json()["is_active"] is True

	# The new version should now be inactive
	# Delete it to clean up
	del_resp = yee_client.delete(
		f"/yee/admin/instruments/{new_id}",
		headers=_bearer_headers(token),
	)
	assert del_resp.status_code == 200, del_resp.text


# ---------------------------------------------------------------------------
# PATCH /yee/admin/instruments/{id} — activate / deactivate
# ---------------------------------------------------------------------------


def test_admin_can_activate_instrument_version(yee_client: TestClient) -> None:
	"""Admin can activate a version via PATCH with is_active=True."""

	token = _login_admin(yee_client)
	suffix = _unique_suffix()

	# Create an inactive draft first
	create_resp = yee_client.post(
		"/yee/admin/instruments",
		params={"activate": "false"},
		headers=_bearer_headers(token),
		json={
			"instrument_key": "yee",
			"instrument_version": f"test-patch-{suffix}",
			"content": _minimal_instrument_content(),
		},
	)
	assert create_resp.status_code == 201, create_resp.text
	draft_id = create_resp.json()["id"]

	# Activate it via PATCH
	patch_resp = yee_client.patch(
		f"/yee/admin/instruments/{draft_id}",
		headers=_bearer_headers(token),
		json={"is_active": True},
	)
	assert patch_resp.status_code == 200, patch_resp.text
	assert patch_resp.json()["is_active"] is True
	assert patch_resp.json()["id"] == draft_id

	# Restore seeded instrument to active
	restore = yee_client.patch(
		f"/yee/admin/instruments/{YEE_INSTRUMENT_ID}",
		headers=_bearer_headers(token),
		json={"is_active": True},
	)
	assert restore.status_code == 200, restore.text

	# Clean up the test draft (now inactive)
	del_resp = yee_client.delete(
		f"/yee/admin/instruments/{draft_id}",
		headers=_bearer_headers(token),
	)
	assert del_resp.status_code == 200, del_resp.text


def test_patch_nonexistent_instrument_returns_404(yee_client: TestClient) -> None:
	"""PATCH on a non-existent instrument ID returns 404."""

	token = _login_admin(yee_client)
	fake_id = uuid.uuid4()
	resp = yee_client.patch(
		f"/yee/admin/instruments/{fake_id}",
		headers=_bearer_headers(token),
		json={"is_active": True},
	)
	assert resp.status_code == 404, resp.text
	assert "Instrument not found" in resp.json()["detail"]


# ---------------------------------------------------------------------------
# DELETE /yee/admin/instruments/{id}
# ---------------------------------------------------------------------------


def test_admin_can_delete_inactive_instrument_version(yee_client: TestClient) -> None:
	"""Deleting an inactive version returns 200 with {"deleted": true}."""

	token = _login_admin(yee_client)
	suffix = _unique_suffix()

	# Create an inactive draft
	create_resp = yee_client.post(
		"/yee/admin/instruments",
		params={"activate": "false"},
		headers=_bearer_headers(token),
		json={
			"instrument_key": "yee",
			"instrument_version": f"test-del-{suffix}",
			"content": _minimal_instrument_content(),
		},
	)
	assert create_resp.status_code == 201, create_resp.text
	draft_id = create_resp.json()["id"]

	# Delete the inactive draft
	del_resp = yee_client.delete(
		f"/yee/admin/instruments/{draft_id}",
		headers=_bearer_headers(token),
	)
	assert del_resp.status_code == 200, del_resp.text
	del_data = del_resp.json()
	assert del_data["deleted"] is True
	assert del_data["instrument_id"] == draft_id


def test_admin_cannot_delete_active_instrument_version(yee_client: TestClient) -> None:
	"""Deleting the active instrument version returns 400.

	The real source raises HTTPException(400, "The active instrument
	version cannot be deleted.") — NOT 409/204. This is the critical
	YEE-specific semantic.
	"""

	token = _login_admin(yee_client)

	# The seeded instrument is active — attempting to delete it must fail
	resp = yee_client.delete(
		f"/yee/admin/instruments/{YEE_INSTRUMENT_ID}",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 400, resp.text
	assert "active instrument version cannot be deleted" in resp.json()["detail"]


def test_delete_nonexistent_instrument_returns_404(yee_client: TestClient) -> None:
	"""DELETE on a non-existent instrument ID returns 404."""

	token = _login_admin(yee_client)
	fake_id = uuid.uuid4()
	resp = yee_client.delete(
		f"/yee/admin/instruments/{fake_id}",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 404, resp.text
	assert "Instrument not found" in resp.json()["detail"]


def test_manager_cannot_delete_instrument(yee_client: TestClient) -> None:
	"""MANAGER gets 403 deleting an instrument (ADMIN-only route)."""

	token = _login_manager(yee_client)
	resp = yee_client.delete(
		f"/yee/admin/instruments/{YEE_INSTRUMENT_ID}",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 403, resp.text
	assert "Admin access is required" in resp.json()["detail"]


def test_auditor_cannot_delete_instrument(yee_client: TestClient) -> None:
	"""AUDITOR gets 403 deleting an instrument (ADMIN-only route)."""

	token = _login_auditor(yee_client)
	resp = yee_client.delete(
		f"/yee/admin/instruments/{YEE_INSTRUMENT_ID}",
		headers=_bearer_headers(token),
	)
	assert resp.status_code == 403, resp.text
	assert "Admin access is required" in resp.json()["detail"]

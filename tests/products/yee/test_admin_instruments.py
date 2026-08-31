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
- DELETE of an inactive version still **referenced by any audit** (a submitted
  ``yee_audit_submissions`` row or an in-progress ``audits`` draft) raises **409**
  "Instrument versions referenced by audits cannot be deleted."
- DELETE of a non-existent ID raises **404** "Instrument not found".
- POST create returns **201** with ``YeeInstrumentVersionResponse``.
- PATCH activate returns 200 with ``YeeInstrumentVersionResponse``.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Audit, AuditStatus, YeeAuditSubmission
from app.seed import YEE_INSTRUMENT_ID
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
	first = data[0]
	for field in (
		"id",
		"instrument_key",
		"instrument_version",
		"is_active",
		"lifecycle",
		"usage_count",
		"schema_generation",
		"compatibility_status",
		"created_at",
		"updated_at",
	):
		assert field in first, f"Missing field {field} in response"
	assert "content" not in first
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
	content_response = yee_client.get("/yee/instrument")
	assert content_response.status_code == 200, content_response.text

	# Create activated version
	resp = yee_client.post(
		"/yee/admin/instruments",
		params={"activate": "true"},
		headers=_bearer_headers(token),
		json={
			"instrument_key": "yee",
			"instrument_version": f"test-active-{suffix}",
			"content": content_response.json(),
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
	content_response = yee_client.get("/yee/instrument")
	assert content_response.status_code == 200, content_response.text

	# Create an inactive draft first
	create_resp = yee_client.post(
		"/yee/admin/instruments",
		params={"activate": "false"},
		headers=_bearer_headers(token),
		json={
			"instrument_key": "yee",
			"instrument_version": f"test-patch-{suffix}",
			"content": content_response.json(),
		},
	)
	assert create_resp.status_code == 201, create_resp.text
	draft_id = create_resp.json()["id"]

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
# Scoring-compatibility gate (publish/activate)
# ---------------------------------------------------------------------------


def test_activate_incompatible_instrument_is_blocked(yee_client: TestClient) -> None:
	"""Activating a version with no scored questions returns 409 with a report."""

	token = _login_admin(yee_client)
	suffix = _unique_suffix()

	create_resp = yee_client.post(
		"/yee/admin/instruments",
		params={"activate": "false"},
		headers=_bearer_headers(token),
		json={
			"instrument_key": "yee",
			"instrument_version": f"test-gate-{suffix}",
			"content": _minimal_instrument_content(),
		},
	)
	assert create_resp.status_code == 201, create_resp.text
	draft_id = create_resp.json()["id"]

	# Activate without override -> blocked by the scoring gate.
	patch_resp = yee_client.patch(
		f"/yee/admin/instruments/{draft_id}",
		headers=_bearer_headers(token),
		json={"is_active": True},
	)
	assert patch_resp.status_code == 409, patch_resp.text
	detail = patch_resp.json()["detail"]
	assert detail["scoring_compatibility"]["ok"] is False
	assert detail["scoring_compatibility"]["missing_items"]

	# The canonical version stays active; the draft never activated. Clean up.
	del_resp = yee_client.delete(
		f"/yee/admin/instruments/{draft_id}",
		headers=_bearer_headers(token),
	)
	assert del_resp.status_code == 200, del_resp.text


def test_create_and_activate_incompatible_without_force_is_blocked(yee_client: TestClient) -> None:
	"""POST create with activate=true and no force is rejected before any row is written."""

	token = _login_admin(yee_client)
	suffix = _unique_suffix()

	resp = yee_client.post(
		"/yee/admin/instruments",
		params={"activate": "true"},
		headers=_bearer_headers(token),
		json={
			"instrument_key": "yee",
			"instrument_version": f"test-gate-create-{suffix}",
			"content": _minimal_instrument_content(),
		},
	)
	assert resp.status_code == 409, resp.text
	assert resp.json()["detail"]["scoring_compatibility"]["ok"] is False


def test_validate_endpoint_flags_incompatible_and_compatible(yee_client: TestClient) -> None:
	"""The dry-run validate endpoint reports ok=False for empty, True for canonical."""

	token = _login_admin(yee_client)

	bad = yee_client.post(
		"/yee/admin/instruments/validate",
		headers=_bearer_headers(token),
		json={"content": _minimal_instrument_content()},
	)
	assert bad.status_code == 200, bad.text
	assert bad.json()["ok"] is False
	assert bad.json()["missing_items"]

	# The active seeded instrument content must validate clean.
	listing = yee_client.get("/yee/admin/instruments", headers=_bearer_headers(token))
	assert listing.status_code == 200, listing.text
	active = next(item for item in listing.json() if item["is_active"])
	detail = yee_client.get(f"/yee/admin/instruments/{active['id']}", headers=_bearer_headers(token))
	assert detail.status_code == 200, detail.text
	good = yee_client.post(
		"/yee/admin/instruments/validate",
		headers=_bearer_headers(token),
		json={"content": detail.json()["content"]},
	)
	assert good.status_code == 200, good.text
	assert good.json()["ok"] is True
	assert good.json()["missing_items"] == []


def test_validate_endpoint_requires_admin(yee_client: TestClient) -> None:
	"""Non-admins cannot reach the scoring validate endpoint."""

	token = _login_manager(yee_client)
	resp = yee_client.post(
		"/yee/admin/instruments/validate",
		headers=_bearer_headers(token),
		json={"content": _minimal_instrument_content()},
	)
	assert resp.status_code == 403, resp.text
	assert "Admin access is required" in resp.json()["detail"]


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


# ---------------------------------------------------------------------------
# DELETE /yee/admin/instruments/{id} — usage guard (referenced by audits)
# ---------------------------------------------------------------------------


async def _restamp_one_draft_version(
	session_factory: async_sessionmaker[AsyncSession],
	instrument_version: str,
) -> tuple[uuid.UUID, str | None]:
	"""Point one seeded in-progress draft at a throwaway version; return (id, original).

	Restamps an existing ``IN_PROGRESS``/``PAUSED`` ``audits`` row rather than
	inserting a new one, because ``audits`` has a unique
	``(project_id, place_id, auditor_profile_id)`` constraint.
	"""

	async with session_factory() as session:
		draft = (
			(
				await session.execute(
					select(Audit).where(Audit.status.in_([AuditStatus.IN_PROGRESS, AuditStatus.PAUSED])).limit(1)
				)
			)
			.scalars()
			.first()
		)
		assert draft is not None, "expected at least one seeded in-progress YEE draft"
		original = draft.instrument_version
		draft.instrument_key = "yee"
		draft.instrument_version = instrument_version
		await session.commit()
		return draft.id, original


async def _restore_draft_version(
	session_factory: async_sessionmaker[AsyncSession],
	audit_id: uuid.UUID,
	instrument_version: str | None,
) -> None:
	"""Restore a draft audit's original instrument version after a guard test."""

	async with session_factory() as session:
		draft = await session.get(Audit, audit_id)
		if draft is not None:
			draft.instrument_version = instrument_version
			await session.commit()


async def _restamp_one_submission_version(
	session_factory: async_sessionmaker[AsyncSession],
	instrument_version: str,
) -> tuple[uuid.UUID, str | None]:
	"""Point one seeded submission at a throwaway version; return (id, original)."""

	async with session_factory() as session:
		submission = (await session.execute(select(YeeAuditSubmission).limit(1))).scalars().first()
		assert submission is not None, "expected at least one seeded YEE submission"
		original = submission.instrument_version
		submission.instrument_key = "yee"
		submission.instrument_version = instrument_version
		await session.commit()
		return submission.id, original


async def _restore_submission_version(
	session_factory: async_sessionmaker[AsyncSession],
	submission_id: uuid.UUID,
	instrument_version: str | None,
) -> None:
	"""Restore a submission's original instrument version after a guard test."""

	async with session_factory() as session:
		submission = await session.get(YeeAuditSubmission, submission_id)
		if submission is not None:
			submission.instrument_version = instrument_version
			await session.commit()


def test_delete_instrument_version_referenced_by_draft_returns_409(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""An inactive version referenced by an in-progress draft cannot be deleted."""

	token = _login_admin(yee_client)
	version_label = f"guard-draft-{_unique_suffix()}"

	create_resp = yee_client.post(
		"/yee/admin/instruments",
		params={"activate": "false"},
		headers=_bearer_headers(token),
		json={
			"instrument_key": "yee",
			"instrument_version": version_label,
			"content": _minimal_instrument_content(),
		},
	)
	assert create_resp.status_code == 201, create_resp.text
	instrument_id = create_resp.json()["id"]

	audit_id, original_version = asyncio.run(_restamp_one_draft_version(yee_test_session_factory, version_label))
	try:
		del_resp = yee_client.delete(
			f"/yee/admin/instruments/{instrument_id}",
			headers=_bearer_headers(token),
		)
		assert del_resp.status_code == 409, del_resp.text
		assert "referenced by audits" in del_resp.json()["detail"]
	finally:
		asyncio.run(_restore_draft_version(yee_test_session_factory, audit_id, original_version))

	# With the draft gone, the same version now deletes cleanly.
	del_again = yee_client.delete(
		f"/yee/admin/instruments/{instrument_id}",
		headers=_bearer_headers(token),
	)
	assert del_again.status_code == 200, del_again.text


def test_delete_instrument_version_referenced_by_submission_returns_409(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""An inactive version referenced by a submitted audit cannot be deleted."""

	token = _login_admin(yee_client)
	version_label = f"guard-sub-{_unique_suffix()}"

	create_resp = yee_client.post(
		"/yee/admin/instruments",
		params={"activate": "false"},
		headers=_bearer_headers(token),
		json={
			"instrument_key": "yee",
			"instrument_version": version_label,
			"content": _minimal_instrument_content(),
		},
	)
	assert create_resp.status_code == 201, create_resp.text
	instrument_id = create_resp.json()["id"]

	submission_id, original_version = asyncio.run(
		_restamp_one_submission_version(yee_test_session_factory, version_label)
	)
	try:
		del_resp = yee_client.delete(
			f"/yee/admin/instruments/{instrument_id}",
			headers=_bearer_headers(token),
		)
		assert del_resp.status_code == 409, del_resp.text
		assert "referenced by audits" in del_resp.json()["detail"]
	finally:
		asyncio.run(_restore_submission_version(yee_test_session_factory, submission_id, original_version))

	del_again = yee_client.delete(
		f"/yee/admin/instruments/{instrument_id}",
		headers=_bearer_headers(token),
	)
	assert del_again.status_code == 200, del_again.text

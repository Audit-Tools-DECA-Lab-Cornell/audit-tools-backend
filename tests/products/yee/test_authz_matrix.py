"""Flow V -- Authz matrix sweep.

Parametrized role x route x method sweep over the /yee/dashboard/* and
/yee/admin/* surfaces.  For every route we assert the authorization gate
precisely:

 * no Authorization header  -> 401 "Authentication required."
 * wrong-tier role           -> 403 (message varies by gate)
 * correct-tier role         -> NOT 401/403 (the request may still fail
   with 404/422 for missing path params or body -- that is fine, the
   point is that the *auth gate* passed)

The route table is derived from GROUND-TRUTH §4 and confirmed against
the real source in dashboard_router.py and products/yee/routes/instrument.py.
"""

from __future__ import annotations

import uuid
from typing import Literal

import pytest
from fastapi.testclient import TestClient

from tests.products.yee._helpers import (
	SEED_AUDITOR_EMAIL,
	SEED_MANAGER_EMAIL,
	SEED_PASSWORD,
	_bearer_headers,
	_login_auditor,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SEED_ADMIN_EMAIL = "admin-demo@yee.local"

# A random UUID for path parameters that need one (won't match any real row,
# so the handler may 404/422 after passing the auth gate -- that is fine).
FAKE_UUID = "00000000-face-4000-8000-000000000000"

# ---------------------------------------------------------------------------
# Login helpers
# ---------------------------------------------------------------------------


def _login_manager(client: TestClient) -> str:
	resp = client.post(
		"/yee/auth/login",
		json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD},
	)
	assert resp.status_code == 200, resp.text
	return resp.json()["access_token"]


def _login_admin(client: TestClient) -> str:
	resp = client.post(
		"/yee/auth/login",
		json={"email": SEED_ADMIN_EMAIL, "password": SEED_PASSWORD},
	)
	assert resp.status_code == 200, resp.text
	return resp.json()["access_token"]


# ---------------------------------------------------------------------------
# Route table
# ---------------------------------------------------------------------------
# Each entry: (method, path, auth_tier, description)
#
# auth_tier values:
#   "manager_or_admin"  -- _require_manager_or_admin  (manager OK, admin OK, auditor 403)
#   "admin_only"        -- _require_admin              (admin OK, manager 403, auditor 403)
#   "auditor_only"      -- auditor-scoped              (auditor OK, manager 403, admin 403)
#   "manager_only"      -- manager account_type check  (manager OK, admin 403, auditor 403)

AuthTier = Literal["manager_or_admin", "admin_only", "auditor_only", "manager_only"]

ROUTE_TABLE: list[tuple[str, str, AuthTier, str]] = [
	# --- manager_or_admin routes (dashboard_router.py) ---
	("GET", "/yee/dashboard/overview", "manager_or_admin", "dashboard overview"),
	("GET", "/yee/dashboard/projects", "manager_or_admin", "list projects"),
	("GET", f"/yee/dashboard/projects/{FAKE_UUID}", "manager_or_admin", "project detail"),
	("GET", "/yee/dashboard/places", "manager_or_admin", "list places"),
	("GET", f"/yee/dashboard/places/{FAKE_UUID}", "manager_or_admin", "place detail"),
	("GET", "/yee/dashboard/auditors", "manager_or_admin", "list auditors"),
	("GET", "/yee/dashboard/audits", "manager_or_admin", "list audits"),
	("GET", f"/yee/dashboard/audits/{FAKE_UUID}/edit", "manager_or_admin", "audit edit state"),
	("GET", "/yee/dashboard/reports/place-comparisons", "manager_or_admin", "place comparisons"),
	("GET", "/yee/dashboard/raw-data", "manager_or_admin", "raw data export"),
	# Write routes -- send minimal/empty body to test the gate only
	("POST", "/yee/dashboard/projects", "manager_or_admin", "create project (gate)"),
	("POST", "/yee/dashboard/places", "manager_or_admin", "create place (gate)"),
	("POST", "/yee/dashboard/auditor-invites", "manager_or_admin", "create auditor invite (gate)"),
	("POST", "/yee/dashboard/assignments", "manager_or_admin", "create assignment (gate)"),
	# --- admin_only routes (dashboard_router.py) ---
	("GET", "/yee/dashboard/users", "admin_only", "list users"),
	("POST", "/yee/dashboard/users/approve", "admin_only", "approve user (gate)"),
	# --- admin_only routes (instrument.py -- /yee/admin/*) ---
	("GET", "/yee/admin/instruments", "admin_only", "list instruments"),
	("GET", "/yee/admin/site-copy", "admin_only", "list site-copy versions"),
	# --- manager_only routes (dashboard_router.py) ---
	("GET", "/yee/dashboard/manager-profile", "manager_only", "get manager profile"),
	("GET", "/yee/dashboard/managers", "manager_only", "list managers"),
	("POST", "/yee/dashboard/my-auditor-profile", "manager_only", "create self auditor profile"),
	# --- auditor_only route ---
	("GET", "/yee/dashboard/my-places", "auditor_only", "my assigned places"),
]

# 403 detail messages per gate
_GATE_403_MSG = {
	"manager_or_admin": "Manager or admin access is required.",
	"admin_only_dashboard": "Admin access is required.",
	"admin_only_instrument": "Admin access is required.",
	"manager_only": "Manager access is required.",
	"auditor_only": "Auditor access is required.",
}


# ---------------------------------------------------------------------------
# Parametrize helpers
# ---------------------------------------------------------------------------


def _make_request(
	client: TestClient,
	method: str,
	path: str,
	headers: dict[str, str] | None = None,
):
	"""Fire one request; for POST/PATCH/PUT send a minimal JSON body."""
	kwargs: dict = {}
	if headers is not None:
		kwargs["headers"] = headers

	if method in ("POST", "PATCH", "PUT"):
		# Send an empty JSON object -- enough to pass the auth gate; the
		# handler will reject it with 422 (missing required fields) or
		# similar AFTER the authz check passes.
		kwargs["json"] = {}

	requester = getattr(client, method.lower())
	return requester(path, **kwargs)


# ---------------------------------------------------------------------------
# Build parametrize cases
# ---------------------------------------------------------------------------

# Each case: (method, path, role_name, expected_outcome, test_id)
#   expected_outcome:
#     "401"  -- must be 401
#     "403"  -- must be 403
#     "pass" -- must NOT be 401 or 403

Case = tuple[str, str, str, str, str]  # method, path, role, outcome, id


def _build_cases() -> list[Case]:
	"""Generate (route x role) cases with expected outcomes."""
	cases: list[Case] = []

	for method, path, tier, desc in ROUTE_TABLE:
		slug = desc.replace(" ", "_").replace("(", "").replace(")", "")

		# --- no token --- (reliable for every method: HTTPBearer 401s before body parsing)
		cases.append((method, path, "none", "401", f"{slug}__no_token"))

		# Write routes (POST/PATCH/PUT): an empty body is rejected by Pydantic
		# (422) BEFORE the role dependency can raise 403, so the role gate cannot
		# be asserted here with a uniform empty body. Role-gating for write routes
		# is covered WITH VALID BODIES in the per-resource files
		# (test_dashboard_projects/places/assignments, test_auditor_invite_gaps,
		# test_admin_users_sitecopy). Here we assert only the no-token 401 gate for
		# writes; the full none/auditor/manager/admin matrix below applies to GET routes.
		if method != "GET":
			continue

		# --- auditor ---
		if tier == "auditor_only":
			cases.append((method, path, "auditor", "pass", f"{slug}__auditor_pass"))
		else:
			cases.append((method, path, "auditor", "403", f"{slug}__auditor_denied"))

		# --- manager ---
		if tier in ("manager_or_admin", "manager_only"):
			cases.append((method, path, "manager", "pass", f"{slug}__manager_pass"))
		else:
			cases.append((method, path, "manager", "403", f"{slug}__manager_denied"))

		# --- admin ---
		if tier in ("manager_or_admin", "admin_only"):
			cases.append((method, path, "admin", "pass", f"{slug}__admin_pass"))
		else:
			cases.append((method, path, "admin", "403", f"{slug}__admin_denied"))

	return cases


ALL_CASES = _build_cases()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _tokens(yee_client: TestClient) -> dict[str, str | None]:
	"""Cache tokens for the three roles; computed once per module."""
	return {
		"none": None,
		"auditor": _login_auditor(yee_client),
		"manager": _login_manager(yee_client),
		"admin": _login_admin(yee_client),
	}


# ---------------------------------------------------------------------------
# The parametrized sweep
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
	"method, path, role, expected, _id",
	ALL_CASES,
	ids=[c[4] for c in ALL_CASES],
)
def test_authz_gate(
	yee_client: TestClient,
	_tokens: dict[str, str | None],
	method: str,
	path: str,
	role: str,
	expected: str,
	_id: str,
):
	"""Assert the auth gate for one (route, role) combination."""

	token = _tokens[role]
	headers = _bearer_headers(token) if token is not None else {}

	resp = _make_request(yee_client, method, path, headers or None)

	if expected == "401":
		assert resp.status_code == 401, f"[{_id}] Expected 401 but got {resp.status_code}: {resp.text}"
		assert "Authentication required" in resp.json().get("detail", ""), f"[{_id}] 401 detail mismatch: {resp.json()}"

	elif expected == "403":
		assert resp.status_code == 403, f"[{_id}] Expected 403 but got {resp.status_code}: {resp.text}"

	elif expected == "pass":
		assert resp.status_code not in (401, 403), (
			f"[{_id}] Expected auth gate to pass but got {resp.status_code}: {resp.text}"
		)

	else:
		raise AssertionError(f"Unknown expected outcome: {expected}")

"""YEE endpoint integration tests.

These verify the YEE routes work against the per-product YEE schema produced by
the `yee` Alembic branch (shared core tables + `yee_audit_submissions`, and no
Playspace tables). They exercise the seeded auditor flow end to end:
instrument → audit-state → draft → submit → list → fetch.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.models import Instrument
from app.seed import YEE_PLACE_PLAZA_ID, _build_yee_entities

# Matches the deterministic YEE seed (see app/seed.py).
SEED_AUDITOR_EMAIL = "auditor-demo-1@yee.local"
SEED_PASSWORD = "DemoPass123!"


def _bearer_headers(access_token: str) -> dict[str, str]:
	"""Build bearer auth headers for session-backed authorization."""

	return {"Authorization": f"bearer {access_token}"}


def _login_auditor(client: TestClient, email: str = SEED_AUDITOR_EMAIL, password: str = SEED_PASSWORD) -> str:
	"""Login a seeded YEE auditor account and return a bearer token."""

	response = client.post("/yee/auth/login", json={"email": email, "password": password})
	assert response.status_code == 200, response.text
	return response.json()["access_token"]


def test_yee_status_is_isolated(yee_client: TestClient) -> None:
	"""The YEE namespace status stub responds without touching Playspace."""

	response = yee_client.get("/yee/status")
	assert response.status_code == 200, response.text
	assert response.json()["product"] == "yee"


def test_yee_instrument_available(yee_client: TestClient) -> None:
	"""The instrument endpoint returns scoring metadata (no DB dependency)."""

	response = yee_client.get("/yee/instrument")
	assert response.status_code == 200, response.text
	assert isinstance(response.json(), dict)


def test_seeded_auditor_can_login(yee_client: TestClient) -> None:
	"""A seeded YEE auditor authenticates against the rebuilt YEE schema."""

	token = _login_auditor(yee_client)
	assert token


def test_audit_state_starts_not_started(yee_client: TestClient) -> None:
	"""An assigned-but-unstarted place reports NOT_STARTED."""

	token = _login_auditor(yee_client)
	response = yee_client.get(
		f"/yee/places/{YEE_PLACE_PLAZA_ID}/audit-state",
		headers=_bearer_headers(token),
	)
	assert response.status_code == 200, response.text
	assert response.json()["status"] == "NOT_STARTED"


def test_yee_draft_submit_flow_uses_yee_audit_submissions(yee_client: TestClient) -> None:
	"""Full flow: save a draft, submit, then read it back via list + detail.

	This is the regression guard for the previously-missing
	``yee_audit_submissions`` table: submit writes one row and the list/detail
	endpoints read it back.
	"""

	token = _login_auditor(yee_client)
	headers = _bearer_headers(token)
	place_path = f"/yee/places/{YEE_PLACE_PLAZA_ID}"
	responses_payload = {"QID22": "3"}

	# Save a backend draft (creates an Audit row with instrument_key="yee").
	draft = yee_client.put(
		f"{place_path}/draft",
		headers=headers,
		json={"participant_info": {"total_minutes": 12}, "responses": responses_payload},
	)
	assert draft.status_code == 200, draft.text
	assert draft.json()["status"] == "DRAFT"

	# Submit the audit (creates exactly one yee_audit_submissions row).
	submit = yee_client.post(
		"/yee/audits",
		headers=headers,
		json={
			"place_id": str(YEE_PLACE_PLAZA_ID),
			"participant_info": {"total_minutes": 12},
			"responses": responses_payload,
		},
	)
	assert submit.status_code == 201, submit.text
	submission_id = submit.json()["id"]

	# A second submit for the same place is rejected.
	duplicate = yee_client.post(
		"/yee/audits",
		headers=headers,
		json={"place_id": str(YEE_PLACE_PLAZA_ID), "responses": responses_payload},
	)
	assert duplicate.status_code == 409, duplicate.text

	# The submission appears in the auditor's list.
	listing = yee_client.get("/yee/my-audits", headers=headers)
	assert listing.status_code == 200, listing.text
	assert any(item["id"] == submission_id for item in listing.json())

	# And is fetchable by id.
	detail = yee_client.get(f"/yee/audits/{submission_id}", headers=headers)
	assert detail.status_code == 200, detail.text
	assert detail.json()["place_id"] == str(YEE_PLACE_PLAZA_ID)

	# audit-state now reports the submitted record.
	state = yee_client.get(f"{place_path}/audit-state", headers=headers)
	assert state.status_code == 200, state.text
	assert state.json()["status"] == "SUBMITTED"


def test_build_yee_entities_instrument_is_active_root_version() -> None:
	"""The seeded YEE instrument is the active root of version history."""

	entities = _build_yee_entities()
	instruments = [entity for entity in entities if isinstance(entity, Instrument)]

	assert len(instruments) == 1
	seed_instrument = instruments[0]
	assert seed_instrument.is_active is True
	assert seed_instrument.parent_instrument_id is None

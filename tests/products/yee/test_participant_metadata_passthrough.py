"""Contract pin: client-supplied audit identity metadata round-trips verbatim.

The YEE mobile app stamps ``participant_id`` (typed by the auditor) and device
identity fields (``tablet_id`` entered once in Settings → Device, plus
best-effort ``os_device_id`` / ``device_model``) into ``participant_info`` so a
completed audit can be linked to an individual participant and traced back to
the tablet it was captured on. The backend intentionally treats
``participant_info`` as an open dict stored verbatim in
``yee_audit_submissions.participant_info_json`` — these tests pin that pass-
through so a future schema tightening cannot silently drop the keys.

Runs entirely inside an isolated org (own manager, project, place, and dual-
role auditor profile) so it never perturbs seeded fixtures other suites read.
"""

from __future__ import annotations

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from tests.products.yee._helpers import (
	_signup_primary_manager,
	_unique_suffix,
)

PARTICIPANT_METADATA = {
	"participant_id": "P-042",
	"tablet_id": "TAB-07",
	"os_device_id": "android-1234abcd",
	"device_model": "Galaxy Tab A9",
	"total_minutes": 18,
}


def _create_assigned_place(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> tuple[dict[str, str], str]:
	"""Provision an isolated org with one place assigned to the manager's own auditor profile.

	Returns the manager's bearer headers (also valid for the auditor-side
	routes via the dual-role profile) and the place id.
	"""

	mgr = _signup_primary_manager(yee_client, yee_test_session_factory)
	suffix = _unique_suffix()

	project = yee_client.post(
		"/yee/dashboard/projects",
		headers=mgr["headers"],
		json={"name": f"Metadata Passthrough Project {suffix}"},
	)
	assert project.status_code == 200, project.text
	project_id = project.json()["id"]

	place = yee_client.post(
		"/yee/dashboard/places",
		headers=mgr["headers"],
		json={
			"project_id": project_id,
			"name": f"Metadata Passthrough Place {suffix}",
			"address": "123 Passthrough Way",
			"city": "Ithaca",
			"province": "New York",
			"country": "United States",
			"place_type": "park",
		},
	)
	assert place.status_code == 200, place.text
	place_id = place.json()["id"]

	profile = yee_client.post("/yee/dashboard/my-auditor-profile", headers=mgr["headers"])
	assert profile.status_code == 201, profile.text

	assignment = yee_client.post(
		"/yee/dashboard/assignments",
		headers=mgr["headers"],
		json={
			"project_id": project_id,
			"auditor_ids": [profile.json()["id"]],
			"place_ids": [place_id],
		},
	)
	assert assignment.status_code in (200, 201), assignment.text

	return mgr["headers"], place_id


def test_draft_preserves_participant_and_device_metadata(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""Draft saves keep the new identity keys through write and state reads."""

	headers, place_id = _create_assigned_place(yee_client, yee_test_session_factory)

	draft = yee_client.put(
		f"/yee/places/{place_id}/draft",
		headers=headers,
		json={"participant_info": PARTICIPANT_METADATA, "responses": {"QID22": "3"}},
	)
	assert draft.status_code == 200, draft.text
	for key, value in PARTICIPANT_METADATA.items():
		assert draft.json()["participant_info"][key] == value

	state = yee_client.get(f"/yee/places/{place_id}/audit-state", headers=headers)
	assert state.status_code == 200, state.text
	assert state.json()["status"] == "DRAFT"
	for key, value in PARTICIPANT_METADATA.items():
		assert state.json()["participant_info"][key] == value


def test_submission_preserves_participant_and_device_metadata(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""Submit stores the identity keys verbatim and detail reads return them."""

	headers, place_id = _create_assigned_place(yee_client, yee_test_session_factory)

	submit = yee_client.post(
		"/yee/audits",
		headers=headers,
		json={
			"place_id": place_id,
			"participant_info": PARTICIPANT_METADATA,
			"responses": {"QID22": "3"},
		},
	)
	assert submit.status_code == 201, submit.text
	submission_id = submit.json()["id"]
	for key, value in PARTICIPANT_METADATA.items():
		assert submit.json()["participant_info"][key] == value

	# Detail read (same dual-role account exercises the owner path; the manager
	# scope path returns the identical payload).
	detail = yee_client.get(f"/yee/audits/{submission_id}", headers=headers)
	assert detail.status_code == 200, detail.text
	for key, value in PARTICIPANT_METADATA.items():
		assert detail.json()["participant_info"][key] == value

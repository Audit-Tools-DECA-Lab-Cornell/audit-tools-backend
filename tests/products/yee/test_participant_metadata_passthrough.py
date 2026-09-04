"""Contract pin: client-supplied audit identity metadata round-trips verbatim.

The YEE mobile app stamps ``participant_id`` (an optional free-text ID typed by
the auditor) into ``participant_info`` so a completed audit can be linked to an
individual participant. The backend intentionally treats ``participant_info``
as an open dict stored verbatim in
``yee_audit_submissions.participant_info_json`` — these tests pin that pass-
through so a future schema tightening cannot silently drop the keys.

Runs entirely inside an isolated org (own manager, project, place, and dual-
role auditor profile) so it never perturbs seeded fixtures other suites read.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.products.yee.services.dashboard import participant_id_from_info
from tests.products.yee._helpers import (
	_signup_primary_manager,
	_unique_suffix,
)


@pytest.mark.parametrize(
	("participant_info", "expected"),
	[
		({"participant_id": "P-042"}, "P-042"),
		({"participant_id": "  P-042  "}, "P-042"),
		({"participant_id": "   "}, None),
		({"participant_id": ""}, None),
		({}, None),
		(None, None),
		# Non-string values from a malformed client are treated as absent, never
		# coerced into display text like "True", "42", or "['P-042']".
		({"participant_id": 42}, None),
		({"participant_id": True}, None),
		({"participant_id": ["P-042"]}, None),
	],
)
def test_participant_id_from_info_only_accepts_non_blank_strings(
	participant_info: object, expected: str | None
) -> None:
	assert participant_id_from_info(participant_info) == expected


PARTICIPANT_METADATA = {
	"participant_id": "P-042",
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


def test_draft_preserves_participant_metadata(
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


def test_submission_preserves_participant_metadata(
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


def test_participant_id_surfaces_on_list_and_report_endpoints(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""The frontend display surfaces need ``participant_id`` on the list/report models.

	The individual submission, audit-state, and manager edit-state responses already
	echo the full ``participant_info`` dict. These four read models flatten only a
	projection of it, so ``participant_id`` is promoted to an explicit field — pin
	that it reaches each of them for a submission that carried the ID.
	"""

	headers, place_id = _create_assigned_place(yee_client, yee_test_session_factory)
	expected_participant_id = PARTICIPANT_METADATA["participant_id"]

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

	def _row_for(rows: list[dict], key: str) -> dict:
		match = next((row for row in rows if row.get(key) == submission_id), None)
		assert match is not None, f"submission {submission_id} missing from {rows}"
		return match

	# Auditor "my audits" list (dual-role account also owns the auditor profile).
	my_audits = yee_client.get("/yee/my-audits", headers=headers)
	assert my_audits.status_code == 200, my_audits.text
	my_audit_row = _row_for(my_audits.json(), "id")
	assert my_audit_row["participant_id"] == expected_participant_id
	assert my_audit_row["total_raw_maximum"] == 122
	assert my_audit_row["total_weighted_maximum"] == 0.0

	# Manager/admin audits list.
	audits = yee_client.get("/yee/dashboard/audits", headers=headers)
	assert audits.status_code == 200, audits.text
	audit_row = _row_for(audits.json(), "submission_id")
	assert audit_row["participant_id"] == expected_participant_id

	# Project detail latest_audits (reuses AuditListItem; resolves the submission).
	project_detail = yee_client.get(f"/yee/dashboard/projects/{audit_row['project_id']}", headers=headers)
	assert project_detail.status_code == 200, project_detail.text
	latest_for_place = next(
		(row for row in project_detail.json()["latest_audits"] if row["place_id"] == place_id),
		None,
	)
	assert latest_for_place is not None, project_detail.text
	assert latest_for_place["participant_id"] == expected_participant_id
	assert latest_for_place["total_raw_maximum"] == audit_row["total_raw_maximum"] == 122
	assert latest_for_place["total_weighted_maximum"] == audit_row["total_weighted_maximum"] == 0.0

	# Place-comparison report rows (manager/admin comparisons).
	comparisons = yee_client.get("/yee/dashboard/reports/place-comparisons", headers=headers)
	assert comparisons.status_code == 200, comparisons.text
	comparison_audits = [audit for group in comparisons.json() for audit in group["audits"]]
	assert _row_for(comparison_audits, "audit_id")["participant_id"] == expected_participant_id

	# Raw-data export feed.
	raw_data = yee_client.get("/yee/dashboard/raw-data", headers=headers)
	assert raw_data.status_code == 200, raw_data.text
	assert _row_for(raw_data.json(), "audit_id")["participant_id"] == expected_participant_id

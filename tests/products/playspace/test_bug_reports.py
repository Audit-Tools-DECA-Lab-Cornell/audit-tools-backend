"""Integration coverage for the internal bug-reporting and known-issues API."""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from tests.products.playspace.conftest import PlayspaceSeedSnapshot

MANAGER_EMAIL = "amelia.carter@example.org"
ADMIN_EMAIL = "playspace.admin@example.org"
SEED_PASSWORD = "DemoPass123!"


def _bearer(token: str) -> dict[str, str]:
	return {"Authorization": f"bearer {token}"}


def _login(client: TestClient, email: str, password: str = SEED_PASSWORD) -> str:
	response = client.post("/playspace/auth/login", json={"email": email, "password": password})
	assert response.status_code == 200, response.text
	return response.json()["access_token"]


def _create_known_issue(client: TestClient, admin_headers: dict[str, str], **overrides: object) -> dict:
	payload = {
		"title": "Audit screen freezes on submit",
		"symptoms": "The submit button spins and nothing happens.",
		"workaround": "Pull to refresh and submit again.",
		"status": "open",
		"tags": ["submit", "freeze"],
		"surfaces": ["mobile"],
		"is_published": True,
	}
	payload.update(overrides)
	response = client.post("/playspace/admin/known-issues", json=payload, headers=admin_headers)
	assert response.status_code == 201, response.text
	return response.json()


######################################################################################
#################################### Bug Reports #####################################
######################################################################################


def test_auditor_can_file_bug_report_with_verified_audit_context(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""An auditor files a report; their own audit reference is persisted as a FK."""

	auditor_headers = _bearer(_login(playspace_client, playspace_seed_snapshot.seeded_auditor_email))

	payload = {
		"surface": "mobile",
		"title": "Scale option not tappable",
		"description": "Tapping the third scale option does nothing on the space audit screen.",
		"severity": "major",
		"playspace_submission_id": playspace_seed_snapshot.riverside_submitted_audit_id,
		"context": {
			"app_version": "0.9.3",
			"screen": "execute/space-audit",
			"network_online": True,
			"sync_phase": "idle",
			"section_id": "play-value",
			"question_id": "pv-12",
		},
	}
	response = playspace_client.post("/playspace/bug-reports", json=payload, headers=auditor_headers)
	assert response.status_code == 201, response.text
	body = response.json()

	assert body["status"] == "new"
	assert body["surface"] == "mobile"
	assert body["severity"] == "major"
	assert body["reporter_role"] == "auditor"
	assert body["reporter_email"] == playspace_seed_snapshot.seeded_auditor_email
	assert body["account_id"] == playspace_seed_snapshot.seeded_auditor_account_id
	# The auditor owns this submission, so the reference is trusted and persisted.
	assert body["playspace_submission_id"] == playspace_seed_snapshot.riverside_submitted_audit_id
	# Allow-listed context survives; nothing else is invented.
	assert body["context"]["section_id"] == "play-value"
	assert body["context"]["question_id"] == "pv-12"


def test_manager_report_persists_owned_project_and_place_refs(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""A manager's references to their own project/place are trusted and stored."""

	manager_headers = _bearer(_login(playspace_client, MANAGER_EMAIL))

	payload = {
		"surface": "web",
		"title": "Place report export is blank",
		"description": "Exporting the Riverside place report produces an empty PDF.",
		"severity": "blocking",
		"project_id": playspace_seed_snapshot.urban_project_id,
		"place_id": playspace_seed_snapshot.riverside_place_id,
	}
	response = playspace_client.post("/playspace/bug-reports", json=payload, headers=manager_headers)
	assert response.status_code == 201, response.text
	body = response.json()

	assert body["project_id"] == playspace_seed_snapshot.urban_project_id
	assert body["place_id"] == playspace_seed_snapshot.riverside_place_id
	assert body["reporter_role"] == "manager"


def test_unknown_entity_reference_is_dropped(
	playspace_client: TestClient,
) -> None:
	"""A reference to a non-existent entity is not persisted as a FK."""

	manager_headers = _bearer(_login(playspace_client, MANAGER_EMAIL))

	payload = {
		"surface": "web",
		"title": "Spurious project reference",
		"description": "Filed with a project id that does not exist.",
		"severity": "minor",
		"project_id": str(uuid.uuid4()),
	}
	response = playspace_client.post("/playspace/bug-reports", json=payload, headers=manager_headers)
	assert response.status_code == 201, response.text
	assert response.json()["project_id"] is None


def test_context_rejects_unknown_fields(
	playspace_client: TestClient,
) -> None:
	"""The diagnostic context is a strict allow-list to keep sensitive data out."""

	manager_headers = _bearer(_login(playspace_client, MANAGER_EMAIL))

	payload = {
		"surface": "web",
		"title": "Should be rejected",
		"description": "Context carries a disallowed field.",
		"severity": "minor",
		"context": {"app_version": "1.0", "auth_token": "secret-should-not-be-allowed"},
	}
	response = playspace_client.post("/playspace/bug-reports", json=payload, headers=manager_headers)
	assert response.status_code == 422, response.text


def test_mine_returns_only_callers_reports(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""``/bug-reports/mine`` is scoped to the authenticated reporter."""

	auditor_headers = _bearer(_login(playspace_client, playspace_seed_snapshot.seeded_auditor_email))
	create = playspace_client.post(
		"/playspace/bug-reports",
		json={
			"surface": "mobile",
			"title": "Mine-scope check",
			"description": "A report that should appear under my reports.",
			"severity": "minor",
		},
		headers=auditor_headers,
	)
	assert create.status_code == 201, create.text
	created_id = create.json()["id"]

	mine = playspace_client.get("/playspace/bug-reports/mine", headers=auditor_headers)
	assert mine.status_code == 200, mine.text
	ids = {row["id"] for row in mine.json()}
	assert created_id in ids


######################################################################################
#################################### Known Issues ####################################
######################################################################################


def test_known_issue_match_returns_published_only(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""Published known issues are matched for reporters; drafts are not."""

	admin_headers = _bearer(_login(playspace_client, ADMIN_EMAIL))
	published = _create_known_issue(
		playspace_client,
		admin_headers,
		title="Submit button freeze on mobile",
		symptoms="Submit spins forever on the final comments screen.",
		is_published=True,
	)
	_create_known_issue(
		playspace_client,
		admin_headers,
		title="Submit button freeze draft (unpublished)",
		symptoms="Submit spins forever on the final comments screen.",
		is_published=False,
	)

	auditor_headers = _bearer(_login(playspace_client, playspace_seed_snapshot.seeded_auditor_email))
	response = playspace_client.get("/playspace/known-issues/match?q=submit+freeze", headers=auditor_headers)
	assert response.status_code == 200, response.text
	matched_ids = {row["id"] for row in response.json()}
	assert published["id"] in matched_ids
	assert all(row["id"] != "" for row in response.json())
	# The unpublished draft must never be surfaced.
	titles = {row["title"] for row in response.json()}
	assert "Submit button freeze draft (unpublished)" not in titles


def test_admin_can_triage_and_link_known_issue(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""Admin updates a report's status and links it to a known issue."""

	auditor_headers = _bearer(_login(playspace_client, playspace_seed_snapshot.seeded_auditor_email))
	report_id = playspace_client.post(
		"/playspace/bug-reports",
		json={
			"surface": "mobile",
			"title": "Triage target",
			"description": "Report to be triaged by admin.",
			"severity": "major",
		},
		headers=auditor_headers,
	).json()["id"]

	admin_headers = _bearer(_login(playspace_client, ADMIN_EMAIL))
	known_issue = _create_known_issue(playspace_client, admin_headers, title="Linked issue")

	response = playspace_client.patch(
		f"/playspace/admin/bug-reports/{report_id}",
		json={"status": "triaged", "linked_known_issue_id": known_issue["id"]},
		headers=admin_headers,
	)
	assert response.status_code == 200, response.text
	body = response.json()
	assert body["status"] == "triaged"
	assert body["linked_known_issue_id"] == known_issue["id"]


def test_known_issue_crud_lifecycle(
	playspace_client: TestClient,
) -> None:
	"""Admin can create, update, and delete a known issue."""

	admin_headers = _bearer(_login(playspace_client, ADMIN_EMAIL))
	issue = _create_known_issue(playspace_client, admin_headers, title="CRUD issue", is_published=False)

	update = playspace_client.patch(
		f"/playspace/admin/known-issues/{issue['id']}",
		json={"is_published": True, "status": "monitoring"},
		headers=admin_headers,
	)
	assert update.status_code == 200, update.text
	assert update.json()["is_published"] is True
	assert update.json()["status"] == "monitoring"

	delete = playspace_client.delete(f"/playspace/admin/known-issues/{issue['id']}", headers=admin_headers)
	assert delete.status_code == 204, delete.text

	listing = playspace_client.get("/playspace/admin/known-issues", headers=admin_headers)
	assert listing.status_code == 200, listing.text
	assert issue["id"] not in {row["id"] for row in listing.json()}


######################################################################################
######################################## RBAC ########################################
######################################################################################


def test_non_admin_cannot_review_or_maintain(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""Only admins reach the review list, triage, and known-issue maintenance."""

	auditor_headers = _bearer(_login(playspace_client, playspace_seed_snapshot.seeded_auditor_email))
	manager_headers = _bearer(_login(playspace_client, MANAGER_EMAIL))

	assert playspace_client.get("/playspace/admin/bug-reports", headers=auditor_headers).status_code == 403
	assert playspace_client.get("/playspace/admin/bug-reports", headers=manager_headers).status_code == 403
	assert (
		playspace_client.post(
			"/playspace/admin/known-issues",
			json={"title": "x", "symptoms": "y"},
			headers=auditor_headers,
		).status_code
		== 403
	)
	assert (
		playspace_client.patch(
			f"/playspace/admin/bug-reports/{uuid.uuid4()}",
			json={"status": "resolved"},
			headers=manager_headers,
		).status_code
		== 403
	)


def test_unauthenticated_requests_are_rejected(
	playspace_client: TestClient,
) -> None:
	"""Filing and matching both require authentication."""

	assert playspace_client.post(
		"/playspace/bug-reports",
		json={"surface": "web", "title": "x", "description": "y", "severity": "minor"},
	).status_code in (401, 403)
	assert playspace_client.get("/playspace/known-issues/match?q=test").status_code in (401, 403)

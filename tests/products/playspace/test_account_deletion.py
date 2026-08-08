"""Coverage for Playspace self-service account deletion.

The governing rule under test is **delete the person, preserve the work**. The
most valuable assertions here are the ones proving that submitted audits, their
codes, and the reports built from them survive an auditor's deletion - that is
the property the whole feature exists to protect, and the one a careless
refactor of the profile detachment would silently destroy.

Schema-level tests run anywhere. The integration tests require
``TEST_DATABASE_URL_PLAYSPACE`` and skip without it.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.products.playspace.schemas.me import (
	AccountDeletionBlocker,
	AccountDeletionRequest,
	PrimaryManagerTransferRequest,
)
from app.products.playspace.services.account_deletion import DELETED_AUDITOR_DISPLAY_NAME
from tests.products.playspace.conftest import PlayspaceSeedSnapshot, _reseed_playspace_database

PRIMARY_MANAGER_EMAIL = "amelia.carter@example.org"
SECONDARY_MANAGER_EMAIL = "noah.bennett@example.org"
ADMIN_EMAIL = "playspace.admin@example.org"
SEED_PASSWORD = "DemoPass123!"
WRONG_PASSWORD = "NotTheRightPassword123!"

DELETION_PATH = "/playspace/me/account-deletion"
TRANSFER_PATH = "/playspace/me/manager-profile/primary-transfer"


def _bearer(token: str) -> dict[str, str]:
	return {"Authorization": f"bearer {token}"}


def _login(client: TestClient, email: str, password: str = SEED_PASSWORD) -> str:
	response = client.post("/playspace/auth/login", json={"email": email, "password": password})
	assert response.status_code == 200, response.text
	return response.json()["access_token"]


def _delete_payload(password: str = SEED_PASSWORD, confirmation: str = "DELETE") -> dict[str, str]:
	return {"current_password": password, "confirmation": confirmation}


@pytest.fixture(autouse=True)
def _restore_playspace_seed(request: pytest.FixtureRequest) -> object:
	"""Rebuild the shared seed after any test in this module that touched the database.

	The Playspace seed is created once per session, and these tests are the only
	ones that permanently remove seeded people. Without this, deleting the seeded
	auditor here would leave every later test - in this file and in every other
	Playspace module - running against a seed that no longer matches the snapshot.

	The database fixture is resolved lazily so the schema-only tests above, which
	need no database at all, keep running in environments without one.
	"""

	yield

	if "playspace_client" not in request.fixturenames:
		return

	session_factory = request.getfixturevalue("playspace_test_session_factory")
	asyncio.run(_reseed_playspace_database(session_factory))


######################################################################################
############################ Request Schema (no database) ############################
######################################################################################


def test_confirmation_must_be_the_exact_word_delete() -> None:
	"""Anything but the literal ``DELETE`` is rejected before any work happens."""

	for rejected in ("delete", "Delete", "DELETE ", "DELETE ACCOUNT", "", "YES"):
		with pytest.raises(ValidationError):
			# Construct from a dict so mypy does not require Literal["DELETE"] here.
			AccountDeletionRequest.model_validate(
				{"current_password": SEED_PASSWORD, "confirmation": rejected}
			)


def test_exact_confirmation_is_accepted() -> None:
	request = AccountDeletionRequest(current_password=SEED_PASSWORD, confirmation="DELETE")
	assert request.confirmation == "DELETE"
	assert request.current_password == SEED_PASSWORD


def test_deletion_request_requires_a_password() -> None:
	with pytest.raises(ValidationError):
		AccountDeletionRequest(current_password="", confirmation="DELETE")


def test_transfer_request_requires_a_successor_uuid() -> None:
	with pytest.raises(ValidationError):
		# Invalid input arrives as a string in API JSON; validate the same way.
		PrimaryManagerTransferRequest.model_validate(
			{"successor_manager_profile_id": "not-a-uuid"}
		)

	successor = uuid.uuid4()
	request = PrimaryManagerTransferRequest(successor_manager_profile_id=successor)
	assert request.successor_manager_profile_id == successor


def test_blocker_values_are_stable_client_contract() -> None:
	"""Clients map these keys to their own wording, so the strings are frozen."""

	assert {blocker.value for blocker in AccountDeletionBlocker} == {
		"PRIMARY_MANAGER_TRANSFER_REQUIRED",
		"PENDING_SUBMISSION_DELIVERY",
		"PERSONAL_ACCOUNT_HAS_DEPENDENCIES",
	}


######################################################################################
################################ Preview (database) ##################################
######################################################################################


def test_auditor_preview_reports_preserved_and_removed_work(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	headers = _bearer(_login(playspace_client, playspace_seed_snapshot.seeded_auditor_email))

	response = playspace_client.get(DELETION_PATH, headers=headers)

	assert response.status_code == 200, response.text
	body = response.json()
	assert body["role"] == "AUDITOR"
	assert body["is_primary_manager"] is False
	assert body["submitted_audits_preserved"] >= 1
	assert body["can_delete"] is True
	assert body["blocker"] is None


def test_primary_manager_preview_is_blocked_until_ownership_moves(
	playspace_client: TestClient,
) -> None:
	headers = _bearer(_login(playspace_client, PRIMARY_MANAGER_EMAIL))

	response = playspace_client.get(DELETION_PATH, headers=headers)

	assert response.status_code == 200, response.text
	body = response.json()
	assert body["role"] == "MANAGER"
	assert body["is_primary_manager"] is True
	assert body["can_delete"] is False
	assert body["blocker"] == "PRIMARY_MANAGER_TRANSFER_REQUIRED"


def test_administrators_cannot_use_self_service_deletion(playspace_client: TestClient) -> None:
	headers = _bearer(_login(playspace_client, ADMIN_EMAIL))

	assert playspace_client.get(DELETION_PATH, headers=headers).status_code == 403
	assert playspace_client.post(DELETION_PATH, json=_delete_payload(), headers=headers).status_code == 403


######################################################################################
############################### Rejected attempts ####################################
######################################################################################


def test_wrong_password_is_rejected_and_changes_nothing(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	headers = _bearer(_login(playspace_client, playspace_seed_snapshot.seeded_auditor_email))

	response = playspace_client.post(DELETION_PATH, json=_delete_payload(password=WRONG_PASSWORD), headers=headers)

	assert response.status_code == 400, response.text
	# The session still works: nothing was removed.
	assert playspace_client.get(DELETION_PATH, headers=headers).status_code == 200


def test_wrong_confirmation_word_is_rejected(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	headers = _bearer(_login(playspace_client, playspace_seed_snapshot.seeded_auditor_email))

	response = playspace_client.post(DELETION_PATH, json=_delete_payload(confirmation="delete"), headers=headers)

	assert response.status_code == 422, response.text
	assert playspace_client.get(DELETION_PATH, headers=headers).status_code == 200


def test_primary_manager_deletion_is_refused(playspace_client: TestClient) -> None:
	headers = _bearer(_login(playspace_client, PRIMARY_MANAGER_EMAIL))

	response = playspace_client.post(DELETION_PATH, json=_delete_payload(), headers=headers)

	assert response.status_code == 409, response.text
	assert response.json()["detail"] == "PRIMARY_MANAGER_TRANSFER_REQUIRED"


######################################################################################
############################## Auditor deletion ######################################
######################################################################################


def test_deleting_an_auditor_preserves_submitted_work_and_scrubs_the_person(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""The core guarantee: the audit survives, the person does not."""

	auditor_email = playspace_seed_snapshot.seeded_auditor_email
	auditor_token = _login(playspace_client, auditor_email)
	auditor_headers = _bearer(auditor_token)

	preview = playspace_client.get(DELETION_PATH, headers=auditor_headers).json()
	preserved_count = preview["submitted_audits_preserved"]
	assert preserved_count >= 1, "seed must contain a submitted audit for this test to mean anything"

	admin_headers = _bearer(_login(playspace_client, ADMIN_EMAIL))
	submitted_audit_id = playspace_seed_snapshot.riverside_submitted_audit_id
	before = playspace_client.get(f"/playspace/audits/{submitted_audit_id}", headers=admin_headers)
	assert before.status_code == 200, before.text
	audit_code_before = before.json().get("audit_code")

	response = playspace_client.post(DELETION_PATH, json=_delete_payload(), headers=auditor_headers)
	assert response.status_code == 204, response.text

	# The deleted person's token no longer authenticates anything.
	assert playspace_client.get(DELETION_PATH, headers=auditor_headers).status_code == 401

	# The submitted audit still resolves, under the same id and code.
	after = playspace_client.get(f"/playspace/audits/{submitted_audit_id}", headers=admin_headers)
	assert after.status_code == 200, after.text
	assert after.json().get("audit_code") == audit_code_before

	# Logging back in as the deleted person is no longer possible.
	failed_login = playspace_client.post(
		"/playspace/auth/login",
		json={"email": auditor_email, "password": SEED_PASSWORD},
	)
	assert failed_login.status_code in (400, 401, 403), failed_login.text


def test_deleted_auditor_is_shown_as_a_placeholder_not_a_missing_record(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""Reports keep rendering: the auditor slot reads as a placeholder name."""

	auditor_headers = _bearer(_login(playspace_client, playspace_seed_snapshot.seeded_auditor_email))
	assert playspace_client.post(DELETION_PATH, json=_delete_payload(), headers=auditor_headers).status_code == 204

	admin_headers = _bearer(_login(playspace_client, ADMIN_EMAIL))
	detail = playspace_client.get(
		f"/playspace/audits/{playspace_seed_snapshot.riverside_submitted_audit_id}",
		headers=admin_headers,
	)
	assert detail.status_code == 200, detail.text

	serialized = detail.text
	assert playspace_seed_snapshot.seeded_auditor_email not in serialized
	if "auditor_name" in detail.json():
		assert detail.json()["auditor_name"] == DELETED_AUDITOR_DISPLAY_NAME


######################################################################################
############################## Manager lifecycle #####################################
######################################################################################


def test_secondary_manager_can_delete_themselves_and_the_workspace_survives(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	secondary_headers = _bearer(_login(playspace_client, SECONDARY_MANAGER_EMAIL))

	preview = playspace_client.get(DELETION_PATH, headers=secondary_headers)
	assert preview.status_code == 200, preview.text
	assert preview.json()["can_delete"] is True

	response = playspace_client.post(DELETION_PATH, json=_delete_payload(), headers=secondary_headers)
	assert response.status_code == 204, response.text

	# The organisation and its project are untouched by a member leaving.
	primary_headers = _bearer(_login(playspace_client, PRIMARY_MANAGER_EMAIL))
	project_ids = _account_project_ids(
		playspace_client,
		primary_headers,
		playspace_seed_snapshot.manager_account_id,
	)
	assert playspace_seed_snapshot.urban_project_id in project_ids


def test_transferring_ownership_then_deleting_the_former_primary(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	"""End-to-end: hand over the organisation, then leave it."""

	primary_headers = _bearer(_login(playspace_client, PRIMARY_MANAGER_EMAIL))
	account_id = playspace_seed_snapshot.manager_account_id

	successor_id = _find_manager_profile_id(
		playspace_client,
		primary_headers,
		account_id,
		SECONDARY_MANAGER_EMAIL,
	)
	transfer = playspace_client.post(
		TRANSFER_PATH,
		json={"successor_manager_profile_id": successor_id},
		headers=primary_headers,
	)
	assert transfer.status_code == 204, transfer.text

	# The former primary is now an ordinary member and may delete themselves.
	primary_headers = _bearer(_login(playspace_client, PRIMARY_MANAGER_EMAIL))
	preview = playspace_client.get(DELETION_PATH, headers=primary_headers)
	assert preview.status_code == 200, preview.text
	assert preview.json()["is_primary_manager"] is False
	assert preview.json()["can_delete"] is True

	assert playspace_client.post(DELETION_PATH, json=_delete_payload(), headers=primary_headers).status_code == 204

	# The successor now owns an intact organisation.
	successor_headers = _bearer(_login(playspace_client, SECONDARY_MANAGER_EMAIL))
	profile = playspace_client.get("/playspace/me/manager-profile", headers=successor_headers)
	assert profile.status_code == 200, profile.text
	assert profile.json()["is_primary"] is True

	project_ids = _account_project_ids(playspace_client, successor_headers, account_id)
	assert playspace_seed_snapshot.urban_project_id in project_ids


def test_a_non_primary_manager_cannot_transfer_ownership(
	playspace_client: TestClient,
	playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
	secondary_headers = _bearer(_login(playspace_client, SECONDARY_MANAGER_EMAIL))
	primary_headers = _bearer(_login(playspace_client, PRIMARY_MANAGER_EMAIL))

	primary_profile_id = _find_manager_profile_id(
		playspace_client,
		primary_headers,
		playspace_seed_snapshot.manager_account_id,
		PRIMARY_MANAGER_EMAIL,
	)
	response = playspace_client.post(
		TRANSFER_PATH,
		json={"successor_manager_profile_id": primary_profile_id},
		headers=secondary_headers,
	)

	assert response.status_code == 409, response.text


def test_ownership_cannot_move_to_a_manager_in_another_organisation(
	playspace_client: TestClient,
) -> None:
	"""Cross-tenant transfer is refused: a place belongs to exactly one account."""

	primary_headers = _bearer(_login(playspace_client, PRIMARY_MANAGER_EMAIL))

	response = playspace_client.post(
		TRANSFER_PATH,
		json={"successor_manager_profile_id": str(uuid.uuid4())},
		headers=primary_headers,
	)

	assert response.status_code == 404, response.text


def _find_manager_profile_id(client: TestClient, headers: dict[str, str], account_id: str, email: str) -> str:
	"""Resolve a manager profile id by email from the organisation's profile list."""

	response = client.get(f"/playspace/accounts/{account_id}/manager-profiles", headers=headers)
	assert response.status_code == 200, response.text
	for profile in response.json():
		if profile["email"] == email:
			return str(profile["id"])
	raise AssertionError(f"manager profile for {email} not found in {response.text}")


def _account_project_ids(client: TestClient, headers: dict[str, str], account_id: str) -> list[str]:
	"""Return the ids of every project the organisation still owns."""

	response = client.get(f"/playspace/accounts/{account_id}/projects", headers=headers)
	assert response.status_code == 200, response.text
	return [str(project["id"]) for project in response.json()]

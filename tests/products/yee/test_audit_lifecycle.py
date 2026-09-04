"""YEE audit lifecycle integration tests.

These exercise the seeded auditor flow end to end against the per-product YEE
schema produced by the `yee` Alembic branch: status stub → instrument →
audit-state → draft → submit → list → fetch, plus the seeded instrument's
version metadata.
"""

from __future__ import annotations

import asyncio
from collections import Counter
from typing import cast

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import (
	AccountType,
	Audit,
	AuditorInvite,
	AuditStatus,
	Instrument,
	ManagerInvite,
	User,
	YeeAuditSubmission,
)
from app.products.yee.schemas.audits import CanonicalScoreSnapshot
from app.products.yee.services.audits import _decode_draft_payload
from app.products.yee.services.scoring import score_yee_responses
from app.products.yee.services.scoring_types import JsonValue
from app.seed import (
	YEE_PLACE_COMMONS_ID,
	YEE_PLACE_GREEN_ID,
	YEE_PLACE_LIBRARY_ID,
	YEE_PLACE_PLAZA_ID,
	YEE_SUBMISSION_HUB_ID,
	_build_yee_entities,
)
from tests.products.yee._helpers import (
	SEED_AUDITOR_THREE_EMAIL,
	_bearer_headers,
	_login_auditor,
)


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
		f"/yee/places/{YEE_PLACE_GREEN_ID}/audit-state",
		headers=_bearer_headers(token),
	)
	assert response.status_code == 200, response.text
	assert response.json()["status"] == "NOT_STARTED"


def test_seeded_in_progress_audit_reports_draft_state(yee_client: TestClient) -> None:
	"""Seeded in-progress audits remain resumable even with fallback seed keys."""

	token = _login_auditor(yee_client, email=SEED_AUDITOR_THREE_EMAIL)
	response = yee_client.get(
		f"/yee/places/{YEE_PLACE_COMMONS_ID}/audit-state",
		headers=_bearer_headers(token),
	)
	assert response.status_code == 200, response.text
	assert response.json()["status"] == "DRAFT"
	assert response.json()["audit_id"] is not None


def test_seeded_in_progress_audit_can_be_saved_again(yee_client: TestClient) -> None:
	"""Auditor 3 can update the seeded Commons draft without tripping the save path."""

	token = _login_auditor(yee_client, email=SEED_AUDITOR_THREE_EMAIL)
	response = yee_client.put(
		f"/yee/places/{YEE_PLACE_COMMONS_ID}/draft",
		headers=_bearer_headers(token),
		json={
			"participant_info": {"total_minutes": 24},
			"responses": {
				"QID22": "3",
				"QID24": "1",
			},
		},
	)
	assert response.status_code == 200, response.text
	assert response.json()["status"] == "DRAFT"
	assert response.json()["audit_id"] is not None
	assert response.json()["participant_info"]["total_minutes"] == 24
	assert response.json()["responses"]["QID22"] == "3"


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
	list_item = next(item for item in listing.json() if item["id"] == submission_id)
	assert list_item["total_raw_maximum"] == 122
	assert list_item["total_weighted_maximum"] == 0.0

	# And is fetchable by id.
	detail = yee_client.get(f"/yee/audits/{submission_id}", headers=headers)
	assert detail.status_code == 200, detail.text
	assert detail.json()["place_id"] == str(YEE_PLACE_PLAZA_ID)

	# audit-state now reports the submitted record.
	state = yee_client.get(f"{place_path}/audit-state", headers=headers)
	assert state.status_code == 200, state.text
	assert state.json()["status"] == "SUBMITTED"
	assert state.json()["score"]["total_raw_maximum"] == 122
	assert state.json()["score"]["total_weighted_maximum"] == 0.0


def test_seeded_submitted_audit_is_visible_to_auditor(yee_client: TestClient) -> None:
	"""A seeded SUBMITTED audit shows up for its auditor instead of NOT_STARTED.

	Regression guard: the seed used to create SUBMITTED ``Audit`` rows with no
	matching ``yee_audit_submissions`` row, so Demo Auditor 3's Maple Library
	Plaza audit (which exists in the DB) rendered as "not started" on the auditor
	dashboard and was missing from ``/my-audits``.
	"""

	token = _login_auditor(yee_client, email=SEED_AUDITOR_THREE_EMAIL)
	headers = _bearer_headers(token)

	listing = yee_client.get("/yee/my-audits", headers=headers)
	assert listing.status_code == 200, listing.text
	library_items = [item for item in listing.json() if item["place_id"] == str(YEE_PLACE_LIBRARY_ID)]
	assert len(library_items) == 1, listing.json()
	assert library_items[0]["place_name"] == "Maple Library Plaza"
	assert library_items[0]["total_score"] > 0

	state = yee_client.get(f"/yee/places/{YEE_PLACE_LIBRARY_ID}/audit-state", headers=headers)
	assert state.status_code == 200, state.text
	assert state.json()["status"] == "SUBMITTED"
	assert state.json()["submission_id"] is not None


def test_my_audits_keeps_unresolvable_corrupt_submission_visible(
	yee_client: TestClient,
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	"""One corrupt historical snapshot must not hide the auditor's whole list."""

	async def corrupt_submission() -> tuple[dict[str, object], str | None, str | None, int]:
		async with yee_test_session_factory() as session:
			submission = await session.get(YeeAuditSubmission, YEE_SUBMISSION_HUB_ID)
			assert submission is not None
			original = (
				dict(submission.scores_json),
				submission.instrument_key,
				submission.instrument_version,
				submission.total_score,
			)
			submission.scores_json = {"corrupt": True}
			submission.instrument_key = "yee"
			submission.instrument_version = "missing-my-audits-version"
			await session.commit()
			return original

	async def restore_submission(
		scores_json: dict[str, object],
		instrument_key: str | None,
		instrument_version: str | None,
	) -> None:
		async with yee_test_session_factory() as session:
			submission = await session.get(YeeAuditSubmission, YEE_SUBMISSION_HUB_ID)
			assert submission is not None
			submission.scores_json = scores_json
			submission.instrument_key = instrument_key
			submission.instrument_version = instrument_version
			await session.commit()

	original_scores, original_key, original_version, original_total = asyncio.run(corrupt_submission())
	headers = _bearer_headers(_login_auditor(yee_client))
	try:
		response = yee_client.get("/yee/my-audits", headers=headers)
		assert response.status_code == 200, response.text
		row = next(item for item in response.json() if item["id"] == str(YEE_SUBMISSION_HUB_ID))
		assert row["total_score"] == original_total
		assert row["total_raw_maximum"] is None
		assert row["total_weighted_maximum"] is None
	finally:
		asyncio.run(restore_submission(original_scores, original_key, original_version))


def test_every_seeded_submitted_audit_has_a_submission() -> None:
	entities = _build_yee_entities()
	submitted_audits = [
		audit for audit in entities if isinstance(audit, Audit) and audit.status == AuditStatus.SUBMITTED
	]
	submissions_by_key = {
		(submission.auditor_id, submission.place_id): submission
		for submission in entities
		if isinstance(submission, YeeAuditSubmission)
	}
	assert submitted_audits, "expected the seed to contain submitted audits"

	for audit in submitted_audits:
		key = (audit.auditor_profile_id, audit.place_id)
		submission = submissions_by_key.get(key)
		assert submission is not None, f"missing YeeAuditSubmission for {key}"
		assert audit.summary_score == float(submission.total_score)
		assert audit.submitted_at == submission.submitted_at
		assert audit.total_minutes == submission.participant_info_json["total_minutes"]
		assert audit.responses_json == submission.responses_json
		assert audit.scores_json["canonical_score"] == submission.scores_json
		CanonicalScoreSnapshot.model_validate(audit.scores_json["canonical_score"])

		recomputed = score_yee_responses(
			cast(dict[str, JsonValue], submission.responses_json),
			cast(dict[str, JsonValue], submission.participant_info_json),
		)
		assert audit.scores_json["total_score"] == recomputed["total_score"]
		assert submission.total_score == recomputed["total_score"]


def test_seeded_draft_uses_real_draft_payload_shape() -> None:
	entities = _build_yee_entities()
	drafts = [audit for audit in entities if isinstance(audit, Audit) and audit.status == AuditStatus.IN_PROGRESS]
	assert drafts, "expected at least one realistic seeded draft"

	for draft in drafts:
		participant_info, responses = _decode_draft_payload(draft)
		assert participant_info
		assert responses
		assert draft.responses_json == {
			"participant_info": participant_info,
			"responses": responses,
		}
		assert isinstance(participant_info.get("domain_weights"), dict)
		assert draft.total_minutes == participant_info["total_minutes"]
		recomputed = score_yee_responses(cast(dict[str, JsonValue], responses), participant_info)
		assert draft.summary_score == float(int(recomputed["total_score"]))
		assert draft.scores_json["total_score"] == recomputed["total_score"]
		assert draft.scores_json["canonical_score"] == recomputed["canonical_score"]
		assert recomputed["matched_scored_answers"] > 0


def test_seeded_reports_have_same_place_multi_auditor_comparison_set() -> None:
	entities = _build_yee_entities()
	submissions = [entity for entity in entities if isinstance(entity, YeeAuditSubmission)]
	counts_by_place = Counter(submission.place_id for submission in submissions)
	assert any(count >= 3 for count in counts_by_place.values()), counts_by_place

	for place_id, count in counts_by_place.items():
		if count < 3:
			continue
		place_scores = [submission.total_score for submission in submissions if submission.place_id == place_id]
		assert len(set(place_scores)) > 1, "comparison set should include scoring variation"
		break


def test_seeded_identity_matrix_includes_pending_and_expired_states() -> None:
	entities = _build_yee_entities()
	auditor_invites = [entity for entity in entities if isinstance(entity, AuditorInvite)]
	manager_invites = [entity for entity in entities if isinstance(entity, ManagerInvite)]
	users = [entity for entity in entities if isinstance(entity, User)]

	assert any(invite.accepted_at is None and invite.auditor_id is None for invite in auditor_invites)
	assert any(invite.accepted_at is None and invite.expires_at.year <= 2026 for invite in auditor_invites)
	assert any(invite.accepted_at is None and invite.accepted_by_user_id is None for invite in manager_invites)
	assert any(invite.accepted_at is not None and invite.accepted_by_user_id is not None for invite in manager_invites)
	assert any(
		user.account_type == AccountType.AUDITOR
		and user.account_id is None
		and not user.email_verified
		and not user.approved
		and not user.profile_completed
		for user in users
	)


def test_build_yee_entities_instrument_is_active_root_version() -> None:
	"""The seeded YEE instrument is the active root of version history."""

	entities = _build_yee_entities()
	instruments = [entity for entity in entities if isinstance(entity, Instrument)]

	assert len(instruments) >= 1
	assert any(instrument.is_active is True and instrument.parent_instrument_id is None for instrument in instruments)

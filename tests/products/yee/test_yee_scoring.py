"""YEE scoring integration tests (Flow N — zero prior coverage).

Validates the two-layer scoring pipeline end-to-end:

Layer 1 — ``score_yee_responses`` (raw scoring)
    The score-preview route (``POST /yee/audits/score``) produces the same
    ``ScoreResult`` that the pure ``score_yee_responses`` oracle computes for
    identical responses. Pure unit tests verify golden hand-computed totals.

Layer 2 — ``_build_submission_scores`` (weighted scoring)
    The dashboard audit-edit state (``GET /yee/dashboard/audits/{id}/edit``)
    exposes score and participant_info that match what ``_build_submission_scores``
    computes from the stored ``section_scores`` and ``participant_info``.
    Verified end-to-end via the seeded HUB submission (all-domain max profile)
    which has real instrument-valid responses.

Golden edge cases:
    Empty / zero domain weights produce ``total_weighted_score == 0.0`` and
    all ``weighted_domain_scores`` are ``0.0``.

A single-item golden case with a hand-computed expected ``total_score``.

The crafted responses use real QSF item IDs with valid choice/answer pairs
taken from the instrument's ``GradingData``.
"""

from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.seed import (
	YEE_AUDIT_HUB_ID,
	YEE_PLACE_HUB_ID,
	YEE_SEED_DOMAIN_WEIGHTS,
)
from app.yee_scoring import score_yee_responses
from app.products.yee.services.dashboard import (
	_build_submission_scores,
	_round_2,
	REPORT_DOMAIN_ITEM_COUNTS,
	REPORT_DOMAIN_ORDER,
)
from tests.products.yee._helpers import (
	SEED_MANAGER_EMAIL,
	_bearer_headers,
	_login_auditor,
)


# ---------------------------------------------------------------------------
# Crafted responses touching all 6 scored domains via real QSF item IDs.
#
# Each value is a matrix-like dict {choice_id: answer_id}.
# Item IDs and valid (choice, answer) pairs sourced from the instrument's
# GradingData (see ``app/yee_scoring.py:get_yee_instrument_data``).
# ---------------------------------------------------------------------------
CRAFTED_RESPONSES: dict[str, object] = {
	# Access presence (QID1#1): choice 1 = Yes, choice 2 = Yes
	"QID1#1": {"1": "1", "2": "1"},
	# Access condition (QID1#2): choice 1 = Great, choice 2 = Acceptable
	"QID1#2": {"1": "3", "2": "2"},
	# Amenities presence (QID12#1): choice 1 = Yes, choice 2 = No
	"QID12#1": {"1": "1", "2": "2"},
	# Activity Spaces presence (QID4#1): choice 1 = Yes
	"QID4#1": {"1": "1"},
	# Experience of Space (QID15#1): choice 2 = Yes a lot, choice 3 = Yes a little
	"QID15#1": {"2": "1", "3": "2"},
	# Aesthetics condition (QID16#1): choice 1 = Acceptable
	"QID16#1": {"1": "2"},
	# Use & Usability presence (QID19#1): choice 1 = Yes
	"QID19#1": {"1": "1"},
}

CRAFTED_DOMAIN_WEIGHTS = {
	"access": 3,
	"activitySpaces": 2,
	"amenities": 2,
	"experienceOfSpace": 1,
	"aestheticsAndCare": 3,
	"useAndUsability": 1,
}

# ---------------------------------------------------------------------------
# Test: Layer 1 pure oracle — score_yee_responses produces correct output
# ---------------------------------------------------------------------------


def test_score_yee_responses_oracle_golden_case() -> None:
	result = score_yee_responses(CRAFTED_RESPONSES)

	assert result["total_score"] == 8
	assert result["matched_scored_answers"] == 8

	section_scores = result["section_scores"]
	assert section_scores["Access: Presence, Condition, Provision"] == 5
	assert section_scores["Amenities: Presence, Condition Provision"] == 0
	assert section_scores["Activity Spaces: Presence, Condition, Provision"] == 0
	assert section_scores["Experience of Space:"] == 3
	assert section_scores["Aesthetics & Care: Presence, condition, provision"] == 0
	assert section_scores["Use & Usability: Presence, condition, provision"] == 0

	cat = result["category_scores"]
	assert cat["Score"] == 8
	assert cat["Access"] == 5
	assert cat["Activity"] == 0
	assert cat["Amenities"] == 0
	assert cat["Experience"] == 3
	assert cat["Aesthetics & Care"] == 0
	assert cat["Use & Usability"] == 0


def test_score_yee_responses_single_item_golden() -> None:
	result = score_yee_responses({"QID19#1": {"1": "1"}})
	assert result["total_score"] == 0
	assert result["matched_scored_answers"] == 1
	assert result["section_scores"]["Use & Usability: Presence, condition, provision"] == 0


def test_score_yee_responses_empty_responses() -> None:
	"""Empty responses produce zero scores across all categories."""

	result = score_yee_responses({})
	assert result["total_score"] == 0
	assert result["matched_scored_answers"] == 0
	assert all(value == 0 for value in result["section_scores"].values())


def test_score_yee_responses_unrecognized_item_ids_ignored() -> None:
	"""Item IDs not in the instrument's scoring_items are silently skipped."""

	result = score_yee_responses({"QID_FAKE": "1", "QID22": "3"})
	assert result["total_score"] == 0
	assert result["matched_scored_answers"] == 0


# ---------------------------------------------------------------------------
# Test: score-preview route matches pure oracle
# ---------------------------------------------------------------------------


def test_score_preview_route_matches_oracle(yee_client: TestClient) -> None:
	"""POST /yee/audits/score returns the same ScoreResult as the pure oracle.

	Oracle: ``score_yee_responses`` called directly on the same responses.
	"""

	expected = score_yee_responses(CRAFTED_RESPONSES)

	resp = yee_client.post(
		"/yee/audits/score",
		json={
			"place_id": str(uuid.uuid4()),  # not checked by the preview route
			"responses": CRAFTED_RESPONSES,
		},
	)
	assert resp.status_code == 200, resp.text
	body = resp.json()

	assert body["total_score"] == expected["total_score"]
	assert body["section_scores"] == expected["section_scores"]
	assert body["category_scores"] == expected["category_scores"]
	assert body["matched_scored_answers"] == expected["matched_scored_answers"]


# ---------------------------------------------------------------------------
# Test: Layer 2 pure oracle — _build_submission_scores
# ---------------------------------------------------------------------------


def test_build_submission_scores_oracle_with_weights() -> None:
	section_scores = {
		"Access: Presence, Condition, Provision": 7,
		"Amenities: Presence, Condition Provision": 1,
		"Activity Spaces: Presence, Condition, Provision": 1,
		"Experience of Space:": 3,
		"Aesthetics & Care: Presence, condition, provision": 2,
		"Use & Usability: Presence, condition, provision": 1,
	}

	raw, weighted, total_weighted = _build_submission_scores(
		section_scores,
		{"domain_weights": CRAFTED_DOMAIN_WEIGHTS},
	)

	assert raw == {
		"access": 7,
		"activitySpaces": 1,
		"amenities": 1,
		"experienceOfSpace": 3,
		"aestheticsAndCare": 2,
		"useAndUsability": 1,
	}

	assert weighted["access"] == 0.29
	assert weighted["activitySpaces"] == 0.02
	assert weighted["amenities"] == 0.02
	assert weighted["experienceOfSpace"] == 0.03
	assert weighted["aestheticsAndCare"] == 0.05
	assert weighted["useAndUsability"] == 0.01

	assert total_weighted == 0.42

	assert total_weighted == _round_2(sum(weighted.values()))


def test_build_submission_scores_zero_weights_produce_zero() -> None:
	"""Empty domain_weights -> total_weighted_score == 0.0, all weighted 0.0.

	Per the scoring rules, if total_weight_sum <= 0 the function returns
	empty weighted scores and 0.0 total.
	"""

	section_scores = {
		"Access: Presence, Condition, Provision": 7,
		"Amenities: Presence, Condition Provision": 1,
	}

	# Case 1: empty dict
	_raw, weighted, total_weighted = _build_submission_scores(section_scores, {"domain_weights": {}})
	assert total_weighted == 0.0
	assert all(v == 0.0 for v in weighted.values())

	# Case 2: no domain_weights key
	_raw2, weighted2, total_weighted2 = _build_submission_scores(section_scores, {})
	assert total_weighted2 == 0.0
	assert all(v == 0.0 for v in weighted2.values())

	# Case 3: all weights explicitly zero (invalid, coerced to 0)
	zero_weights = {d: 0 for d in REPORT_DOMAIN_ORDER}
	_raw3, weighted3, total_weighted3 = _build_submission_scores(section_scores, {"domain_weights": zero_weights})
	assert total_weighted3 == 0.0
	assert all(v == 0.0 for v in weighted3.values())


def test_build_submission_scores_internal_consistency() -> None:
	"""Weighted domain scores are self-consistent with the formula.

	For each domain: weighted_domain == round2(exact_normalized_weight * raw/item_count).
	total_weighted_score == round2(sum(weighted_domain_scores)).
	"""

	section_scores = {
		"Access: Presence, Condition, Provision": 10,
		"Amenities: Presence, Condition Provision": 5,
		"Activity Spaces: Presence, Condition, Provision": 8,
		"Experience of Space:": 6,
		"Aesthetics & Care: Presence, condition, provision": 12,
		"Use & Usability: Presence, condition, provision": 4,
	}
	weights = {
		"access": 2,
		"activitySpaces": 3,
		"amenities": 1,
		"experienceOfSpace": 2,
		"aestheticsAndCare": 3,
		"useAndUsability": 1,
	}

	raw, weighted, total_weighted = _build_submission_scores(
		section_scores,
		{"domain_weights": weights},
	)

	total_weight_sum = sum(weights.values())
	assert total_weight_sum == 12

	for domain in REPORT_DOMAIN_ORDER:
		normalized = weights[domain] / total_weight_sum
		expected_weighted = _round_2(normalized * (raw[domain] / REPORT_DOMAIN_ITEM_COUNTS[domain]))
		assert weighted[domain] == expected_weighted, f"Mismatch for {domain}"

	assert total_weighted == _round_2(sum(weighted.values()))


# ---------------------------------------------------------------------------
# Test: full submit flow with scoring verification (Layer 1 + Layer 2)
# ---------------------------------------------------------------------------


def test_seeded_hub_submission_scoring_end_to_end(yee_client: TestClient) -> None:
	"""Verify Layer 1 + Layer 2 scoring on the seeded HUB submission.

	The seeded HUB submission (auditor-1, all-domain max profile) has instrument-valid
	responses built by ``_build_yee_domain_scored_responses``. The manager fetches
	the audit via the dashboard edit endpoint; the test re-scores the stored
	responses with the ``score_yee_responses`` oracle and verifies the result
	matches what the endpoint returns. Then ``_build_submission_scores`` is used
	to verify Layer 2 weighted scores are consistent.

	Oracles: ``score_yee_responses``, ``_build_submission_scores``.
	"""

	manager_token = _login_auditor(yee_client, email=SEED_MANAGER_EMAIL)
	manager_headers = _bearer_headers(manager_token)

	# Fetch the seeded HUB audit via the manager edit endpoint
	audit_id = str(YEE_AUDIT_HUB_ID)
	edit_resp = yee_client.get(
		f"/yee/dashboard/audits/{audit_id}/edit",
		headers=manager_headers,
	)
	assert edit_resp.status_code == 200, edit_resp.text
	edit_body = edit_resp.json()

	# The edit state must have a submission_id (seeded submission exists)
	assert edit_body["submission_id"] is not None
	assert edit_body["place_id"] == str(YEE_PLACE_HUB_ID)

	# ---- Layer 1: re-score the stored responses with the oracle ----
	stored_responses = edit_body["responses"]
	assert isinstance(stored_responses, dict) and len(stored_responses) > 0

	expected_score = score_yee_responses(stored_responses)

	# The edit endpoint calls score_yee_responses on the submission's responses
	assert edit_body["score"]["total_score"] == expected_score["total_score"]
	assert edit_body["score"]["section_scores"] == expected_score["section_scores"]
	assert edit_body["score"]["category_scores"] == expected_score["category_scores"]
	assert edit_body["score"]["matched_scored_answers"] == expected_score["matched_scored_answers"]

	# Total score must be positive for a quality=1.0 submission
	assert expected_score["total_score"] > 0
	assert expected_score["matched_scored_answers"] > 0

	# ---- Layer 2: verify weighted scores via _build_submission_scores oracle ----
	stored_participant_info = edit_body["participant_info"]
	assert stored_participant_info["domain_weights"] == dict(YEE_SEED_DOMAIN_WEIGHTS)

	expected_raw, expected_weighted, expected_total_weighted = _build_submission_scores(
		expected_score["section_scores"],
		stored_participant_info,
	)

	# The dashboard list also exposes weighted scores; verify consistency
	audits_list = yee_client.get("/yee/dashboard/audits", headers=manager_headers)
	assert audits_list.status_code == 200, audits_list.text

	hub_items = [a for a in audits_list.json() if a.get("id") == audit_id]
	assert len(hub_items) == 1, f"HUB audit not found in list: {audits_list.json()}"
	list_item = hub_items[0]

	assert list_item["total_raw_score"] == expected_score["total_score"]
	assert list_item["total_weighted_score"] == expected_total_weighted
	assert list_item["domain_weights"] == dict(YEE_SEED_DOMAIN_WEIGHTS)

	# Internal consistency: total_weighted == round(sum(weighted_domains), 2)
	assert expected_total_weighted == _round_2(sum(expected_weighted.values()))

	total_weight_sum = sum(dict(YEE_SEED_DOMAIN_WEIGHTS).values())
	for domain in REPORT_DOMAIN_ORDER:
		normalized = dict(YEE_SEED_DOMAIN_WEIGHTS)[domain] / total_weight_sum
		check_weighted = _round_2(normalized * (expected_raw[domain] / REPORT_DOMAIN_ITEM_COUNTS[domain]))
		assert expected_weighted[domain] == check_weighted, f"Weighted mismatch for {domain}"


def test_score_preview_with_zero_weights_produces_zero_weighted(yee_client: TestClient) -> None:
	"""Score-preview + empty domain_weights -> total_weighted_score == 0.0.

	Uses the score-preview endpoint (no persistence needed) to verify Layer 1,
	then verifies Layer 2 via the pure oracle with empty weights.

	This is the golden edge case from GROUND-TRUTH section 5.8.
	"""

	zero_weight_participant = {
		"total_minutes": 10,
		"domain_weights": {},
	}

	# Layer 1: score preview still returns correct raw total_score
	resp = yee_client.post(
		"/yee/audits/score",
		json={
			"place_id": str(uuid.uuid4()),
			"participant_info": zero_weight_participant,
			"responses": CRAFTED_RESPONSES,
		},
	)
	assert resp.status_code == 200, resp.text
	assert resp.json()["total_score"] == 8

	# Layer 2: _build_submission_scores with empty weights
	section_scores = resp.json()["section_scores"]
	_raw, weighted, total_weighted = _build_submission_scores(
		section_scores,
		zero_weight_participant,
	)
	assert total_weighted == 0.0
	for domain in REPORT_DOMAIN_ORDER:
		assert weighted[domain] == 0.0


# ---------------------------------------------------------------------------
# Test: auditor cannot access manager dashboard edit route (authz guard)
# ---------------------------------------------------------------------------


def test_auditor_cannot_access_dashboard_edit_route(yee_client: TestClient) -> None:
	"""Auditors get 403 when trying to access the manager audit edit endpoint."""

	auditor_token = _login_auditor(yee_client)
	auditor_headers = _bearer_headers(auditor_token)

	# Use a placeholder audit ID -- authz check fires before the 404
	resp = yee_client.get(
		f"/yee/dashboard/audits/{uuid.uuid4()}/edit",
		headers=auditor_headers,
	)
	assert resp.status_code == 403, resp.text

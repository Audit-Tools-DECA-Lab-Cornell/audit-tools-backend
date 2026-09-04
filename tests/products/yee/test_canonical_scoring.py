from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.models import Audit, AuditStatus
from app.products.yee.schemas.audits import CanonicalScoreSnapshot, ScoreResult, flatten_canonical_score
from app.products.yee.services.score_snapshots import stored_audit_score
from app.products.yee.services.scoring_engine import build_canonical_score_snapshot
from app.products.yee.services.scoring_spec import SCHEMA_V1_SCORING_CONTRACT
from app.products.yee.services.scoring_types import JsonValue


DOMAIN_WEIGHTS: dict[str, JsonValue] = {
	"access": 3,
	"activitySpaces": 2,
	"amenities": 2,
	"experienceOfSpace": 1,
	"aestheticsAndCare": 3,
	"useAndUsability": 1,
}


def test_canonical_scoring_uses_presence_condition_products() -> None:
	responses: dict[str, JsonValue] = {
		"QID1#1": {"1": "1", "2": "1"},
		"QID1#2": {"1": "3", "2": "2"},
		"QID12#1": {"1": "1"},
	}

	snapshot = build_canonical_score_snapshot(
		responses,
		{"domain_weights": DOMAIN_WEIGHTS},
		contract=SCHEMA_V1_SCORING_CONTRACT,
	)

	assert snapshot["raw"]["item_scores"]["access.q1"] == 3
	assert snapshot["raw"]["item_scores"]["access.q2"] == 2
	assert snapshot["raw"]["item_scores"]["amenities.q1"] == 0
	assert snapshot["raw"]["domain_scores"]["access"] == 5
	assert snapshot["raw"]["domain_scores"]["amenities"] == 0
	assert snapshot["raw"]["total_score"] == 5


def test_canonical_weighting_uses_domain_specific_max_average() -> None:
	responses: dict[str, JsonValue] = {
		"QID1#1": {"1": "1", "2": "1"},
		"QID1#2": {"1": "3", "2": "2"},
	}

	snapshot = build_canonical_score_snapshot(
		responses,
		{"domain_weights": DOMAIN_WEIGHTS},
		contract=SCHEMA_V1_SCORING_CONTRACT,
	)

	assert snapshot["meta"]["domain_item_counts"]["access"] == 6
	assert snapshot["meta"]["domain_max_average_scores"]["access"] == 2.33
	assert snapshot["weighted"]["raw_domain_weights"]["access"] == 3
	assert snapshot["weighted"]["normalized_domain_weights"]["access"] == 0.25
	assert snapshot["weighted"]["domain_average_scores"]["access"] == 0.83
	assert snapshot["weighted"]["weighted_domain_scores"]["access"] == 0.21
	assert snapshot["weighted"]["priority_gaps"]["access"] == 0.38
	assert snapshot["raw"]["domain_maximums"]["useAndUsability"] == 15
	assert snapshot["raw"]["total_maximum"] == 122
	assert snapshot["weighted"]["domain_maximums"]["access"] == 0.58
	assert snapshot["weighted"]["total_maximum"] == 2.32


def test_priority_gaps_use_exact_normalized_weights_before_rounding() -> None:
	equal_weights: dict[str, JsonValue] = {domain: 1 for domain in DOMAIN_WEIGHTS}

	snapshot = build_canonical_score_snapshot(
		{},
		{"domain_weights": equal_weights},
		contract=SCHEMA_V1_SCORING_CONTRACT,
	)

	assert snapshot["weighted"]["normalized_domain_weights"]["access"] == 0.17
	assert snapshot["weighted"]["priority_gaps"]["access"] == 0.39
	assert snapshot["weighted"]["domain_maximums"]["useAndUsability"] == 0.31
	# The exact 1/6 weights produce 2.25. Summing values calculated from the
	# rounded 0.17 weights would incorrectly produce 2.30.
	assert snapshot["weighted"]["total_maximum"] == 2.25
	validated = CanonicalScoreSnapshot.model_validate(snapshot)
	assert round(sum(validated.weighted.domain_maximums.values()), 2) == 2.24
	assert validated.weighted.total_maximum == 2.25


def test_old_canonical_snapshot_backfills_maxima_from_frozen_data() -> None:
	equal_weights: dict[str, JsonValue] = {domain: 1 for domain in DOMAIN_WEIGHTS}
	current_snapshot = build_canonical_score_snapshot(
		{},
		{"domain_weights": equal_weights},
		contract=SCHEMA_V1_SCORING_CONTRACT,
	)
	legacy_snapshot = CanonicalScoreSnapshot.model_validate(current_snapshot).model_dump(mode="python")
	legacy_snapshot["raw"].pop("domain_maximums")
	legacy_snapshot["raw"].pop("total_maximum")
	legacy_snapshot["weighted"].pop("domain_maximums")
	legacy_snapshot["weighted"].pop("total_maximum")
	# A frozen historical maximum that differs from schema-v1 proves the
	# backfill cannot consult the active contract or a static domain table.
	legacy_snapshot["meta"]["domain_max_average_scores"]["useAndUsability"] = 1.5

	validated = CanonicalScoreSnapshot.model_validate(legacy_snapshot)
	flattened = flatten_canonical_score(legacy_snapshot)

	assert validated.raw.domain_maximums["useAndUsability"] == 12
	assert validated.raw.total_maximum == 119
	assert validated.weighted.domain_maximums["useAndUsability"] == 0.25
	assert validated.weighted.total_maximum == 2.19
	assert flattened["raw_domain_maximums"]["useAndUsability"] == 12
	assert flattened["total_raw_maximum"] == 119
	assert flattened["weighted_domain_maximums"]["useAndUsability"] == 0.25
	assert flattened["total_weighted_maximum"] == 2.19


@pytest.mark.parametrize(
	"corruption",
	["partial", "negative", "wrong_type", "non_finite", "complete_inconsistent", "total_inconsistent"],
)
def test_backfill_replaces_invalid_raw_maxima(corruption: str) -> None:
	snapshot = build_canonical_score_snapshot(
		{},
		{"domain_weights": DOMAIN_WEIGHTS},
		contract=SCHEMA_V1_SCORING_CONTRACT,
	)
	payload = CanonicalScoreSnapshot.model_validate(snapshot).model_dump(mode="python")
	raw_maximums = dict(payload["raw"]["domain_maximums"])
	if corruption == "partial":
		raw_maximums.pop("access")
	elif corruption == "negative":
		raw_maximums["access"] = -1
	elif corruption == "wrong_type":
		raw_maximums["access"] = "14"
	elif corruption == "non_finite":
		raw_maximums["access"] = float("inf")
	elif corruption == "complete_inconsistent":
		raw_maximums["useAndUsability"] = 18
	payload["raw"]["domain_maximums"] = raw_maximums
	if corruption == "total_inconsistent":
		payload["raw"]["total_maximum"] = 125

	validated = CanonicalScoreSnapshot.model_validate(payload)

	assert validated.raw.domain_maximums == snapshot["raw"]["domain_maximums"]
	assert validated.raw.total_maximum == sum(validated.raw.domain_maximums.values()) == 122


@pytest.mark.parametrize("invalid_total", [float("nan"), float("inf"), -1, 122.0, "122", True, 125])
def test_backfill_replaces_invalid_or_inconsistent_raw_total(invalid_total: object) -> None:
	snapshot = build_canonical_score_snapshot(
		{},
		{"domain_weights": DOMAIN_WEIGHTS},
		contract=SCHEMA_V1_SCORING_CONTRACT,
	)
	payload = CanonicalScoreSnapshot.model_validate(snapshot).model_dump(mode="python")
	payload["raw"]["total_maximum"] = invalid_total

	validated = CanonicalScoreSnapshot.model_validate(payload)

	assert validated.raw.total_maximum == sum(validated.raw.domain_maximums.values()) == 122


@pytest.mark.parametrize(
	"corruption",
	["partial", "negative", "wrong_type", "non_finite", "complete_inconsistent"],
)
def test_backfill_replaces_invalid_weighted_domain_maxima(corruption: str) -> None:
	snapshot = build_canonical_score_snapshot(
		{},
		{"domain_weights": DOMAIN_WEIGHTS},
		contract=SCHEMA_V1_SCORING_CONTRACT,
	)
	payload = CanonicalScoreSnapshot.model_validate(snapshot).model_dump(mode="python")
	weighted_maximums = dict(payload["weighted"]["domain_maximums"])
	if corruption == "partial":
		weighted_maximums.pop("access")
	elif corruption == "negative":
		weighted_maximums["access"] = -0.01
	elif corruption == "wrong_type":
		weighted_maximums["access"] = "0.58"
	elif corruption == "non_finite":
		weighted_maximums["access"] = float("nan")
	elif corruption == "complete_inconsistent":
		weighted_maximums["access"] = 0.59
	payload["weighted"]["domain_maximums"] = weighted_maximums

	validated = CanonicalScoreSnapshot.model_validate(payload)

	assert validated.weighted.domain_maximums == snapshot["weighted"]["domain_maximums"]
	assert validated.weighted.total_maximum == snapshot["weighted"]["total_maximum"]


@pytest.mark.parametrize("invalid_total", [float("nan"), float("inf"), -1.0, "2.32", True, 999.0])
def test_backfill_replaces_invalid_weighted_total(invalid_total: object) -> None:
	snapshot = build_canonical_score_snapshot(
		{},
		{"domain_weights": DOMAIN_WEIGHTS},
		contract=SCHEMA_V1_SCORING_CONTRACT,
	)
	payload = CanonicalScoreSnapshot.model_validate(snapshot).model_dump(mode="python")
	payload["weighted"]["total_maximum"] = invalid_total

	validated = CanonicalScoreSnapshot.model_validate(payload)

	assert validated.weighted.total_maximum == 2.32


def test_score_result_prefers_canonical_maxima_over_stale_flat_fields() -> None:
	snapshot = build_canonical_score_snapshot(
		{},
		{"domain_weights": DOMAIN_WEIGHTS},
		contract=SCHEMA_V1_SCORING_CONTRACT,
	)

	result = ScoreResult(
		total_score=snapshot["raw"]["total_score"],
		section_scores=snapshot["raw"]["section_scores"],
		category_scores=snapshot["raw"]["category_scores"],
		matched_scored_answers=snapshot["raw"]["matched_scored_answers"],
		canonical_score=CanonicalScoreSnapshot.model_validate(snapshot),
		total_raw_maximum=999,
		raw_domain_maximums={"useAndUsability": 999},
		total_weighted_maximum=999.0,
		weighted_domain_maximums={"useAndUsability": 999.0},
	)

	assert result.total_raw_maximum == 122
	assert result.raw_domain_maximums["useAndUsability"] == 15
	assert result.total_weighted_maximum == 2.32
	assert result.weighted_domain_maximums["useAndUsability"] == 0.16


def test_audit_fallback_prefers_stored_canonical_score_shape() -> None:
	stored_snapshot = build_canonical_score_snapshot(
		{
			"QID1#1": {"1": "1", "2": "1"},
			"QID1#2": {"1": "3", "2": "2"},
		},
		{"domain_weights": DOMAIN_WEIGHTS},
		contract=SCHEMA_V1_SCORING_CONTRACT,
	)

	audit = Audit(
		project_id=uuid.uuid4(),
		place_id=uuid.uuid4(),
		auditor_profile_id=uuid.uuid4(),
		audit_code="YEE-STORED-SNAPSHOT",
		status=AuditStatus.SUBMITTED,
		responses_json={},
		scores_json={"total_score": 999, "canonical_score": stored_snapshot},
	)
	score = stored_audit_score(audit)

	assert score is not None
	assert score["total_score"] == stored_snapshot["raw"]["total_score"]
	assert score["section_scores"] == stored_snapshot["raw"]["section_scores"]
	assert score["canonical_score"]["raw"]["total_score"] == stored_snapshot["raw"]["total_score"]


def test_preview_route_returns_canonical_score_additively(yee_client: TestClient) -> None:
	responses = {
		"QID1#1": {"1": "1", "2": "1"},
		"QID1#2": {"1": "3", "2": "2"},
	}

	response = yee_client.post(
		"/yee/audits/score",
		json={
			"place_id": str(uuid.uuid4()),
			"participant_info": {"domain_weights": DOMAIN_WEIGHTS},
			"responses": responses,
		},
	)

	assert response.status_code == 200, response.text
	body = response.json()
	assert body["total_score"] == 5
	assert body["canonical_score"]["scoring_version"] == "yee_v2"
	assert body["canonical_score"]["raw"]["total_score"] == body["total_score"]
	assert body["canonical_score"]["raw"]["total_maximum"] == body["total_raw_maximum"] == 122
	assert body["canonical_score"]["raw"]["domain_maximums"]["useAndUsability"] == 15
	assert body["canonical_score"]["weighted"]["total_maximum"] == body["total_weighted_maximum"]
	assert body["canonical_score"]["weighted"]["priority_gaps"]["access"] == 0.38

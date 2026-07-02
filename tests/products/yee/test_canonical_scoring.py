from __future__ import annotations

import uuid

from fastapi.testclient import TestClient

from app.products.yee.services.dashboard import _score_from_audit_fallback
from app.products.yee.services.scoring_engine import build_canonical_score_snapshot
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

	snapshot = build_canonical_score_snapshot(responses, {"domain_weights": DOMAIN_WEIGHTS})

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

	snapshot = build_canonical_score_snapshot(responses, {"domain_weights": DOMAIN_WEIGHTS})

	assert snapshot["meta"]["domain_item_counts"]["access"] == 6
	assert snapshot["meta"]["domain_max_average_scores"]["access"] == 2.33
	assert snapshot["weighted"]["raw_domain_weights"]["access"] == 3
	assert snapshot["weighted"]["normalized_domain_weights"]["access"] == 0.25
	assert snapshot["weighted"]["domain_average_scores"]["access"] == 0.83
	assert snapshot["weighted"]["weighted_domain_scores"]["access"] == 0.21
	assert snapshot["weighted"]["priority_gaps"]["access"] == 0.38


def test_priority_gaps_use_exact_normalized_weights_before_rounding() -> None:
	equal_weights: dict[str, JsonValue] = {domain: 1 for domain in DOMAIN_WEIGHTS}

	snapshot = build_canonical_score_snapshot({}, {"domain_weights": equal_weights})

	assert snapshot["weighted"]["normalized_domain_weights"]["access"] == 0.17
	assert snapshot["weighted"]["priority_gaps"]["access"] == 0.39


def test_audit_fallback_prefers_stored_canonical_score_shape() -> None:
	stored_snapshot = build_canonical_score_snapshot(
		{
			"QID1#1": {"1": "1", "2": "1"},
			"QID1#2": {"1": "3", "2": "2"},
		},
		{"domain_weights": DOMAIN_WEIGHTS},
	)

	score = _score_from_audit_fallback(
		audit_scores_json={"total_score": 999, "canonical_score": stored_snapshot},
		participant_info={},
		responses={},
	)

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
	assert body["canonical_score"]["weighted"]["priority_gaps"]["access"] == 0.38

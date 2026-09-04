from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, Mock

import pytest
from typing import Any
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audit, AuditStatus, YeeAuditSubmission
from app.products.yee.services.runtime_scoring import RuntimeScorer, RuntimeScoringResolutionError
from app.products.yee.services.score_snapshots import (
	resolved_submission_score,
	stored_audit_score,
	stored_submission_score,
)
from app.products.yee.services.scoring import score_yee_responses
from app.products.yee.services.scoring_spec import SCHEMA_V1_SCORING_CONTRACT
from tests.products.yee._helpers import error_detail


def _canonical_score() -> dict[str, Any]:
	return dict(score_yee_responses({}, contract=SCHEMA_V1_SCORING_CONTRACT)["canonical_score"])


def test_valid_foreign_submission_snapshot_remains_authoritative() -> None:
	canonical = _canonical_score()
	canonical["scoring_version"] = "historical_foreign_algorithm"
	submission = YeeAuditSubmission(
		auditor_id=uuid.uuid4(),
		place_id=uuid.uuid4(),
		participant_info_json={},
		responses_json={"would": "score differently"},
		section_scores_json=canonical["raw"]["section_scores"],
		scores_json=canonical,
		scoring_version="historical_foreign_algorithm",
		total_score=canonical["raw"]["total_score"],
	)

	stored = stored_submission_score(submission)

	assert stored is not None
	assert stored["canonical_score"]["scoring_version"] == "historical_foreign_algorithm"


@pytest.mark.parametrize("legacy_without_maxima", [False, True])
def test_valid_stored_snapshot_resolves_without_instrument_query(legacy_without_maxima: bool) -> None:
	canonical = _canonical_score()
	if legacy_without_maxima:
		canonical["raw"].pop("domain_maximums")
		canonical["raw"].pop("total_maximum")
		canonical["weighted"].pop("domain_maximums")
		canonical["weighted"].pop("total_maximum")
	submission = YeeAuditSubmission(
		auditor_id=uuid.uuid4(),
		place_id=uuid.uuid4(),
		participant_info_json={},
		responses_json={},
		section_scores_json=canonical["raw"]["section_scores"],
		scores_json=canonical,
		scoring_version=canonical["scoring_version"],
		total_score=canonical["raw"]["total_score"],
		instrument_key="yee",
		instrument_version="query-must-not-run",
	)
	session = AsyncMock(spec=AsyncSession)

	resolved = asyncio.run(resolved_submission_score(RuntimeScorer(session), submission))

	assert resolved["canonical_score"]["raw"]["total_maximum"] == 122
	session.execute.assert_not_awaited()


def test_persisted_snapshot_without_maxima_is_backfilled_on_read() -> None:
	canonical = _canonical_score()
	canonical["raw"].pop("domain_maximums")
	canonical["raw"].pop("total_maximum")
	canonical["weighted"].pop("domain_maximums")
	canonical["weighted"].pop("total_maximum")
	submission = YeeAuditSubmission(
		auditor_id=uuid.uuid4(),
		place_id=uuid.uuid4(),
		participant_info_json={},
		responses_json={},
		section_scores_json=canonical["raw"]["section_scores"],
		scores_json=canonical,
		scoring_version=canonical["scoring_version"],
		total_score=canonical["raw"]["total_score"],
	)

	stored = stored_submission_score(submission)

	assert stored is not None
	assert stored["canonical_score"]["raw"]["total_maximum"] == 122
	assert stored["canonical_score"]["raw"]["domain_maximums"]["useAndUsability"] == 15
	assert stored["canonical_score"]["weighted"]["total_maximum"] == 0.0


def test_submission_snapshot_with_denormalized_mismatch_requires_resolution() -> None:
	canonical = _canonical_score()
	submission = YeeAuditSubmission(
		auditor_id=uuid.uuid4(),
		place_id=uuid.uuid4(),
		participant_info_json={},
		responses_json={},
		section_scores_json={},
		scores_json=canonical,
		scoring_version=canonical["scoring_version"],
		total_score=999,
	)

	assert stored_submission_score(submission) is None


def test_audit_prefers_valid_stored_canonical_before_responses() -> None:
	canonical = _canonical_score()
	audit = Audit(
		project_id=uuid.uuid4(),
		place_id=uuid.uuid4(),
		auditor_profile_id=uuid.uuid4(),
		audit_code="YEE-STORED",
		status=AuditStatus.SUBMITTED,
		submitted_at=datetime.now(timezone.utc),
		responses_json={"new": "response"},
		scores_json={"total_score": 0, "canonical_score": canonical},
	)

	stored = stored_audit_score(audit)

	assert stored is not None
	assert stored["canonical_score"] == canonical


def test_invalid_snapshot_with_missing_stamped_row_fails_without_mutating_submission() -> None:
	# Given a historical submission whose stored snapshot is invalid and exact instrument is missing
	submission = YeeAuditSubmission(
		auditor_id=uuid.uuid4(),
		place_id=uuid.uuid4(),
		participant_info_json={"keep": "participant"},
		responses_json={"keep": "response"},
		section_scores_json={"keep": 1},
		scores_json={"keep": "stored"},
		scoring_version="historical",
		total_score=7,
		instrument_key="yee",
		instrument_version="missing",
	)
	original = {
		"participant": dict(submission.participant_info_json),
		"responses": dict(submission.responses_json),
		"sections": dict(submission.section_scores_json),
		"scores": dict(submission.scores_json),
		"total": submission.total_score,
	}
	session = AsyncMock(spec=AsyncSession)
	result = Mock()
	result.scalars.return_value.all.return_value = []
	session.execute.return_value = result

	# When version-safe fallback tries to resolve the exact missing row
	with pytest.raises(RuntimeScoringResolutionError) as raised:
		asyncio.run(resolved_submission_score(RuntimeScorer(session), submission))

	# Then it fails visibly and leaves every historical value untouched
	assert error_detail(raised.value)["code"] == "missing_stamped_instrument"
	assert submission.participant_info_json == original["participant"]
	assert submission.responses_json == original["responses"]
	assert submission.section_scores_json == original["sections"]
	assert submission.scores_json == original["scores"]
	assert submission.total_score == original["total"]

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from pydantic import ValidationError

from app.models import Audit, YeeAuditSubmission
from app.products.yee.schemas.audits import CanonicalScoreSnapshot
from app.products.yee.services.runtime_scoring import InstrumentStamp, RuntimeScorer
from app.products.yee.services.scoring_types import JsonValue, LegacyScoreResult


def validated_canonical_snapshot(value: object) -> dict[str, Any] | None:
	if not isinstance(value, dict):
		return None
	try:
		return CanonicalScoreSnapshot.model_validate(value).model_dump(mode="python")
	except ValidationError:
		return None


def stored_canonical_snapshot(scores_json: object) -> dict[str, Any] | None:
	if not isinstance(scores_json, dict):
		return None
	direct = validated_canonical_snapshot(scores_json)
	if direct is not None:
		return direct
	return validated_canonical_snapshot(scores_json.get("canonical_score"))


def legacy_result_from_canonical(canonical_score: dict[str, Any]) -> LegacyScoreResult:
	canonical = CanonicalScoreSnapshot.model_validate(canonical_score)
	return cast(
		LegacyScoreResult,
		{
			"total_score": canonical.raw.total_score,
			"section_scores": dict(canonical.raw.section_scores),
			"category_scores": dict(canonical.raw.category_scores),
			"matched_scored_answers": canonical.raw.matched_scored_answers,
			"canonical_score": canonical.model_dump(mode="python"),
		},
	)


def audit_score_cache(score: LegacyScoreResult) -> dict[str, object]:
	return {
		"total_score": score["total_score"],
		"section_scores": score["section_scores"],
		"category_scores": score["category_scores"],
		"matched_scored_answers": score["matched_scored_answers"],
		"canonical_score": score["canonical_score"],
	}


def stored_submission_score(submission: YeeAuditSubmission) -> LegacyScoreResult | None:
	canonical = stored_canonical_snapshot(submission.scores_json)
	if canonical is None:
		return None
	if canonical["raw"]["total_score"] != submission.total_score:
		return None
	if canonical["scoring_version"] != submission.scoring_version:
		return None
	return legacy_result_from_canonical(canonical)


def stored_audit_score(audit: Audit) -> LegacyScoreResult | None:
	canonical = stored_canonical_snapshot(audit.scores_json)
	if canonical is None:
		return None
	return legacy_result_from_canonical(canonical)


async def resolved_submission_score(
	scorer: RuntimeScorer,
	submission: YeeAuditSubmission,
) -> LegacyScoreResult:
	stored = stored_submission_score(submission)
	if stored is not None:
		return stored
	return await scorer.score_for_stamp(
		InstrumentStamp(submission.instrument_key, submission.instrument_version),
		cast(Mapping[str, JsonValue], submission.responses_json),
		cast(Mapping[str, JsonValue], submission.participant_info_json),
	)


async def resolved_audit_score(
	scorer: RuntimeScorer,
	audit: Audit,
	*,
	participant_info: dict[str, Any],
	responses: dict[str, Any],
) -> LegacyScoreResult:
	stored = stored_audit_score(audit)
	if stored is not None:
		return stored
	return await scorer.score_for_stamp(
		InstrumentStamp(audit.instrument_key, audit.instrument_version),
		responses,
		participant_info,
	)

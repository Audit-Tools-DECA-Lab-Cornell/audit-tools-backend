"""Shared place rollup helpers for Playspace audit and survey axes."""

from __future__ import annotations

from app.models import AuditStatus, PlayspaceSubmission
from app.products.playspace.execution_mode_scope import (
	execution_mode_includes_audit,
	execution_mode_includes_survey,
)
from app.products.playspace.schemas import ScorePairResponse
from app.products.playspace.schemas.base import PlaceActivityStatus


def round_score_pair(pv: float | None, u: float | None) -> ScorePairResponse | None:
	"""Build the compact PV/U pair when both values are present."""

	if pv is None or u is None:
		return None
	return ScorePairResponse(pv=round(pv, 1), u=round(u, 1))


def overall_score_pair(
	audit_scores: ScorePairResponse | None,
	survey_scores: ScorePairResponse | None,
) -> ScorePairResponse | None:
	"""Combine available audit and survey mean score pairs into one overall pair."""

	if audit_scores is None:
		return survey_scores
	if survey_scores is None:
		return audit_scores
	return round_score_pair(audit_scores.pv + survey_scores.pv, audit_scores.u + survey_scores.u)


def derive_place_activity_status(
	submissions: list[PlayspaceSubmission],
) -> tuple[PlaceActivityStatus, PlaceActivityStatus]:
	"""Derive per-axis place activity from execution_mode; `both` counts on both axes."""

	place_audit_status: PlaceActivityStatus = "not_started"
	place_survey_status: PlaceActivityStatus = "not_started"
	audits = [submission for submission in submissions if execution_mode_includes_audit(submission.execution_mode)]
	surveys = [submission for submission in submissions if execution_mode_includes_survey(submission.execution_mode)]
	if any(submission.status == AuditStatus.SUBMITTED for submission in audits):
		place_audit_status = "submitted"
	elif any(submission.status in {AuditStatus.IN_PROGRESS, AuditStatus.PAUSED} for submission in audits):
		place_audit_status = "in_progress"
	if any(submission.status == AuditStatus.SUBMITTED for submission in surveys):
		place_survey_status = "submitted"
	elif any(submission.status in {AuditStatus.IN_PROGRESS, AuditStatus.PAUSED} for submission in surveys):
		place_survey_status = "in_progress"
	return place_audit_status, place_survey_status


def mean_partition_score_pair(
	submissions: list[PlayspaceSubmission],
	*,
	partition: str,
) -> ScorePairResponse | None:
	"""Average one partition's PV/U scores across submitted Playspace submissions."""

	pv_values: list[float] = []
	u_values: list[float] = []
	for submission in submissions:
		if submission.status != AuditStatus.SUBMITTED:
			continue
		if partition == "audit":
			pv_value = submission.audit_play_value_score
			u_value = submission.audit_usability_score
		else:
			pv_value = submission.survey_play_value_score
			u_value = submission.survey_usability_score
		if pv_value is None or u_value is None:
			continue
		pv_values.append(float(pv_value))
		u_values.append(float(u_value))

	if not pv_values or not u_values:
		return None
	return round_score_pair(sum(pv_values) / len(pv_values), sum(u_values) / len(u_values))

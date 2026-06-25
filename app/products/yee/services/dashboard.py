"""YEE-only dashboard service.

YEE manager/admin reporting reads (place comparisons and raw audit export) and
the manager audit edit / re-submit write model, plus the shared scoring and
participant-payload helpers they depend on. The shared top-level
`app/dashboard_router.py` keeps the thin route handlers and imports these.
"""

from __future__ import annotations

import re
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, cast

from fastapi import HTTPException
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from app.models import (
	Account,
	Audit,
	AuditStatus,
	Auditor,
	Place,
	Project,
	ProjectPlace,
	YeeAuditSubmission,
)
from app.products.yee.schemas.dashboard import (
	DashboardScoreResult,
	ManagerAuditEditRequest,
	ManagerAuditEditState,
	PlaceComparisonAuditItem,
	PlaceComparisonGroup,
	RawDataExportRow,
)
from app.yee_scoring import score_yee_responses

REPORT_DOMAIN_ORDER = (
	"access",
	"activitySpaces",
	"amenities",
	"experienceOfSpace",
	"aestheticsAndCare",
	"useAndUsability",
)

REPORT_DOMAIN_SCORE_MAXIMUMS: dict[str, int] = {
	"access": 14,
	"activitySpaces": 26,
	"amenities": 23,
	"experienceOfSpace": 20,
	"aestheticsAndCare": 24,
	"useAndUsability": 18,
}

REPORT_DOMAIN_ITEM_COUNTS: dict[str, int] = {
	"access": 8,
	"activitySpaces": 16,
	"amenities": 13,
	"experienceOfSpace": 10,
	"aestheticsAndCare": 14,
	"useAndUsability": 10,
}


def _format_timestamp(value: datetime | None) -> str:
	if value is None:
		return "Not yet"
	return value.strftime("%b %d, %Y")


def _display_auditor_code(code: str | None) -> str:
	if not code:
		return "AUD000"
	normalized = code.strip().upper()
	if normalized.startswith(("AUD", "ADT", "A")) and re.search(r"\d+$", normalized):
		digits_match = re.search(r"(\d+)$", normalized)
		if digits_match is not None:
			return f"AUD{int(digits_match.group(1)):03d}"
		return normalized
	digits_match = re.search(r"(\d+)$", normalized)
	if digits_match is not None:
		return f"AUD{int(digits_match.group(1)):03d}"
	return normalized


def _extract_score(scores_json: dict[str, object]) -> int:
	score = scores_json.get("total_score")
	return score if isinstance(score, int) else 0


def _empty_domain_scores() -> dict[str, int]:
	return {domain: 0 for domain in REPORT_DOMAIN_ORDER}


def _round_2(value: float) -> float:
	return round(value + 1e-9, 2)


def _empty_weighted_domain_scores() -> dict[str, float]:
	return {domain: 0.0 for domain in REPORT_DOMAIN_ORDER}


def _section_to_domain(section_name: str) -> str | None:
	normalized = section_name.lower()
	if "access" in normalized:
		return "access"
	if "activity spaces" in normalized:
		return "activitySpaces"
	if "amenities" in normalized:
		return "amenities"
	if "experience" in normalized:
		return "experienceOfSpace"
	if "aesthetics" in normalized:
		return "aestheticsAndCare"
	if "use & usability" in normalized:
		return "useAndUsability"
	return None


def _coerce_weight(value: object) -> int:
	if isinstance(value, int):
		return value if value in {1, 2, 3} else 0
	if isinstance(value, str) and value.isdigit():
		numeric = int(value)
		return numeric if numeric in {1, 2, 3} else 0
	return 0


def _extract_domain_weights(participant_info: dict[str, Any]) -> dict[str, int]:
	raw_weights = participant_info.get("domain_weights")
	if not isinstance(raw_weights, dict):
		return _empty_domain_scores()
	return {domain: _coerce_weight(raw_weights.get(domain)) for domain in REPORT_DOMAIN_ORDER}


def _build_submission_scores(
	section_scores: dict[str, Any],
	participant_info: dict[str, Any],
) -> tuple[dict[str, int], dict[str, float], float]:
	raw_domain_scores = _empty_domain_scores()
	for section_name, score in section_scores.items():
		domain = _section_to_domain(section_name)
		if domain is None or not isinstance(score, int):
			continue
		raw_domain_scores[domain] += score

	weights = _extract_domain_weights(participant_info)
	total_weight_sum = sum(weights.values())
	if total_weight_sum <= 0:
		return raw_domain_scores, _empty_weighted_domain_scores(), 0.0

	normalized_weights = {domain: _round_2(weights[domain] / total_weight_sum) for domain in REPORT_DOMAIN_ORDER}
	weighted_domain_scores = {
		domain: _round_2(normalized_weights[domain] * (raw_domain_scores[domain] / REPORT_DOMAIN_ITEM_COUNTS[domain]))
		for domain in REPORT_DOMAIN_ORDER
	}
	total_weighted_score = _round_2(sum(weighted_domain_scores.values()))
	return raw_domain_scores, weighted_domain_scores, total_weighted_score


def _decode_audit_participant_payload(audit: Audit) -> tuple[dict[str, Any], dict[str, Any]]:
	raw_payload = audit.responses_json if isinstance(audit.responses_json, dict) else {}
	participant_info = raw_payload.get("participant_info")
	responses = raw_payload.get("responses")
	if isinstance(participant_info, dict) and isinstance(responses, dict):
		return participant_info, responses
	return {}, raw_payload if isinstance(raw_payload, dict) else {}


def _dashboard_score_result(score: dict[str, Any]) -> DashboardScoreResult:
	return DashboardScoreResult(
		total_score=int(score.get("total_score", 0)),
		section_scores={str(key): int(value) for key, value in dict(score.get("section_scores", {})).items()},
		category_scores={str(key): int(value) for key, value in dict(score.get("category_scores", {})).items()},
		matched_scored_answers=int(score.get("matched_scored_answers", 0)),
	)


async def _repair_missing_yee_submission(
	session: AsyncSession,
	*,
	audit: Audit,
	place: Place,
	auditor: Auditor,
) -> YeeAuditSubmission | None:
	if audit.status != AuditStatus.SUBMITTED:
		return None

	participant_info, responses = _decode_audit_participant_payload(audit)
	if not responses:
		return None

	if not participant_info:
		participant_info = {
			"auditor_id": _display_auditor_code(auditor.auditor_code),
			"place_id": str(place.id),
			"place_name": place.name,
			"audit_date": audit.submitted_at.date().isoformat() if audit.submitted_at else None,
			"start_time": "",
			"finish_time": "",
			"total_minutes": audit.total_minutes or 0,
			"visit_frequency": "",
			"season": "",
			"weather": "",
			"domain_weights": _empty_domain_scores(),
			"comments": "",
			"section_comments": _empty_domain_scores(),
		}

	score = score_yee_responses(responses)
	submission = YeeAuditSubmission(
		auditor_id=audit.auditor_profile_id,
		place_id=audit.place_id,
		submitted_at=audit.submitted_at or datetime.now(timezone.utc),
		participant_info_json=participant_info,
		responses_json=responses,
		section_scores_json=score["section_scores"],
		total_score=score["total_score"],
	)
	session.add(submission)
	await session.flush()
	return submission


def _flatten_responses(responses: dict[str, Any]) -> dict[str, str]:
	flat: dict[str, str] = {}
	for key, value in responses.items():
		if isinstance(value, dict):
			for nested_key, nested_value in value.items():
				flat[f"response_{key}__{nested_key}"] = str(nested_value)
		else:
			flat[f"response_{key}"] = str(value)
	return flat


async def fetch_reporting_rows(
	session: AsyncSession,
	project_scope: ColumnElement[bool] | None,
) -> list[tuple[YeeAuditSubmission, Place, Project, str, str]]:
	stmt = (
		select(YeeAuditSubmission, Place, Project, Auditor.auditor_code, Account.name)
		.join(Place, YeeAuditSubmission.place_id == Place.id)
		.join(ProjectPlace, ProjectPlace.place_id == Place.id)
		.join(Project, ProjectPlace.project_id == Project.id)
		.join(Account, Project.account_id == Account.id)
		.join(Auditor, YeeAuditSubmission.auditor_id == Auditor.id)
		.order_by(Project.name.asc(), Place.name.asc(), YeeAuditSubmission.submitted_at.desc())
	)
	if project_scope is not None:
		stmt = stmt.where(project_scope)
	rows = (await session.execute(stmt)).all()
	return [
		(submission, place, project, auditor_code, organization_name)
		for submission, place, project, auditor_code, organization_name in rows
	]


async def fetch_place_comparison_groups(
	session: AsyncSession,
	project_scope: ColumnElement[bool] | None,
) -> list[PlaceComparisonGroup]:
	rows = await fetch_reporting_rows(session, project_scope)
	grouped: dict[str, dict[str, Any]] = defaultdict(dict)

	for submission, place, project, auditor_code, _organization_name in rows:
		group = grouped.setdefault(
			str(place.id),
			{
				"place_id": str(place.id),
				"place_name": place.name,
				"project_id": str(project.id),
				"project_name": project.name,
				"audits": [],
			},
		)
		weights = _extract_domain_weights(submission.participant_info_json)
		raw_domain_scores, weighted_domain_scores, total_weighted_score = _build_submission_scores(
			submission.section_scores_json,
			submission.participant_info_json,
		)
		group["audits"].append(
			PlaceComparisonAuditItem(
				audit_id=str(submission.id),
				auditor_id=_display_auditor_code(auditor_code),
				place_id=str(place.id),
				place_name=place.name,
				project_id=str(project.id),
				project_name=project.name,
				date=_format_timestamp(submission.submitted_at),
				total_raw_score=submission.total_score,
				total_weighted_score=total_weighted_score,
				domain_weights=weights,
				raw_domain_scores=raw_domain_scores,
				weighted_domain_scores=weighted_domain_scores,
			)
		)

	return [
		PlaceComparisonGroup(
			place_id=group["place_id"],
			place_name=group["place_name"],
			project_id=group["project_id"],
			project_name=group["project_name"],
			audits=group["audits"],
		)
		for group in grouped.values()
		if group["audits"]
	]


async def fetch_raw_data_rows(
	session: AsyncSession,
	project_scope: ColumnElement[bool] | None,
) -> list[RawDataExportRow]:
	rows = await fetch_reporting_rows(session, project_scope)
	export_rows: list[RawDataExportRow] = []
	for submission, place, project, auditor_code, organization_name in rows:
		participant_info = submission.participant_info_json
		raw_domain_scores, weighted_domain_scores, total_weighted_score = _build_submission_scores(
			submission.section_scores_json,
			participant_info,
		)
		export_rows.append(
			RawDataExportRow(
				audit_id=str(submission.id),
				auditor_generated_id=_display_auditor_code(auditor_code),
				organization=organization_name,
				place_id=str(place.id),
				place_name=place.name,
				project_id=str(project.id),
				project_name=project.name,
				date=str(participant_info.get("audit_date") or submission.submitted_at.date().isoformat()),
				submitted_at=submission.submitted_at.isoformat(),
				start_time=str(participant_info.get("start_time") or ""),
				finish_time=str(participant_info.get("finish_time") or ""),
				total_minutes=int(cast(int | str, participant_info.get("total_minutes") or 0)),
				visit_frequency=str(participant_info.get("visit_frequency") or ""),
				season=str(participant_info.get("season") or ""),
				weather=str(participant_info.get("weather") or ""),
				comments=str(participant_info.get("comments") or ""),
				raw_access=raw_domain_scores["access"],
				raw_activity_spaces=raw_domain_scores["activitySpaces"],
				raw_amenities=raw_domain_scores["amenities"],
				raw_experience_of_space=raw_domain_scores["experienceOfSpace"],
				raw_aesthetics_and_care=raw_domain_scores["aestheticsAndCare"],
				raw_use_and_usability=raw_domain_scores["useAndUsability"],
				weighted_access=weighted_domain_scores["access"],
				weighted_activity_spaces=weighted_domain_scores["activitySpaces"],
				weighted_amenities=weighted_domain_scores["amenities"],
				weighted_experience_of_space=weighted_domain_scores["experienceOfSpace"],
				weighted_aesthetics_and_care=weighted_domain_scores["aestheticsAndCare"],
				weighted_use_and_usability=weighted_domain_scores["useAndUsability"],
				total_raw_score=submission.total_score,
				total_weighted_score=total_weighted_score,
				domain_weights=_extract_domain_weights(participant_info),
				responses=_flatten_responses(submission.responses_json),
			)
		)
	return export_rows


async def fetch_manager_audit_edit_state(
	session: AsyncSession,
	audit_id: uuid.UUID,
	*,
	is_admin: bool,
	manager_account_id: uuid.UUID | None,
) -> ManagerAuditEditState:
	stmt = (
		select(Audit, Project, Place, Auditor, YeeAuditSubmission)
		.join(Project, Audit.project_id == Project.id)
		.join(Place, Audit.place_id == Place.id)
		.join(Auditor, Audit.auditor_profile_id == Auditor.id)
		.outerjoin(
			YeeAuditSubmission,
			and_(
				YeeAuditSubmission.auditor_id == Audit.auditor_profile_id,
				YeeAuditSubmission.place_id == Audit.place_id,
			),
		)
		.where(Audit.id == audit_id)
	)
	row = (await session.execute(stmt)).first()
	if row is None:
		raise HTTPException(status_code=404, detail="Audit not found.")

	audit, project, place, auditor, submission = row
	if not is_admin and project.account_id != manager_account_id:
		raise HTTPException(status_code=403, detail="You do not have access to this audit.")

	if submission is None and audit.status == AuditStatus.SUBMITTED:
		submission = await _repair_missing_yee_submission(session, audit=audit, place=place, auditor=auditor)
		if submission is not None:
			await session.commit()

	if submission is not None:
		score = score_yee_responses(submission.responses_json)
		return ManagerAuditEditState(
			audit_id=str(audit.id),
			submission_id=str(submission.id),
			place_id=str(place.id),
			place_name=place.name,
			auditor_id=str(auditor.id),
			auditor_generated_id=_display_auditor_code(auditor.auditor_code),
			submitted_at=submission.submitted_at.isoformat(),
			participant_info=submission.participant_info_json,
			responses=submission.responses_json,
			score=_dashboard_score_result(score),
		)

	participant_info, responses = _decode_audit_participant_payload(audit)
	score = (
		score_yee_responses(responses)
		if responses
		else {
			"total_score": _extract_score(audit.scores_json),
			"section_scores": {},
			"category_scores": {},
			"matched_scored_answers": 0,
		}
	)
	if not participant_info:
		participant_info = {
			"auditor_id": _display_auditor_code(auditor.auditor_code),
			"place_id": str(place.id),
			"place_name": place.name,
			"audit_date": audit.submitted_at.date().isoformat() if audit.submitted_at else None,
			"start_time": "",
			"finish_time": "",
			"total_minutes": audit.total_minutes or 0,
			"visit_frequency": "",
			"season": "",
			"weather": "",
			"domain_weights": {},
			"comments": "",
			"section_comments": {},
		}
	return ManagerAuditEditState(
		audit_id=str(audit.id),
		submission_id=None,
		place_id=str(place.id),
		place_name=place.name,
		auditor_id=str(auditor.id),
		auditor_generated_id=_display_auditor_code(auditor.auditor_code),
		submitted_at=audit.submitted_at.isoformat() if audit.submitted_at is not None else None,
		participant_info=participant_info,
		responses=responses,
		score=_dashboard_score_result(score),
	)


async def update_manager_audit_edit_state(
	session: AsyncSession,
	audit_id: uuid.UUID,
	payload: ManagerAuditEditRequest,
	*,
	is_admin: bool,
	manager_account_id: uuid.UUID | None,
) -> ManagerAuditEditState:
	stmt = (
		select(Audit, Project, Place, Auditor, YeeAuditSubmission)
		.join(Project, Audit.project_id == Project.id)
		.join(Place, Audit.place_id == Place.id)
		.join(Auditor, Audit.auditor_profile_id == Auditor.id)
		.outerjoin(
			YeeAuditSubmission,
			and_(
				YeeAuditSubmission.auditor_id == Audit.auditor_profile_id,
				YeeAuditSubmission.place_id == Audit.place_id,
			),
		)
		.where(Audit.id == audit_id)
	)
	row = (await session.execute(stmt)).first()
	if row is None:
		raise HTTPException(status_code=404, detail="Audit not found.")

	audit, project, place, auditor, submission = row
	if not is_admin and project.account_id != manager_account_id:
		raise HTTPException(status_code=403, detail="You do not have access to this audit.")

	if payload.submission_id:
		target_submission = await session.get(YeeAuditSubmission, uuid.UUID(payload.submission_id))
		if target_submission is None:
			raise HTTPException(status_code=404, detail="YEE submission not found for this audit.")
		if target_submission.place_id != audit.place_id or target_submission.auditor_id != audit.auditor_profile_id:
			raise HTTPException(status_code=400, detail="Submission does not belong to the selected audit.")
		submission = target_submission

	score = score_yee_responses(payload.responses)
	submitted_at = (
		datetime.now(timezone.utc)
		if payload.resubmit
		else submission.submitted_at
		if submission is not None
		else audit.submitted_at or datetime.now(timezone.utc)
	)

	audit.status = AuditStatus.SUBMITTED
	audit.submitted_at = submitted_at
	audit.total_minutes = int(payload.participant_info.get("total_minutes") or 0) if payload.participant_info else None
	audit.responses_json = payload.responses
	audit.summary_score = float(cast(int, score["total_score"]))
	audit.scores_json = {
		"total_score": score["total_score"],
		"section_scores": score["section_scores"],
		"category_scores": score["category_scores"],
		"matched_scored_answers": score["matched_scored_answers"],
	}

	if submission is None:
		submission = YeeAuditSubmission(
			auditor_id=audit.auditor_profile_id,
			place_id=audit.place_id,
			submitted_at=submitted_at,
			participant_info_json=payload.participant_info,
			responses_json=payload.responses,
			section_scores_json=score["section_scores"],
			total_score=score["total_score"],
		)
		session.add(submission)
	else:
		submission.submitted_at = submitted_at
		submission.participant_info_json = payload.participant_info
		submission.responses_json = payload.responses
		submission.section_scores_json = score["section_scores"]
		submission.total_score = score["total_score"]

	await session.commit()
	await session.refresh(audit)
	await session.refresh(submission)

	return ManagerAuditEditState(
		audit_id=str(audit.id),
		submission_id=str(submission.id),
		place_id=str(place.id),
		place_name=place.name,
		auditor_id=str(auditor.id),
		auditor_generated_id=_display_auditor_code(auditor.auditor_code),
		submitted_at=submission.submitted_at.isoformat(),
		participant_info=submission.participant_info_json,
		responses=submission.responses_json,
		score=_dashboard_score_result(score),
	)

"""YEE audit lifecycle service.

Assignment checks, draft lookup/save, final submit with idempotent replay, and
audit-state response assembly. Pure helpers and data-access functions backing
the YEE audit routes in `app/products/yee/routes/audits.py`.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

from fastapi import HTTPException, Response, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
	AccountType,
	Assignment,
	Audit,
	AuditStatus,
	Auditor,
	Place,
	ProjectPlace,
	User,
	YeeAuditSubmission,
)
from app.products.yee.schemas.audits import (
	CanonicalScoreSnapshot,
	ScoreResult,
	YeeAuditStateResponse,
	YeeAuditSubmissionResponse,
)
from app.products.yee.services.scoring import score_yee_responses
from app.products.yee.services.scoring_types import LegacyScoreResult


def _public_auditor_id(code: str) -> str:
	normalized = code.strip().upper()
	if normalized.startswith(("AUD", "ADT", "A")) and re.search(r"\d+$", normalized):
		match = re.search(r"(\d+)$", normalized)
		if match:
			return f"AUD{int(match.group(1)):03d}"
		return normalized
	match = re.search(r"(\d+)$", normalized)
	if match:
		return f"AUD{int(match.group(1)):03d}"
	return normalized


def _score_result_from_dict(score: LegacyScoreResult) -> ScoreResult:
	return ScoreResult(
		total_score=int(score.get("total_score", 0)),
		section_scores={str(key): int(value) for key, value in dict(score.get("section_scores", {})).items()},
		category_scores={str(key): int(value) for key, value in dict(score.get("category_scores", {})).items()},
		matched_scored_answers=int(score.get("matched_scored_answers", 0)),
		canonical_score=CanonicalScoreSnapshot.model_validate(score["canonical_score"]),
	)


def _submission_response(
	submission: YeeAuditSubmission,
	*,
	place: Place,
	auditor: Auditor,
) -> YeeAuditSubmissionResponse:
	"""Assemble the submission response, recomputing the full scoring shape."""

	score = score_yee_responses(submission.responses_json, submission.participant_info_json)
	return YeeAuditSubmissionResponse(
		id=submission.id,
		place_id=submission.place_id,
		place_name=place.name,
		auditor_id=submission.auditor_id,
		auditor_generated_id=_public_auditor_id(auditor.auditor_code),
		submitted_at=submission.submitted_at,
		participant_info=submission.participant_info_json,
		responses=submission.responses_json,
		score=_score_result_from_dict(score),
	)


def _resolve_existing_submission(
	existing: YeeAuditSubmission,
	*,
	idempotency_key: str | None,
	place: Place,
	auditor: Auditor,
	response: Response,
) -> YeeAuditSubmissionResponse:
	"""Return the stored submission on a matching-key replay, else conflict.

	A queued offline submit that resends the same idempotency key is treated as
	a safe replay and returns the existing record with a 200. Any other repeat
	(different key, or no key) keeps the protective 409.
	"""

	if idempotency_key and existing.submit_idempotency_key and idempotency_key == existing.submit_idempotency_key:
		response.status_code = status.HTTP_200_OK
		return _submission_response(existing, place=place, auditor=auditor)
	raise HTTPException(status_code=409, detail="You have already submitted an audit for this place.")


def _build_empty_score() -> ScoreResult:
	return _score_result_from_dict(score_yee_responses({}))


async def _get_current_auditor(session: AsyncSession, user: User) -> Auditor:
	auditor_result = await session.execute(select(Auditor).where(Auditor.user_id == user.id))
	auditor = auditor_result.scalar_one_or_none()
	if auditor is None:
		raise HTTPException(status_code=404, detail="Auditor profile not found.")
	return auditor


async def _get_current_yee_auditor_actor(session: AsyncSession, user: User) -> Auditor:
	"""Allow standard auditors and manager-users with a self auditor profile."""

	if user.account_type not in {AccountType.AUDITOR, AccountType.MANAGER}:
		raise HTTPException(status_code=403, detail="Auditor access is required.")
	auditor = await _get_current_auditor(session, user)
	if (
		user.account_type == AccountType.MANAGER
		and user.account_id is not None
		and auditor.account_id != user.account_id
	):
		raise HTTPException(status_code=403, detail="Your auditor profile is outside your manager organization.")
	return auditor


async def _get_assigned_place(
	session: AsyncSession,
	*,
	auditor: Auditor,
	place_id: uuid.UUID,
) -> tuple[Assignment, Place]:
	stmt = (
		select(Assignment, Place)
		.join(ProjectPlace, ProjectPlace.project_id == Assignment.project_id)
		.join(
			Place,
			and_(
				Place.id == ProjectPlace.place_id,
				or_(Assignment.place_id.is_(None), Assignment.place_id == ProjectPlace.place_id),
			),
		)
		.where(
			Assignment.auditor_profile_id == auditor.id,
			Place.id == place_id,
		)
		.order_by(Assignment.place_id.is_(None).asc())
	)
	row = (await session.execute(stmt)).one_or_none()
	if row is None:
		raise HTTPException(status_code=403, detail="This place is not assigned to you.")
	return row._tuple()


def _decode_draft_payload(audit: Audit) -> tuple[dict[str, Any], dict[str, Any]]:
	raw_payload = audit.responses_json if isinstance(audit.responses_json, dict) else {}
	participant_info = raw_payload.get("participant_info")
	responses = raw_payload.get("responses")
	if isinstance(participant_info, dict) and isinstance(responses, dict):
		return participant_info, responses
	if isinstance(raw_payload, dict):
		return {}, raw_payload
	return {}, {}


def _encode_draft_payload(participant_info: dict[str, Any], responses: dict[str, Any]) -> dict[str, Any]:
	return {
		"participant_info": participant_info,
		"responses": responses,
	}


def _build_state_response(
	*,
	place: Place,
	auditor: Auditor,
	status_value: str,
	audit_id: uuid.UUID | None = None,
	submission_id: uuid.UUID | None = None,
	submitted_at: datetime | None = None,
	participant_info: dict[str, Any] | None = None,
	responses: dict[str, Any] | None = None,
	score: ScoreResult | None = None,
) -> YeeAuditStateResponse:
	return YeeAuditStateResponse(
		audit_id=audit_id,
		submission_id=submission_id,
		place_id=place.id,
		place_name=place.name,
		auditor_generated_id=_public_auditor_id(auditor.auditor_code),
		status=status_value,
		submitted_at=submitted_at,
		participant_info=participant_info or {},
		responses=responses or {},
		score=score,
	)


async def _get_draft_audit(
	session: AsyncSession,
	*,
	auditor: Auditor,
	place_id: uuid.UUID,
) -> Audit | None:
	stmt = (
		select(Audit)
		.where(
			Audit.auditor_profile_id == auditor.id,
			Audit.place_id == place_id,
			or_(Audit.instrument_key == "yee", Audit.instrument_key.like("yee%"), Audit.instrument_key.is_(None)),
			Audit.status.in_([AuditStatus.IN_PROGRESS, AuditStatus.PAUSED]),
		)
		.order_by(Audit.updated_at.desc())
	)
	return (await session.execute(stmt)).scalars().first()


async def _get_latest_yee_audit(
	session: AsyncSession,
	*,
	auditor: Auditor,
	place_id: uuid.UUID,
) -> Audit | None:
	stmt = (
		select(Audit)
		.where(
			Audit.auditor_profile_id == auditor.id,
			Audit.place_id == place_id,
			or_(Audit.instrument_key == "yee", Audit.instrument_key.like("yee%"), Audit.instrument_key.is_(None)),
		)
		.order_by(Audit.updated_at.desc(), Audit.created_at.desc())
	)
	return (await session.execute(stmt)).scalars().first()

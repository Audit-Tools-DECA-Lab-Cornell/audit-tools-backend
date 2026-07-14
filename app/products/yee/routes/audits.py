"""YEE auditor-facing audit lifecycle routes.

Endpoints for instrument-backed audit state, drafts, score preview, final
submit, and submission list/detail. Thin HTTP layer over
`app.products.yee.services.audits`.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_auth_session, get_current_user
from app.models import (
	AccountType,
	Audit,
	AuditStatus,
	Auditor,
	Place,
	User,
	YeeAuditSubmission,
)
from app.products.yee.schemas.audits import (
	MyYeeAuditItem,
	SaveYeeDraftRequest,
	ScoreResult,
	SubmitYeeAuditRequest,
	YeeAuditStateResponse,
	YeeAuditSubmissionResponse,
)
from app.products.yee.services.audits import (
	_build_empty_score,
	_build_state_response,
	_decode_draft_payload,
	_encode_draft_payload,
	_get_assigned_place,
	_get_current_yee_auditor_actor,
	_get_draft_audit,
	_get_latest_yee_audit,
	_manager_can_view_submission,
	_public_auditor_id,
	_resolve_active_instrument_stamp,
	_resolve_existing_submission,
	_score_result_from_dict,
	_submission_response,
)
from app.products.yee.services.dashboard import participant_id_from_info
from app.products.yee.services.scoring_spec import SCORING_VERSION
from app.products.yee.services.scoring import score_yee_responses

router = APIRouter()


@router.get("/my-audits", response_model=list[MyYeeAuditItem])
async def list_my_yee_audits(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[MyYeeAuditItem]:
	"""Return submitted YEE audits for the authenticated auditor."""

	auditor = await _get_current_yee_auditor_actor(session, user)

	stmt = (
		select(YeeAuditSubmission, Place.name)
		.join(Place, YeeAuditSubmission.place_id == Place.id)
		.where(YeeAuditSubmission.auditor_id == auditor.id)
		.order_by(YeeAuditSubmission.submitted_at.desc())
	)
	rows = (await session.execute(stmt)).all()
	return [
		MyYeeAuditItem(
			id=submission.id,
			place_id=submission.place_id,
			place_name=place_name,
			submitted_at=submission.submitted_at,
			total_score=score_yee_responses(submission.responses_json, submission.participant_info_json)["total_score"],
			participant_id=participant_id_from_info(submission.participant_info_json),
		)
		for submission, place_name in rows
	]


@router.get("/places/{place_id}/audit-state", response_model=YeeAuditStateResponse)
async def get_yee_audit_state(
	place_id: uuid.UUID,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> YeeAuditStateResponse:
	"""Return the current YEE draft/submission state for one auditor-place pair."""

	auditor = await _get_current_yee_auditor_actor(session, user)
	_, place = await _get_assigned_place(session, auditor=auditor, place_id=place_id)

	submission_stmt = (
		select(YeeAuditSubmission)
		.where(
			YeeAuditSubmission.auditor_id == auditor.id,
			YeeAuditSubmission.place_id == place_id,
		)
		.order_by(YeeAuditSubmission.submitted_at.desc())
	)
	submission = (await session.execute(submission_stmt)).scalars().first()
	if submission is not None:
		score = score_yee_responses(submission.responses_json, submission.participant_info_json)
		return _build_state_response(
			place=place,
			auditor=auditor,
			status_value="SUBMITTED",
			submission_id=submission.id,
			submitted_at=submission.submitted_at,
			participant_info=submission.participant_info_json,
			responses=submission.responses_json,
			score=_score_result_from_dict(score),
		)

	draft_audit = await _get_draft_audit(session, auditor=auditor, place_id=place_id)
	if draft_audit is not None:
		participant_info, responses = _decode_draft_payload(draft_audit)
		score = score_yee_responses(responses, participant_info)
		return _build_state_response(
			place=place,
			auditor=auditor,
			status_value="DRAFT",
			audit_id=draft_audit.id,
			participant_info=participant_info,
			responses=responses,
			score=_score_result_from_dict(score),
		)

	return _build_state_response(
		place=place,
		auditor=auditor,
		status_value="NOT_STARTED",
		score=_build_empty_score(),
	)


@router.put("/places/{place_id}/draft", response_model=YeeAuditStateResponse)
async def save_yee_draft(
	place_id: uuid.UUID,
	payload: SaveYeeDraftRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> YeeAuditStateResponse:
	"""Persist or update one backend-backed YEE draft for the current auditor/place."""

	auditor = await _get_current_yee_auditor_actor(session, user)
	assignment, place = await _get_assigned_place(session, auditor=auditor, place_id=place_id)

	existing_submission_stmt = select(YeeAuditSubmission).where(
		YeeAuditSubmission.auditor_id == auditor.id,
		YeeAuditSubmission.place_id == place_id,
	)
	existing_submission = (await session.execute(existing_submission_stmt)).scalar_one_or_none()
	if existing_submission is not None:
		raise HTTPException(status_code=409, detail="This audit has already been submitted and is locked.")

	existing_audit = await _get_latest_yee_audit(session, auditor=auditor, place_id=place_id)
	if existing_audit is not None and existing_audit.status == AuditStatus.SUBMITTED:
		raise HTTPException(status_code=409, detail="This audit has already been submitted and is locked.")

	score = score_yee_responses(payload.responses, payload.participant_info)
	if existing_audit is None:
		instrument_key, instrument_version = await _resolve_active_instrument_stamp(session)
		existing_audit = Audit(
			project_id=assignment.project_id,
			place_id=place_id,
			auditor_profile_id=auditor.id,
			audit_code=f"YEE-{uuid.uuid4().hex[:8].upper()}",
			instrument_key=instrument_key,
			instrument_version=instrument_version,
			status=AuditStatus.IN_PROGRESS,
		)
		session.add(existing_audit)

	existing_audit.status = AuditStatus.IN_PROGRESS
	existing_audit.total_minutes = (
		int(payload.participant_info.get("total_minutes") or 0) if payload.participant_info else None
	)
	existing_audit.summary_score = float(int(score["total_score"]))
	existing_audit.responses_json = _encode_draft_payload(payload.participant_info, payload.responses)
	existing_audit.scores_json = {
		"total_score": score["total_score"],
		"section_scores": score["section_scores"],
		"category_scores": score["category_scores"],
		"matched_scored_answers": score["matched_scored_answers"],
		"canonical_score": score["canonical_score"],
	}

	await session.commit()
	await session.refresh(existing_audit)

	return _build_state_response(
		place=place,
		auditor=auditor,
		status_value="DRAFT",
		audit_id=existing_audit.id,
		participant_info=payload.participant_info,
		responses=payload.responses,
		score=_score_result_from_dict(score),
	)


@router.post("/audits/score", response_model=ScoreResult)
def preview_yee_score(payload: SubmitYeeAuditRequest) -> ScoreResult:
	"""Compute scores without persisting an audit submission."""

	score = score_yee_responses(payload.responses, payload.participant_info)
	return _score_result_from_dict(score)


@router.post(
	"/audits",
	response_model=YeeAuditSubmissionResponse,
	status_code=status.HTTP_201_CREATED,
)
async def submit_yee_audit(
	payload: SubmitYeeAuditRequest,
	response: Response,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> YeeAuditSubmissionResponse:
	"""Compute and persist an authenticated YEE audit submission.

	The final submit is the only durability boundary for YEE: drafts live on the
	device. A matching ``idempotency_key`` replay returns the stored submission
	(200) instead of a 409, and a unique ``(auditor, place)`` constraint plus an
	``IntegrityError`` fallback make a concurrent double-submit safe.
	"""

	auditor = await _get_current_yee_auditor_actor(session, user)
	assignment, place = await _get_assigned_place(session, auditor=auditor, place_id=payload.place_id)

	existing_submission_stmt = select(YeeAuditSubmission).where(
		YeeAuditSubmission.auditor_id == auditor.id,
		YeeAuditSubmission.place_id == payload.place_id,
	)
	existing_submission = (await session.execute(existing_submission_stmt)).scalar_one_or_none()
	if existing_submission is not None:
		return _resolve_existing_submission(
			existing_submission,
			idempotency_key=payload.idempotency_key,
			place=place,
			auditor=auditor,
			response=response,
		)

	score = score_yee_responses(payload.responses, payload.participant_info)
	active_instrument_key, active_instrument_version = await _resolve_active_instrument_stamp(session)
	audit = await _get_latest_yee_audit(session, auditor=auditor, place_id=payload.place_id)
	if audit is None:
		audit = Audit(
			project_id=assignment.project_id,
			place_id=payload.place_id,
			auditor_profile_id=auditor.id,
			audit_code=f"YEE-{uuid.uuid4().hex[:8].upper()}",
			instrument_key=active_instrument_key,
			instrument_version=active_instrument_version,
			status=AuditStatus.SUBMITTED,
		)
		session.add(audit)

	submitted_at = datetime.now(timezone.utc)
	audit.project_id = assignment.project_id
	audit.status = AuditStatus.SUBMITTED
	audit.submitted_at = submitted_at
	audit.total_minutes = int(payload.participant_info.get("total_minutes") or 0) if payload.participant_info else None
	audit.responses_json = payload.responses
	audit.scores_json = {
		"total_score": score["total_score"],
		"section_scores": score["section_scores"],
		"category_scores": score["category_scores"],
		"matched_scored_answers": score["matched_scored_answers"],
		"canonical_score": score["canonical_score"],
	}
	audit.summary_score = float(int(score["total_score"]))

	submission = YeeAuditSubmission(
		auditor_id=auditor.id,
		place_id=payload.place_id,
		participant_info_json=payload.participant_info,
		responses_json=payload.responses,
		section_scores_json=score["section_scores"],
		scores_json=score["canonical_score"],
		scoring_version=SCORING_VERSION,
		total_score=score["total_score"],
		submit_idempotency_key=payload.idempotency_key,
		instrument_key=audit.instrument_key or active_instrument_key,
		instrument_version=audit.instrument_version or active_instrument_version,
	)
	session.add(submission)
	try:
		await session.commit()
	except IntegrityError:
		# A concurrent submit won the unique (auditor, place) constraint. Reload
		# the winning row and apply the same idempotent-replay vs conflict rule.
		await session.rollback()
		existing_submission = (await session.execute(existing_submission_stmt)).scalar_one_or_none()
		if existing_submission is None:
			raise
		return _resolve_existing_submission(
			existing_submission,
			idempotency_key=payload.idempotency_key,
			place=place,
			auditor=auditor,
			response=response,
		)
	await session.refresh(submission)

	return _submission_response(submission, place=place, auditor=auditor)


@router.get("/audits/{submission_id}", response_model=YeeAuditSubmissionResponse)
async def get_yee_submission(
	submission_id: uuid.UUID,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> YeeAuditSubmissionResponse:
	"""Fetch a previously submitted YEE audit and return stored score."""

	stmt = select(YeeAuditSubmission).where(YeeAuditSubmission.id == submission_id)
	result = await session.execute(stmt)
	submission = result.scalar_one_or_none()
	if submission is None:
		raise HTTPException(status_code=404, detail="YEE submission not found.")

	auditor: Auditor | None
	if user.account_type == AccountType.AUDITOR:
		auditor = await _get_current_yee_auditor_actor(session, user)
		if submission.auditor_id != auditor.id:
			raise HTTPException(status_code=403, detail="You do not have access to this submission.")
	else:
		if user.account_type == AccountType.MANAGER:
			# Managers read any submission whose place sits in a project their
			# account owns — the same scope the dashboard reports expose. A
			# manager's own submissions (self auditor profile) live in their
			# org's projects, so this path covers them too. By product invariant
			# a place/project belongs to exactly one account (no shared places or
			# cross-account sharing — see SCHEMA.md section 1), so this never
			# grants access to another account's submission.
			if user.account_id is None or not await _manager_can_view_submission(
				session,
				account_id=user.account_id,
				place_id=submission.place_id,
			):
				raise HTTPException(status_code=403, detail="You do not have access to this submission.")
		auditor = (
			await session.execute(select(Auditor).where(Auditor.id == submission.auditor_id))
		).scalar_one_or_none()

	place = (await session.execute(select(Place).where(Place.id == submission.place_id))).scalar_one_or_none()

	# Recompute score from stored responses so this endpoint always returns
	# the same full scoring shape as /yee/audits and /yee/audits/score.
	score = score_yee_responses(submission.responses_json, submission.participant_info_json)
	return YeeAuditSubmissionResponse(
		id=submission.id,
		place_id=submission.place_id,
		place_name=place.name if place is not None else None,
		auditor_id=submission.auditor_id,
		auditor_generated_id=_public_auditor_id(auditor.auditor_code) if auditor is not None else None,
		submitted_at=submission.submitted_at,
		participant_info=submission.participant_info_json,
		responses=submission.responses_json,
		score=_score_result_from_dict(score),
	)

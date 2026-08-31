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
	_resolve_existing_submission,
	_score_result_from_dict,
	_submission_response,
)
from app.products.yee.services.dashboard import participant_id_from_info
from app.products.yee.services.runtime_scoring import InstrumentStamp, RuntimeScorer
from app.products.yee.services.submission_validation import (
	find_incomplete_responses,
	submit_completeness_enforced,
)
from app.products.yee.services.score_snapshots import (
	audit_score_cache,
	resolved_audit_score,
	resolved_submission_score,
)

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
			total_score=submission.total_score,
			participant_id=participant_id_from_info(submission.participant_info_json),
			instrument_key=submission.instrument_key,
			instrument_version=submission.instrument_version,
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
	scorer = RuntimeScorer(session)

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
		score = await resolved_submission_score(scorer, submission)
		return _build_state_response(
			place=place,
			auditor=auditor,
			status_value="SUBMITTED",
			submission_id=submission.id,
			submitted_at=submission.submitted_at,
			participant_info=submission.participant_info_json,
			responses=submission.responses_json,
			score=_score_result_from_dict(score),
			instrument_key=submission.instrument_key,
			instrument_version=submission.instrument_version,
		)

	draft_audit = await _get_draft_audit(session, auditor=auditor, place_id=place_id)
	if draft_audit is not None:
		participant_info, responses = _decode_draft_payload(draft_audit)
		score = await resolved_audit_score(
			scorer,
			draft_audit,
			participant_info=participant_info,
			responses=responses,
		)
		return _build_state_response(
			place=place,
			auditor=auditor,
			status_value="DRAFT",
			audit_id=draft_audit.id,
			participant_info=participant_info,
			responses=responses,
			score=_score_result_from_dict(score),
			instrument_key=draft_audit.instrument_key,
			instrument_version=draft_audit.instrument_version,
		)

	active_stamp, _contract = await scorer.active_stamp_and_contract()
	return _build_state_response(
		place=place,
		auditor=auditor,
		status_value="NOT_STARTED",
		score=await _build_empty_score(scorer),
		instrument_key=active_stamp.instrument_key,
		instrument_version=active_stamp.instrument_version,
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

	scorer = RuntimeScorer(session)
	if existing_audit is None:
		requested_stamp = InstrumentStamp(payload.instrument_key, payload.instrument_version)
		if requested_stamp.is_unstamped:
			stamp, _contract = await scorer.active_stamp_and_contract()
		else:
			await scorer.contract_for_stamp(requested_stamp)
			stamp = requested_stamp
		existing_audit = Audit(
			project_id=assignment.project_id,
			place_id=place_id,
			auditor_profile_id=auditor.id,
			audit_code=f"YEE-{uuid.uuid4().hex[:8].upper()}",
			instrument_key=stamp.instrument_key,
			instrument_version=stamp.instrument_version,
			status=AuditStatus.IN_PROGRESS,
		)
		session.add(existing_audit)
	else:
		stamp = InstrumentStamp(existing_audit.instrument_key, existing_audit.instrument_version)
		requested_stamp = InstrumentStamp(payload.instrument_key, payload.instrument_version)
		if not requested_stamp.is_unstamped and requested_stamp != stamp:
			raise HTTPException(
				status_code=409,
				detail={
					"code": "instrument_stamp_conflict",
					"message": "This draft belongs to a different instrument version.",
					"instrument_key": stamp.instrument_key,
					"instrument_version": stamp.instrument_version,
				},
			)
	score = await scorer.score_for_stamp(stamp, payload.responses, payload.participant_info)

	existing_audit.status = AuditStatus.IN_PROGRESS
	existing_audit.total_minutes = (
		int(payload.participant_info.get("total_minutes") or 0) if payload.participant_info else None
	)
	existing_audit.summary_score = float(int(score["total_score"]))
	existing_audit.responses_json = _encode_draft_payload(payload.participant_info, payload.responses)
	existing_audit.scores_json = audit_score_cache(score)

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
		instrument_key=existing_audit.instrument_key,
		instrument_version=existing_audit.instrument_version,
	)


@router.post("/audits/score", response_model=ScoreResult)
async def preview_yee_score(
	payload: SubmitYeeAuditRequest,
	session: AsyncSession = Depends(get_auth_session),
) -> ScoreResult:
	"""Compute scores without persisting an audit submission."""

	scorer = RuntimeScorer(session)
	stamp = InstrumentStamp(payload.instrument_key, payload.instrument_version)
	if stamp.is_unstamped:
		stamp, _contract = await scorer.active_stamp_and_contract()
	score = await scorer.score_for_stamp(stamp, payload.responses, payload.participant_info)
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
	scorer = RuntimeScorer(session)

	existing_submission_stmt = select(YeeAuditSubmission).where(
		YeeAuditSubmission.auditor_id == auditor.id,
		YeeAuditSubmission.place_id == payload.place_id,
	)
	existing_submission = (await session.execute(existing_submission_stmt)).scalar_one_or_none()
	if existing_submission is not None:
		return await _resolve_existing_submission(
			existing_submission,
			idempotency_key=payload.idempotency_key,
			place=place,
			auditor=auditor,
			response=response,
			scorer=scorer,
		)

	audit = await _get_latest_yee_audit(session, auditor=auditor, place_id=payload.place_id)
	if audit is None:
		requested_stamp = InstrumentStamp(payload.instrument_key, payload.instrument_version)
		if requested_stamp.is_unstamped:
			stamp, _contract = await scorer.active_stamp_and_contract()
		else:
			await scorer.contract_for_stamp(requested_stamp)
			stamp = requested_stamp
		audit = Audit(
			project_id=assignment.project_id,
			place_id=payload.place_id,
			auditor_profile_id=auditor.id,
			audit_code=f"YEE-{uuid.uuid4().hex[:8].upper()}",
			instrument_key=stamp.instrument_key,
			instrument_version=stamp.instrument_version,
			status=AuditStatus.SUBMITTED,
		)
		session.add(audit)
	else:
		stamp = InstrumentStamp(audit.instrument_key, audit.instrument_version)
		requested_stamp = InstrumentStamp(payload.instrument_key, payload.instrument_version)
		if not requested_stamp.is_unstamped and requested_stamp != stamp:
			raise HTTPException(
				status_code=409,
				detail={
					"code": "instrument_stamp_conflict",
					"message": "This audit belongs to a different instrument version.",
					"instrument_key": stamp.instrument_key,
					"instrument_version": stamp.instrument_version,
				},
			)
	# Completeness runs AFTER the exact stamp is resolved and BEFORE scoring or
	# persistence: an audit is judged against the contract it was taken under,
	# and a rejected submission leaves no partial row behind. The idempotent
	# replay above already returned, so a retry that owns a stored submission is
	# never re-validated under a rule that did not exist when it was accepted.
	if submit_completeness_enforced():
		instrument_content = await scorer.content_for_stamp(stamp)
		incomplete = find_incomplete_responses(instrument_content, payload.responses)
		if not incomplete.is_complete:
			raise HTTPException(status_code=422, detail=incomplete.as_error_detail())

	score = await scorer.score_for_stamp(stamp, payload.responses, payload.participant_info)

	submitted_at = datetime.now(timezone.utc)
	audit.project_id = assignment.project_id
	audit.status = AuditStatus.SUBMITTED
	audit.submitted_at = submitted_at
	audit.total_minutes = int(payload.participant_info.get("total_minutes") or 0) if payload.participant_info else None
	audit.responses_json = payload.responses
	audit.scores_json = audit_score_cache(score)
	audit.summary_score = float(int(score["total_score"]))

	submission = YeeAuditSubmission(
		auditor_id=auditor.id,
		place_id=payload.place_id,
		participant_info_json=payload.participant_info,
		responses_json=payload.responses,
		section_scores_json=score["section_scores"],
		scores_json=score["canonical_score"],
		scoring_version=score["canonical_score"]["scoring_version"],
		total_score=score["total_score"],
		submit_idempotency_key=payload.idempotency_key,
		instrument_key=audit.instrument_key,
		instrument_version=audit.instrument_version,
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
		return await _resolve_existing_submission(
			existing_submission,
			idempotency_key=payload.idempotency_key,
			place=place,
			auditor=auditor,
			response=response,
			scorer=scorer,
		)
	await session.refresh(submission)

	return await _submission_response(submission, place=place, auditor=auditor, scorer=scorer)


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

	return await _submission_response(
		submission,
		place=place,
		auditor=auditor,
		scorer=RuntimeScorer(session),
	)

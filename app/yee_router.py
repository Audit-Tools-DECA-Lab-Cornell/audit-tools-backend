"""YEE REST API endpoints."""

from __future__ import annotations

import uuid
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import and_, or_, select
from sqlalchemy import update as sqlalchemy_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_auth_session, get_current_user
from app.models import (
	AccountType,
	Assignment,
	Audit,
	AuditStatus,
	Auditor,
	Instrument,
	Place,
	ProjectPlace,
	User,
	YeeAuditSubmission,
)
from app.yee_scoring import get_yee_instrument_data, score_yee_responses
from app.yee_instrument_schema import YeeInstrumentResponse

router: APIRouter = APIRouter(prefix="/yee", tags=["yee"])


class SubmitYeeAuditRequest(BaseModel):
	"""
	YEE audit submission payload.

	`responses` format:
	- Single-choice item: {"QID22": "3"}
	- Matrix-like item: {"QID1#2": {"1": "3", "2": "2"}}
	"""

	place_id: uuid.UUID
	participant_info: dict[str, Any] = Field(default_factory=dict)
	responses: dict[str, Any] = Field(default_factory=dict)


class SaveYeeDraftRequest(BaseModel):
	participant_info: dict[str, Any] = Field(default_factory=dict)
	responses: dict[str, Any] = Field(default_factory=dict)


class ScoreResult(BaseModel):
	total_score: int
	section_scores: dict[str, int]
	category_scores: dict[str, int]
	matched_scored_answers: int


class YeeAuditSubmissionResponse(BaseModel):
	id: uuid.UUID
	place_id: uuid.UUID
	place_name: str | None = None
	auditor_id: uuid.UUID
	auditor_generated_id: str | None = None
	submitted_at: datetime
	participant_info: dict[str, Any]
	responses: dict[str, Any]
	score: ScoreResult


class YeeAuditStateResponse(BaseModel):
	audit_id: uuid.UUID | None = None
	submission_id: uuid.UUID | None = None
	place_id: uuid.UUID
	place_name: str
	auditor_generated_id: str
	status: str
	submitted_at: datetime | None = None
	participant_info: dict[str, Any] = Field(default_factory=dict)
	responses: dict[str, Any] = Field(default_factory=dict)
	score: ScoreResult | None = None


class MyYeeAuditItem(BaseModel):
	id: uuid.UUID
	place_id: uuid.UUID
	place_name: str
	submitted_at: datetime
	total_score: int


class YeeInstrumentVersionResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: uuid.UUID
	instrument_key: str
	instrument_version: str
	is_active: bool
	content: dict[str, Any]
	created_at: datetime
	updated_at: datetime


class YeeInstrumentCreateRequest(BaseModel):
	instrument_key: str = Field(default="yee")
	instrument_version: str
	content: dict[str, Any]


class YeeInstrumentActivateRequest(BaseModel):
	is_active: bool


class SiteCopyVersionResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: uuid.UUID
	instrument_key: str
	instrument_version: str
	is_active: bool
	content: dict[str, Any]
	created_at: datetime
	updated_at: datetime


class SiteCopyCreateRequest(BaseModel):
	instrument_version: str
	content: dict[str, Any]


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


def _score_result_from_dict(score: dict[str, object]) -> ScoreResult:
	return ScoreResult(
		total_score=int(score.get("total_score", 0)),
		section_scores={str(key): int(value) for key, value in dict(score.get("section_scores", {})).items()},
		category_scores={str(key): int(value) for key, value in dict(score.get("category_scores", {})).items()},
		matched_scored_answers=int(score.get("matched_scored_answers", 0)),
	)


def _require_admin(user: User) -> None:
	if user.account_type != AccountType.ADMIN:
		raise HTTPException(status_code=403, detail="Admin access is required.")


def _build_empty_score() -> ScoreResult:
	return ScoreResult(
		total_score=0,
		section_scores={},
		category_scores={},
		matched_scored_answers=0,
	)


async def _get_current_auditor(session: AsyncSession, user: User) -> Auditor:
	auditor_result = await session.execute(select(Auditor).where(Auditor.user_id == user.id))
	auditor = auditor_result.scalar_one_or_none()
	if auditor is None:
		raise HTTPException(status_code=404, detail="Auditor profile not found.")
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
	return row


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


async def _get_active_yee_instrument(session: AsyncSession, instrument_key: str = "yee") -> Instrument | None:
	stmt = (
		select(Instrument)
		.where(Instrument.instrument_key == instrument_key)
		.where(Instrument.is_active.is_(True))
		.order_by(Instrument.created_at.desc())
		.limit(1)
	)
	return (await session.execute(stmt)).scalar_one_or_none()


async def _bootstrap_yee_instrument_if_missing(session: AsyncSession, instrument_key: str = "yee") -> Instrument | None:
	existing = await _get_active_yee_instrument(session, instrument_key)
	if existing is not None:
		return existing

	any_existing = (
		await session.execute(
			select(Instrument)
			.where(Instrument.instrument_key == instrument_key)
			.order_by(Instrument.created_at.desc())
			.limit(1)
		)
	).scalar_one_or_none()
	if any_existing is not None:
		return any_existing

	canonical = YeeInstrumentResponse.model_validate(get_yee_instrument_data()).model_dump()
	row = Instrument(
		instrument_key=instrument_key,
		instrument_version=str(canonical.get("version", "1")),
		is_active=True,
		content=canonical,
	)
	session.add(row)
	await session.commit()
	await session.refresh(row)
	return row


async def _list_yee_instrument_versions(session: AsyncSession, instrument_key: str = "yee") -> list[Instrument]:
	stmt = select(Instrument).where(Instrument.instrument_key == instrument_key).order_by(Instrument.created_at.desc())
	return list((await session.execute(stmt)).scalars().all())


async def _get_yee_instrument_by_id(session: AsyncSession, instrument_id: uuid.UUID) -> Instrument | None:
	return (await session.execute(select(Instrument).where(Instrument.id == instrument_id))).scalar_one_or_none()


def _normalize_yee_instrument_content(raw_content: Any) -> dict[str, Any]:
	canonical = YeeInstrumentResponse.model_validate(get_yee_instrument_data()).model_dump()
	if not isinstance(raw_content, dict):
		return canonical
	merged = {**canonical, **raw_content}
	return YeeInstrumentResponse.model_validate(merged).model_dump()


async def _create_yee_instrument_version(
	session: AsyncSession,
	data: YeeInstrumentCreateRequest,
	activate: bool = True,
) -> Instrument:
	if activate:
		await session.execute(
			sqlalchemy_update(Instrument)
			.where(Instrument.instrument_key == data.instrument_key)
			.values(is_active=False, updated_at=datetime.now(timezone.utc))
		)

	new_instrument = Instrument(
		instrument_key=data.instrument_key,
		instrument_version=data.instrument_version,
		is_active=activate,
		content=data.content,
	)
	session.add(new_instrument)
	await session.commit()
	await session.refresh(new_instrument)
	return new_instrument


async def _update_yee_instrument_status(
	session: AsyncSession,
	instrument_id: uuid.UUID,
	data: YeeInstrumentActivateRequest,
) -> Instrument | None:
	instrument = await _get_yee_instrument_by_id(session, instrument_id)
	if instrument is None:
		return None

	if data.is_active:
		await session.execute(
			sqlalchemy_update(Instrument)
			.where(Instrument.instrument_key == instrument.instrument_key)
			.values(is_active=False, updated_at=datetime.now(timezone.utc))
		)

	instrument.is_active = data.is_active
	instrument.updated_at = datetime.now(timezone.utc)
	await session.commit()
	await session.refresh(instrument)
	return instrument


async def _delete_yee_instrument_version(session: AsyncSession, instrument_id: uuid.UUID) -> Instrument | None:
	instrument = await _get_yee_instrument_by_id(session, instrument_id)
	if instrument is None:
		return None
	if instrument.is_active:
		raise HTTPException(status_code=400, detail="The active instrument version cannot be deleted.")
	await session.delete(instrument)
	await session.commit()
	return instrument


@router.get("/instrument")
async def get_yee_instrument(
	session: AsyncSession = Depends(get_auth_session),
) -> dict[str, object]:
	"""Return YEE instrument metadata and scoring matrix extracted from QSF."""

	active = await _get_active_yee_instrument(session)
	if active is None:
		active = await _bootstrap_yee_instrument_if_missing(session)
	if active is not None:
		try:
			return _normalize_yee_instrument_content(active.content)
		except Exception:
			pass
	return YeeInstrumentResponse.model_validate(get_yee_instrument_data()).model_dump()


@router.get("/admin/instruments", response_model=list[YeeInstrumentVersionResponse])
async def admin_list_yee_instruments(
	instrument_key: str = "yee",
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[YeeInstrumentVersionResponse]:
	_require_admin(user)
	await _bootstrap_yee_instrument_if_missing(session, instrument_key)
	rows = await _list_yee_instrument_versions(session, instrument_key)
	return [
		YeeInstrumentVersionResponse.model_validate(
			{
				"id": row.id,
				"instrument_key": row.instrument_key,
				"instrument_version": row.instrument_version,
				"is_active": row.is_active,
				"content": _normalize_yee_instrument_content(row.content) if instrument_key == "yee" else row.content,
				"created_at": row.created_at,
				"updated_at": row.updated_at,
			}
		)
		for row in rows
	]


@router.get("/site-copy")
async def get_site_copy(
	session: AsyncSession = Depends(get_auth_session),
) -> dict[str, Any]:
	active = await _get_active_yee_instrument(session, "yee_site_copy")
	if active is None or not isinstance(active.content, dict):
		return {}
	return active.content


@router.get("/admin/site-copy", response_model=list[SiteCopyVersionResponse])
async def admin_list_site_copy_versions(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[SiteCopyVersionResponse]:
	_require_admin(user)
	rows = await _list_yee_instrument_versions(session, "yee_site_copy")
	return [SiteCopyVersionResponse.model_validate(row) for row in rows]


@router.post("/admin/site-copy", response_model=SiteCopyVersionResponse, status_code=status.HTTP_201_CREATED)
async def admin_create_site_copy_version(
	data: SiteCopyCreateRequest,
	activate: bool = True,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> SiteCopyVersionResponse:
	_require_admin(user)
	row = await _create_yee_instrument_version(
		session,
		YeeInstrumentCreateRequest(
			instrument_key="yee_site_copy",
			instrument_version=data.instrument_version,
			content=data.content,
		),
		activate,
	)
	return SiteCopyVersionResponse.model_validate(row)


@router.patch("/admin/site-copy/{copy_id}", response_model=SiteCopyVersionResponse)
async def admin_update_site_copy_version(
	copy_id: uuid.UUID,
	data: YeeInstrumentActivateRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> SiteCopyVersionResponse:
	_require_admin(user)
	row = await _update_yee_instrument_status(session, copy_id, data)
	if row is None:
		raise HTTPException(status_code=404, detail="Site copy version not found")
	return SiteCopyVersionResponse.model_validate(row)


@router.post("/admin/instruments", response_model=YeeInstrumentVersionResponse, status_code=status.HTTP_201_CREATED)
async def admin_create_yee_instrument(
	data: YeeInstrumentCreateRequest,
	activate: bool = True,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> YeeInstrumentVersionResponse:
	_require_admin(user)
	validated_content = YeeInstrumentResponse.model_validate(data.content).model_dump()
	validated_data = YeeInstrumentCreateRequest(
		instrument_key=data.instrument_key,
		instrument_version=data.instrument_version,
		content=validated_content,
	)
	row = await _create_yee_instrument_version(session, validated_data, activate)
	return YeeInstrumentVersionResponse.model_validate(
		{
			"id": row.id,
			"instrument_key": row.instrument_key,
			"instrument_version": row.instrument_version,
			"is_active": row.is_active,
			"content": _normalize_yee_instrument_content(row.content),
			"created_at": row.created_at,
			"updated_at": row.updated_at,
		}
	)


@router.patch("/admin/instruments/{instrument_id}", response_model=YeeInstrumentVersionResponse)
async def admin_update_yee_instrument(
	instrument_id: uuid.UUID,
	data: YeeInstrumentActivateRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> YeeInstrumentVersionResponse:
	_require_admin(user)
	row = await _update_yee_instrument_status(session, instrument_id, data)
	if row is None:
		raise HTTPException(status_code=404, detail="Instrument not found")
	return YeeInstrumentVersionResponse.model_validate(
		{
			"id": row.id,
			"instrument_key": row.instrument_key,
			"instrument_version": row.instrument_version,
			"is_active": row.is_active,
			"content": _normalize_yee_instrument_content(row.content),
			"created_at": row.created_at,
			"updated_at": row.updated_at,
		}
	)


@router.delete("/admin/instruments/{instrument_id}")
async def admin_delete_yee_instrument(
	instrument_id: uuid.UUID,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> dict[str, Any]:
	_require_admin(user)
	row = await _delete_yee_instrument_version(session, instrument_id)
	if row is None:
		raise HTTPException(status_code=404, detail="Instrument not found")
	return {"deleted": True, "instrument_id": str(instrument_id)}


@router.get("/my-audits", response_model=list[MyYeeAuditItem])
async def list_my_yee_audits(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[MyYeeAuditItem]:
	"""Return submitted YEE audits for the authenticated auditor."""

	if user.account_type != AccountType.AUDITOR:
		raise HTTPException(status_code=403, detail="Auditor access is required.")

	auditor = await _get_current_auditor(session, user)

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

	if user.account_type != AccountType.AUDITOR:
		raise HTTPException(status_code=403, detail="Auditor access is required.")

	auditor = await _get_current_auditor(session, user)
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
		score = score_yee_responses(submission.responses_json)
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
		score = score_yee_responses(responses)
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

	if user.account_type != AccountType.AUDITOR:
		raise HTTPException(status_code=403, detail="Auditor access is required.")

	auditor = await _get_current_auditor(session, user)
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

	score = score_yee_responses(payload.responses)
	if existing_audit is None:
		existing_audit = Audit(
			project_id=assignment.project_id,
			place_id=place_id,
			auditor_profile_id=auditor.id,
			audit_code=f"YEE-{uuid.uuid4().hex[:8].upper()}",
			instrument_key="yee",
			instrument_version="1",
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

	score = score_yee_responses(payload.responses)
	return ScoreResult(**score)


@router.post(
	"/audits",
	response_model=YeeAuditSubmissionResponse,
	status_code=status.HTTP_201_CREATED,
)
async def submit_yee_audit(
	payload: SubmitYeeAuditRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> YeeAuditSubmissionResponse:
	"""Compute and persist an authenticated YEE audit submission."""

	if user.account_type != AccountType.AUDITOR:
		raise HTTPException(status_code=403, detail="Auditor access is required.")

	auditor = await _get_current_auditor(session, user)
	assignment, place = await _get_assigned_place(session, auditor=auditor, place_id=payload.place_id)

	existing_submission_stmt = select(YeeAuditSubmission).where(
		YeeAuditSubmission.auditor_id == auditor.id,
		YeeAuditSubmission.place_id == payload.place_id,
	)
	existing_submission = (await session.execute(existing_submission_stmt)).scalar_one_or_none()
	if existing_submission is not None:
		raise HTTPException(status_code=409, detail="You have already submitted an audit for this place.")

	score = score_yee_responses(payload.responses)
	audit = await _get_latest_yee_audit(session, auditor=auditor, place_id=payload.place_id)
	if audit is None:
		audit = Audit(
			project_id=assignment.project_id,
			place_id=payload.place_id,
			auditor_profile_id=auditor.id,
			audit_code=f"YEE-{uuid.uuid4().hex[:8].upper()}",
			instrument_key="yee",
			instrument_version="1",
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
	}
	audit.summary_score = float(int(score["total_score"]))

	submission = YeeAuditSubmission(
		auditor_id=auditor.id,
		place_id=payload.place_id,
		participant_info_json=payload.participant_info,
		responses_json=payload.responses,
		section_scores_json=score["section_scores"],
		total_score=score["total_score"],
	)
	session.add(submission)
	await session.commit()
	await session.refresh(submission)

	return YeeAuditSubmissionResponse(
		id=submission.id,
		place_id=submission.place_id,
		place_name=place.name,
		auditor_id=submission.auditor_id,
		auditor_generated_id=_public_auditor_id(auditor.auditor_code),
		submitted_at=submission.submitted_at,
		participant_info=submission.participant_info_json,
		responses=submission.responses_json,
		score=ScoreResult(**score),
	)


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

	if user.account_type == AccountType.AUDITOR:
		auditor = await _get_current_auditor(session, user)
		if auditor is None or submission.auditor_id != auditor.id:
			raise HTTPException(status_code=403, detail="You do not have access to this submission.")
	else:
		auditor = (
			await session.execute(select(Auditor).where(Auditor.id == submission.auditor_id))
		).scalar_one_or_none()

	place = (await session.execute(select(Place).where(Place.id == submission.place_id))).scalar_one_or_none()

	# Recompute score from stored responses so this endpoint always returns
	# the same full scoring shape as /yee/audits and /yee/audits/score.
	score = score_yee_responses(submission.responses_json)
	return YeeAuditSubmissionResponse(
		id=submission.id,
		place_id=submission.place_id,
		place_name=place.name if place is not None else None,
		auditor_id=submission.auditor_id,
		auditor_generated_id=_public_auditor_id(auditor.auditor_code) if auditor is not None else None,
		submitted_at=submission.submitted_at,
		participant_info=submission.participant_info_json,
		responses=submission.responses_json,
		score=ScoreResult(**score),
	)

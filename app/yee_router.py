"""YEE REST API endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_auth_session, get_current_user
from app.models import AccountType, Assignment, Audit, AuditStatus, Auditor, Instrument, Place, YeeAuditSubmission, User
from app.yee_scoring import get_yee_instrument_data, score_yee_responses

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


class ScoreResult(BaseModel):
    total_score: int
    section_scores: dict[str, int]
    category_scores: dict[str, int]
    matched_scored_answers: int


class YeeAuditSubmissionResponse(BaseModel):
    id: uuid.UUID
    place_id: uuid.UUID
    auditor_id: uuid.UUID
    submitted_at: datetime
    participant_info: dict[str, Any]
    responses: dict[str, Any]
    score: ScoreResult


class MyYeeAuditItem(BaseModel):
    id: uuid.UUID
    place_id: uuid.UUID
    place_name: str
    submitted_at: datetime
    total_score: int


@router.get("/instrument")
def get_yee_instrument() -> dict[str, object]:
    """Return YEE instrument metadata and scoring matrix extracted from QSF."""

    return get_yee_instrument_data()


@router.get("/my-audits", response_model=list[MyYeeAuditItem])
async def list_my_yee_audits(
    user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_auth_session),
) -> list[MyYeeAuditItem]:
    """Return submitted YEE audits for the authenticated auditor."""

    if user.account_type != AccountType.AUDITOR:
        raise HTTPException(status_code=403, detail="Auditor access is required.")

    auditor_result = await session.execute(select(Auditor).where(Auditor.user_id == user.id))
    auditor = auditor_result.scalar_one_or_none()
    if auditor is None:
        raise HTTPException(status_code=404, detail="Auditor profile not found.")

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

    auditor_result = await session.execute(select(Auditor).where(Auditor.user_id == user.id))
    auditor = auditor_result.scalar_one_or_none()
    if auditor is None:
        raise HTTPException(status_code=404, detail="Auditor profile not found.")

    assignment_stmt = select(Assignment).where(
        Assignment.auditor_id == auditor.id,
        Assignment.place_id == payload.place_id,
    )
    assignment = (await session.execute(assignment_stmt)).scalar_one_or_none()
    if assignment is None:
        raise HTTPException(status_code=403, detail="This place is not assigned to you.")

    existing_submission_stmt = select(YeeAuditSubmission).where(
        YeeAuditSubmission.auditor_id == auditor.id,
        YeeAuditSubmission.place_id == payload.place_id,
    )
    existing_submission = (await session.execute(existing_submission_stmt)).scalar_one_or_none()
    if existing_submission is not None:
        raise HTTPException(status_code=409, detail="You have already submitted an audit for this place.")

    score = score_yee_responses(payload.responses)
    instrument_result = await session.execute(select(Instrument).where(Instrument.key == "yee").order_by(Instrument.version.desc()))
    instrument = instrument_result.scalar_one_or_none()
    if instrument is None:
        instrument = Instrument(key="yee", version="1", name="Youth Enabling Environments")
        session.add(instrument)
        await session.flush()

    audit = Audit(
        instrument_id=instrument.id,
        place_id=payload.place_id,
        auditor_id=auditor.id,
        status=AuditStatus.SUBMITTED,
        submitted_at=datetime.now(timezone.utc),
        responses_json=payload.responses,
        scores_json={
            "total_score": score["total_score"],
            "section_scores": score["section_scores"],
            "category_scores": score["category_scores"],
            "matched_scored_answers": score["matched_scored_answers"],
        },
    )
    session.add(audit)

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
        auditor_id=submission.auditor_id,
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
        auditor_result = await session.execute(select(Auditor).where(Auditor.user_id == user.id))
        auditor = auditor_result.scalar_one_or_none()
        if auditor is None or submission.auditor_id != auditor.id:
            raise HTTPException(status_code=403, detail="You do not have access to this submission.")

    # Recompute score from stored responses so this endpoint always returns
    # the same full scoring shape as /yee/audits and /yee/audits/score.
    score = score_yee_responses(submission.responses_json)
    return YeeAuditSubmissionResponse(
        id=submission.id,
        place_id=submission.place_id,
        auditor_id=submission.auditor_id,
        submitted_at=submission.submitted_at,
        participant_info=submission.participant_info_json,
        responses=submission.responses_json,
        score=ScoreResult(**score),
    )

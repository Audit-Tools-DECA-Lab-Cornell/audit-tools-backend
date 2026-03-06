"""YEE REST API endpoints."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_async_session_yee
from app.models import YeeAuditSubmission
from app.yee_scoring import get_yee_instrument_data, score_yee_responses

router: APIRouter = APIRouter(prefix="/yee", tags=["yee"])


class SubmitYeeAuditRequest(BaseModel):
    """
    YEE audit submission payload.

    `responses` format:
    - Single-choice item: {"QID22": "3"}
    - Matrix-like item: {"QID1#2": {"1": "3", "2": "2"}}
    """

    participant_info: dict[str, Any] = Field(default_factory=dict)
    responses: dict[str, Any] = Field(default_factory=dict)


class ScoreResult(BaseModel):
    total_score: int
    section_scores: dict[str, int]
    category_scores: dict[str, int]
    matched_scored_answers: int


class YeeAuditSubmissionResponse(BaseModel):
    id: uuid.UUID
    submitted_at: datetime
    participant_info: dict[str, Any]
    responses: dict[str, Any]
    score: ScoreResult


@router.get("/instrument")
def get_yee_instrument() -> dict[str, object]:
    """Return YEE instrument metadata and scoring matrix extracted from QSF."""

    return get_yee_instrument_data()


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
    session: AsyncSession = Depends(get_async_session_yee),
) -> YeeAuditSubmissionResponse:
    """Compute and persist a YEE audit submission."""

    score = score_yee_responses(payload.responses)
    submission = YeeAuditSubmission(
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
        submitted_at=submission.submitted_at,
        participant_info=submission.participant_info_json,
        responses=submission.responses_json,
        score=ScoreResult(**score),
    )


@router.get("/audits/{submission_id}", response_model=YeeAuditSubmissionResponse)
async def get_yee_submission(
    submission_id: uuid.UUID,
    session: AsyncSession = Depends(get_async_session_yee),
) -> YeeAuditSubmissionResponse:
    """Fetch a previously submitted YEE audit and return stored score."""

    stmt = select(YeeAuditSubmission).where(YeeAuditSubmission.id == submission_id)
    result = await session.execute(stmt)
    submission = result.scalar_one_or_none()
    if submission is None:
        raise HTTPException(status_code=404, detail="YEE submission not found.")

    # Recompute score from stored responses so this endpoint always returns
    # the same full scoring shape as /yee/audits and /yee/audits/score.
    score = score_yee_responses(submission.responses_json)
    return YeeAuditSubmissionResponse(
        id=submission.id,
        submitted_at=submission.submitted_at,
        participant_info=submission.participant_info_json,
        responses=submission.responses_json,
        score=ScoreResult(**score),
    )

"""
Playspace audit assignment, draft, and progress schemas.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field

from app.models import AuditStatus
from app.products.playspace.schemas.base import ApiModel, RequestModel
from app.products.playspace.schemas.instrument import (
    ExecutionMode,
    PlayspaceInstrumentResponse,
)

######################################################################################
#################################### Audit Schemas ###################################
######################################################################################

QuestionResponseValue = str | list[str] | dict[str, str] | None
QuestionResponsePayload = dict[str, QuestionResponseValue]


class AssignmentResponse(ApiModel):
    """Manager-facing assignment record for project or project-place scope."""

    id: uuid.UUID
    auditor_profile_id: uuid.UUID
    project_id: uuid.UUID
    place_id: uuid.UUID | None
    scope_type: Literal["project", "place"]
    scope_id: uuid.UUID
    scope_name: str
    project_name: str
    place_name: str | None
    assigned_at: datetime


class AssignmentWriteRequest(RequestModel):
    """Create or update a project or project-place assignment."""

    project_id: uuid.UUID
    place_id: uuid.UUID | None = None


class AuditMetaPatchRequest(RequestModel):
    """Mutable execution metadata stored with a Playspace audit draft."""

    execution_mode: ExecutionMode | None = None


class AuditMetaResponse(ApiModel):
    """Typed execution metadata returned with an audit session."""

    execution_mode: ExecutionMode | None


class PreAuditPatchRequest(RequestModel):
    """Structured pre-audit answers shown on the first page."""

    place_size: str | None = None
    current_users_0_5: str | None = None
    current_users_6_12: str | None = None
    current_users_13_17: str | None = None
    current_users_18_plus: str | None = None
    playspace_busyness: str | None = None
    season: str | None = None
    weather_conditions: list[str] = Field(default_factory=list)
    wind_conditions: str | None = None


class PreAuditResponse(ApiModel):
    """Typed pre-audit answers returned with an audit session."""

    place_size: str | None = None
    current_users_0_5: str | None = None
    current_users_6_12: str | None = None
    current_users_13_17: str | None = None
    current_users_18_plus: str | None = None
    playspace_busyness: str | None = None
    season: str | None = None
    weather_conditions: list[str] = Field(default_factory=list)
    wind_conditions: str | None = None


class SectionDraftPatchRequest(RequestModel):
    """Per-section draft answers and free-text note."""

    responses: dict[str, QuestionResponsePayload] = Field(default_factory=dict)
    note: str | None = None


class AuditSectionStateResponse(ApiModel):
    """Typed section payload returned with an audit session."""

    section_key: str
    responses: dict[str, QuestionResponsePayload] = Field(default_factory=dict)
    note: str | None = None


class AuditScoreTotalsResponse(ApiModel):
    """One raw Playspace score bucket for overall, section, or domain totals."""

    quantity_total: float
    diversity_total: float
    challenge_total: float
    sociability_total: float
    play_value_total: float
    usability_total: float


class AuditScoresResponse(ApiModel):
    """Typed calculated score payload for drafts and submitted audits."""

    draft_progress_percent: float | None = None
    execution_mode: ExecutionMode | None = None
    overall: AuditScoreTotalsResponse | None = None
    by_section: dict[str, AuditScoreTotalsResponse] = Field(default_factory=dict)
    by_domain: dict[str, AuditScoreTotalsResponse] = Field(default_factory=dict)


class AuditAggregateWriteRequest(RequestModel):
    """Canonical aggregate payload written by revision-aware draft sync clients."""

    schema_version: int | None = Field(default=None, ge=1)
    meta: AuditMetaPatchRequest | None = None
    pre_audit: PreAuditPatchRequest | None = None
    sections: dict[str, SectionDraftPatchRequest] = Field(default_factory=dict)


class AuditAggregateResponse(ApiModel):
    """Canonical audit aggregate returned by the backend."""

    schema_version: int
    revision: int
    meta: AuditMetaResponse
    pre_audit: PreAuditResponse
    sections: dict[str, AuditSectionStateResponse]


class AuditDraftPatchRequest(RequestModel):
    """Draft-save payload supporting aggregate writes and legacy fragment compatibility."""

    expected_revision: int | None = Field(default=None, ge=0)
    aggregate: AuditAggregateWriteRequest | None = None
    meta: AuditMetaPatchRequest | None = None
    pre_audit: PreAuditPatchRequest | None = None
    sections: dict[str, SectionDraftPatchRequest] = Field(default_factory=dict)


class AuditDraftSaveResponse(ApiModel):
    """Lightweight acknowledgement returned after saving an audit draft."""

    audit_id: uuid.UUID
    status: AuditStatus
    schema_version: int
    revision: int
    draft_progress_percent: float | None = None
    saved_at: datetime


class AuditSubmitRequest(RequestModel):
    """Optional revision check payload for final audit submission."""

    expected_revision: int | None = Field(default=None, ge=0)


class PlaceAuditAccessRequest(RequestModel):
    """Project-place target used when creating or resuming an audit session."""

    project_id: uuid.UUID
    execution_mode: ExecutionMode | None = None


class AuditSectionProgressResponse(ApiModel):
    """Section-level progress summary used by the mobile overview screen."""

    section_key: str
    title: str
    visible_question_count: int
    answered_question_count: int
    is_complete: bool


class AuditProgressResponse(ApiModel):
    """Computed audit completion state derived from instrument + stored responses."""

    required_pre_audit_complete: bool
    visible_section_count: int
    completed_section_count: int
    total_visible_questions: int
    answered_visible_questions: int
    ready_to_submit: bool
    sections: list[AuditSectionProgressResponse]


class AuditorPlaceResponse(ApiModel):
    """Project-place summary visible to auditors on dashboard and places tabs."""

    place_id: uuid.UUID
    place_name: str
    place_type: str | None
    project_id: uuid.UUID
    project_name: str
    city: str | None
    province: str | None
    country: str | None
    lat: float | None
    lng: float | None
    audit_status: AuditStatus | None
    audit_id: uuid.UUID | None
    started_at: datetime | None
    submitted_at: datetime | None
    summary_score: float | None
    score_totals: AuditScoreTotalsResponse | None = None
    progress_percent: float | None
    selected_execution_mode: ExecutionMode | None = None


class AuditorAuditSummaryResponse(ApiModel):
    """Audit row visible in auditor report and activity screens."""

    audit_id: uuid.UUID
    audit_code: str
    place_id: uuid.UUID
    place_name: str
    project_id: uuid.UUID
    project_name: str
    status: AuditStatus
    started_at: datetime
    submitted_at: datetime | None
    summary_score: float | None
    score_totals: AuditScoreTotalsResponse | None = None
    progress_percent: float | None


class AuditorDashboardSummaryResponse(ApiModel):
    """Top-level auditor dashboard metrics."""

    total_assigned_places: int
    in_progress_audits: int
    submitted_audits: int
    pending_places: int
    average_submitted_score: float | None


class AuditSessionResponse(ApiModel):
    """Audit state returned for create/resume, draft saves, and submission."""

    audit_id: uuid.UUID
    audit_code: str
    project_id: uuid.UUID
    project_name: str
    place_id: uuid.UUID
    place_name: str
    place_type: str | None
    allowed_execution_modes: list[ExecutionMode]
    selected_execution_mode: ExecutionMode | None
    status: AuditStatus
    instrument_key: str
    instrument_version: str
    instrument: PlayspaceInstrumentResponse
    schema_version: int
    revision: int
    aggregate: AuditAggregateResponse
    started_at: datetime
    submitted_at: datetime | None
    total_minutes: int | None
    meta: AuditMetaResponse
    pre_audit: PreAuditResponse
    sections: dict[str, AuditSectionStateResponse]
    scores: AuditScoresResponse
    progress: AuditProgressResponse

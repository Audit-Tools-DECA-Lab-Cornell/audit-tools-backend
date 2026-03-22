"""
Playspace audit assignment, draft, and progress schemas.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator

from app.models import AuditStatus
from app.products.playspace.schemas.base import ApiModel, RequestModel
from app.products.playspace.schemas.instrument import AssignmentRole, ExecutionMode

######################################################################################
#################################### Audit Schemas ###################################
######################################################################################


class AssignmentResponse(ApiModel):
    """Manager-facing assignment record with place-scoped capabilities."""

    id: uuid.UUID
    auditor_profile_id: uuid.UUID
    project_id: uuid.UUID | None
    place_id: uuid.UUID | None
    scope_type: Literal["project", "place"]
    scope_id: uuid.UUID
    scope_name: str
    project_name: str
    place_name: str | None
    audit_roles: list[AssignmentRole]
    assigned_at: datetime


class AssignmentWriteRequest(RequestModel):
    """Create or update a project/place assignment for an auditor profile."""

    project_id: uuid.UUID | None = None
    place_id: uuid.UUID | None = None
    audit_roles: list[AssignmentRole] = Field(default_factory=lambda: [AssignmentRole.AUDITOR])

    @field_validator("audit_roles")
    @classmethod
    def validate_audit_roles(cls, value: list[AssignmentRole]) -> list[AssignmentRole]:
        """Ensure assignment roles are non-empty and de-duplicated."""

        if not value:
            raise ValueError("audit_roles must contain at least one role.")

        ordered_unique: list[AssignmentRole] = []
        seen: set[AssignmentRole] = set()
        for role in value:
            if role in seen:
                continue
            seen.add(role)
            ordered_unique.append(role)
        return ordered_unique


class AuditMetaPatchRequest(RequestModel):
    """Mutable execution metadata stored with a Playspace audit draft."""

    execution_mode: ExecutionMode | None = None


class AuditMetaResponse(ApiModel):
    """Typed execution metadata returned with an audit session."""

    execution_mode: ExecutionMode | None


class PreAuditPatchRequest(RequestModel):
    """Structured pre-audit answers shown on the first page."""

    season: str | None = None
    weather_conditions: list[str] = Field(default_factory=list)
    users_present: list[str] = Field(default_factory=list)
    user_count: str | None = None
    age_groups: list[str] = Field(default_factory=list)
    place_size: str | None = None


class PreAuditResponse(ApiModel):
    """Typed pre-audit answers returned with an audit session."""

    season: str | None = None
    weather_conditions: list[str] = Field(default_factory=list)
    users_present: list[str] = Field(default_factory=list)
    user_count: str | None = None
    age_groups: list[str] = Field(default_factory=list)
    place_size: str | None = None


class SectionDraftPatchRequest(RequestModel):
    """Per-section draft answers and free-text note."""

    responses: dict[str, dict[str, str]] = Field(default_factory=dict)
    note: str | None = None


class AuditSectionStateResponse(ApiModel):
    """Typed section payload returned with an audit session."""

    section_key: str
    responses: dict[str, dict[str, str]] = Field(default_factory=dict)
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


class AuditDraftPatchRequest(RequestModel):
    """Patch payload for saving an audit draft without replacing the full document."""

    meta: AuditMetaPatchRequest | None = None
    pre_audit: PreAuditPatchRequest | None = None
    sections: dict[str, SectionDraftPatchRequest] = Field(default_factory=dict)


class AuditDraftSaveResponse(ApiModel):
    """Lightweight acknowledgement returned after saving an audit draft."""

    audit_id: uuid.UUID
    status: AuditStatus
    draft_progress_percent: float | None = None
    saved_at: datetime


class PlaceAuditAccessRequest(RequestModel):
    """Optional mode hint used when creating or resuming an audit session."""

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
    """Place summary visible to auditors on mobile dashboard and places tabs."""

    place_id: uuid.UUID
    place_name: str
    place_type: str | None
    project_id: uuid.UUID
    project_name: str
    city: str | None
    province: str | None
    country: str | None
    assignment_roles: list[AssignmentRole]
    audit_status: AuditStatus | None
    audit_id: uuid.UUID | None
    started_at: datetime | None
    submitted_at: datetime | None
    summary_score: float | None
    score_totals: AuditScoreTotalsResponse | None = None
    progress_percent: float | None


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
    place_id: uuid.UUID
    place_name: str
    place_type: str | None
    assignment_roles: list[AssignmentRole]
    allowed_execution_modes: list[ExecutionMode]
    selected_execution_mode: ExecutionMode | None
    status: AuditStatus
    instrument_key: str
    instrument_version: str
    started_at: datetime
    submitted_at: datetime | None
    total_minutes: int | None
    meta: AuditMetaResponse
    pre_audit: PreAuditResponse
    sections: dict[str, AuditSectionStateResponse]
    scores: AuditScoresResponse
    progress: AuditProgressResponse

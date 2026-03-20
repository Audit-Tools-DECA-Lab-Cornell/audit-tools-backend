"""
Playspace audit assignment, draft, and progress schemas.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field, field_validator

from app.models import AuditStatus
from app.products.playspace.schemas.base import ApiModel, JsonDict, RequestModel
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
    """Mutable execution metadata stored in `responses_json.meta`."""

    execution_mode: ExecutionMode | None = None


class PreAuditPatchRequest(RequestModel):
    """Structured pre-audit answers shown on the first page."""

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


class AuditDraftPatchRequest(RequestModel):
    """Patch payload for saving an audit draft without replacing the full document."""

    meta: AuditMetaPatchRequest | None = None
    pre_audit: PreAuditPatchRequest | None = None
    sections: dict[str, SectionDraftPatchRequest] = Field(default_factory=dict)


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
    progress_percent: float | None


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
    responses_json: JsonDict
    scores_json: JsonDict
    progress: AuditProgressResponse

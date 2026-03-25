"""Parity tests for Playspace canonical aggregate migration tooling."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models import (
    Audit,
    AuditStatus,
    PlayspaceAuditContext,
    PlayspaceAuditSection,
    PlayspacePreAuditAnswer,
    PlayspaceQuestionResponse,
    PlayspaceScaleAnswer,
)
from app.products.playspace.migration_tools import (
    migrate_audit_to_canonical_aggregate,
    verify_audit_aggregate_parity,
)


def _build_audit(status: AuditStatus) -> Audit:
    """Create one in-memory Playspace audit with legacy relation-backed answers."""

    audit = Audit(
        project_id=uuid.uuid4(),
        place_id=uuid.uuid4(),
        auditor_profile_id=uuid.uuid4(),
        audit_code=f"AUDIT-{uuid.uuid4()}",
        instrument_key="pvua_v5_2",
        instrument_version="5.2",
        status=status,
        started_at=datetime.now(timezone.utc),
        responses_json={},
        scores_json={},
    )
    audit.playspace_context = PlayspaceAuditContext(
        execution_mode="survey",
        draft_progress_percent=None,
    )
    audit.playspace_pre_audit_answers = [
        PlayspacePreAuditAnswer(field_key="season", selected_value="spring", sort_order=0),
        PlayspacePreAuditAnswer(
            field_key="weather_conditions",
            selected_value="windy",
            sort_order=0,
        ),
    ]

    section = PlayspaceAuditSection(
        section_key="section_1_playspace_character_community",
        note="Legacy note",
    )
    question = PlayspaceQuestionResponse(question_key="q_1_1")
    question.scale_answers = [
        PlayspaceScaleAnswer(scale_key="quantity", option_key="a_lot"),
        PlayspaceScaleAnswer(scale_key="diversity", option_key="some_diversity"),
    ]
    section.question_responses = [question]
    audit.playspace_sections = [section]
    return audit


def test_migrate_draft_audit_to_canonical_aggregate_preserves_parity() -> None:
    """Draft migrations should normalize responses_json without changing behavior."""

    audit = _build_audit(AuditStatus.IN_PROGRESS)

    result = migrate_audit_to_canonical_aggregate(audit)

    assert result.is_matching
    assert audit.responses_json["schema_version"] == 1
    assert audit.responses_json["revision"] == 0
    assert audit.responses_json["meta"] == {"execution_mode": "survey"}
    assert audit.responses_json["sections"]["section_1_playspace_character_community"]["note"] == "Legacy note"
    assert audit.scores_json["draft_progress_percent"] is not None


def test_migrate_submitted_audit_to_canonical_aggregate_preserves_scoring() -> None:
    """Submitted migrations should rebuild overall scores from the canonical aggregate."""

    audit = _build_audit(AuditStatus.SUBMITTED)

    result = migrate_audit_to_canonical_aggregate(audit)
    parity = verify_audit_aggregate_parity(audit)

    assert result.is_matching
    assert parity.is_matching
    assert isinstance(audit.scores_json.get("overall"), dict)
    assert audit.summary_score is not None

"""Tests for compact auditor dashboard summary helpers."""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from app.models import AuditStatus
from app.products.playspace.scoring import _get_visible_questions
from app.products.playspace.schemas.audit import AuditorPlaceResponse
from app.products.playspace.schemas.instrument import ExecutionMode
from app.products.playspace.scoring_metadata import SCORING_SECTIONS

from app.products.playspace.services.audit import PlayspaceAuditService
from app.products.playspace.services.audit_sessions import _CompactAuditSnapshot


def _build_service() -> PlayspaceAuditService:
    """Create a service instance without requiring a live database session."""

    return object.__new__(PlayspaceAuditService)


def test_resolve_compact_audit_summary_prefers_cached_overall_totals() -> None:
    """Compact summaries should use cached overall totals before fallback columns."""

    service = _build_service()

    score_totals, summary_score = service._resolve_compact_audit_summary(
        raw_scores={
            "overall": {
                "provision_total": 1.0,
                "diversity_total": 2.0,
                "challenge_total": 3.0,
                "sociability_total": 4.0,
                "play_value_total": 5.25,
                "usability_total": 1.75,
            }
        },
        fallback_summary_score=11.0,
    )

    assert score_totals is not None
    assert score_totals.play_value_total == 5.25
    assert score_totals.usability_total == 1.75
    assert summary_score == 7.0


def test_resolve_compact_audit_summary_falls_back_to_stored_summary_score() -> None:
    """Stored summary_score should be used when cached totals are incomplete."""

    service = _build_service()

    score_totals, summary_score = service._resolve_compact_audit_summary(
        raw_scores={
            "overall": {
                "provision_total": 1.0,
                "diversity_total": 2.0,
                "challenge_total": 3.0,
                "sociability_total": 4.0,
                "play_value_total": "invalid",
                "usability_total": 1.75,
            }
        },
        fallback_summary_score=8.4,
    )

    assert score_totals is None
    assert summary_score == 8.4


def test_get_visible_questions_returns_all_questions_for_both_mode() -> None:
    """Execution mode `both` should expose all questions in a section."""

    section = next(
        current_section
        for current_section in SCORING_SECTIONS
        if any(question.mode != "both" for question in current_section.questions)
    )

    visible_questions = _get_visible_questions(
        section=section,
        execution_mode=ExecutionMode.BOTH,
    )

    assert len(visible_questions) == len(section.questions)


def test_compact_audit_snapshot_tracks_selected_execution_mode() -> None:
    """Compact audit snapshots should preserve the latest execution mode."""

    snapshot = _CompactAuditSnapshot(
        audit_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        place_id=uuid.uuid4(),
        audit_code="AUD-001",
        status=AuditStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        submitted_at=None,
        summary_score=None,
        score_totals=None,
        progress_percent=50.0,
        selected_execution_mode=ExecutionMode.SURVEY,
    )

    assert snapshot.selected_execution_mode is ExecutionMode.SURVEY


def test_auditor_place_response_schema_exposes_selected_execution_mode() -> None:
    """Auditor place responses should include the selected execution mode field."""

    response_schema = AuditorPlaceResponse.model_json_schema()

    assert "selected_execution_mode" in response_schema.get("properties", {})

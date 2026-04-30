"""Tests for compact auditor dashboard summary helpers."""

from __future__ import annotations

import uuid

from app.models import AuditStatus, PlayspaceSubmission
from app.products.playspace.audit_state import set_execution_mode_value
from app.products.playspace.schemas.audit import AuditorPlaceResponse
from app.products.playspace.schemas.instrument import ExecutionMode
from app.products.playspace.scoring import _get_visible_questions
from app.products.playspace.scoring_metadata import SCORING_SECTIONS
from app.products.playspace.services.audit import PlayspaceAuditService
from app.products.playspace.services.audit_sessions import (
	_derive_place_axis_status,
	_resolve_composite_place_status,
)


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
				"provision_total_max": 10.0,
				"diversity_total": 2.0,
				"diversity_total_max": 10.0,
				"challenge_total": 3.0,
				"challenge_total_max": 10.0,
				"sociability_total": 4.0,
				"sociability_total_max": 10.0,
				"play_value_total": 5.25,
				"play_value_total_max": 10.0,
				"usability_total": 1.75,
				"usability_total_max": 10.0,
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
				"provision_total_max": 10.0,
				"diversity_total": 2.0,
				"diversity_total_max": 10.0,
				"challenge_total": 3.0,
				"challenge_total_max": 10.0,
				"sociability_total": 4.0,
				"sociability_total_max": 10.0,
				"play_value_total": "invalid",
				"play_value_total_max": 10.0,
				"usability_total": 1.75,
				"usability_total_max": 10.0,
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
		section_answers={},
	)

	assert len(visible_questions) == len(section.questions)


def test_derive_place_axis_status_not_started_when_no_submission() -> None:
	"""Axis status should be not_started when the auditor has no submission."""

	assert _derive_place_axis_status(axis_included=True, audit_status=None) == "not_started"


def test_derive_place_axis_status_not_started_when_axis_not_covered() -> None:
	"""Axis status should be not_started when the mode does not cover the axis."""

	assert _derive_place_axis_status(axis_included=False, audit_status=AuditStatus.IN_PROGRESS) == "not_started"


def test_derive_place_axis_status_in_progress_when_active() -> None:
	"""An active (IN_PROGRESS) submission on a covered axis should yield in_progress."""

	assert _derive_place_axis_status(axis_included=True, audit_status=AuditStatus.IN_PROGRESS) == "in_progress"


def test_derive_place_axis_status_submitted_when_submitted() -> None:
	"""A SUBMITTED submission on a covered axis should yield submitted."""

	assert _derive_place_axis_status(axis_included=True, audit_status=AuditStatus.SUBMITTED) == "submitted"


def test_resolve_composite_status_audit_mode_uses_audit_axis() -> None:
	"""In audit-only mode the composite status mirrors the audit axis status."""

	assert (
		_resolve_composite_place_status(
			place_audit_status="submitted",
			place_survey_status="not_started",
			selected_execution_mode=ExecutionMode.AUDIT,
		)
		== "submitted"
	)


def test_resolve_composite_status_survey_mode_uses_survey_axis() -> None:
	"""In survey-only mode the composite status mirrors the survey axis status."""

	assert (
		_resolve_composite_place_status(
			place_audit_status="not_started",
			place_survey_status="in_progress",
			selected_execution_mode=ExecutionMode.SURVEY,
		)
		== "in_progress"
	)


def test_resolve_composite_status_both_mode_requires_both_axes_submitted() -> None:
	"""In both mode the composite status is submitted only when both axes are submitted."""

	assert (
		_resolve_composite_place_status(
			place_audit_status="submitted",
			place_survey_status="in_progress",
			selected_execution_mode=ExecutionMode.BOTH,
		)
		== "in_progress"
	)
	assert (
		_resolve_composite_place_status(
			place_audit_status="submitted",
			place_survey_status="submitted",
			selected_execution_mode=ExecutionMode.BOTH,
		)
		== "submitted"
	)


def test_set_execution_mode_value_persists_on_submission() -> None:
	"""Execution mode changes should persist on the Playspace submission row."""

	submission = PlayspaceSubmission(
		project_id=uuid.uuid4(),
		place_id=uuid.uuid4(),
		auditor_profile_id=uuid.uuid4(),
		audit_code="PS-001",
		status=AuditStatus.IN_PROGRESS,
		responses_json={},
		scores_json={},
	)

	set_execution_mode_value(audit=submission, execution_mode=ExecutionMode.BOTH.value)

	assert submission.execution_mode == ExecutionMode.BOTH.value


def test_auditor_place_response_schema_exposes_selected_execution_mode() -> None:
	"""Auditor place responses should include the selected execution mode field."""

	response_schema = AuditorPlaceResponse.model_json_schema()

	assert "selected_execution_mode" in response_schema.get("properties", {})

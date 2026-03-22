"""Tests for compact auditor dashboard summary helpers."""

from __future__ import annotations

from app.products.playspace.services.audit import PlayspaceAuditService


def _build_service() -> PlayspaceAuditService:
    """Create a service instance without requiring a live database session."""

    return object.__new__(PlayspaceAuditService)


def test_resolve_compact_audit_summary_prefers_cached_overall_totals() -> None:
    """Compact summaries should use cached overall totals before fallback columns."""

    service = _build_service()

    score_totals, summary_score = service._resolve_compact_audit_summary(
        raw_scores={
            "overall": {
                "quantity_total": 1.0,
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
                "quantity_total": 1.0,
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

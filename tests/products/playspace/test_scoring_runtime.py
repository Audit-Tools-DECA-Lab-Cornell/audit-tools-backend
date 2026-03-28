"""Regression tests for backend scoring/progress question visibility rules."""

from __future__ import annotations

from app.products.playspace.schemas.instrument import ExecutionMode
from app.products.playspace.scoring import build_audit_progress, score_audit
from app.products.playspace.scoring_metadata import (
    ScoringDisplayCondition,
    ScoringQuestion,
    ScoringScale,
    ScoringScaleOption,
    ScoringSection,
)


def _build_custom_section() -> ScoringSection:
    """Create a tiny section with a scaled parent and optional checklist child."""

    return ScoringSection(
        section_key="section_demo",
        questions=[
            ScoringQuestion(
                question_key="q_parent",
                mode="audit",
                constructs=["usability"],
                domains=["Demo"],
                question_type="scaled",
                required=True,
                display_if=None,
                options=[],
                scales=[
                    ScoringScale(
                        key="quantity",
                        options=[
                            ScoringScaleOption(
                                key="no",
                                addition_value=0.0,
                                boost_value=0.0,
                                allows_follow_up_scales=False,
                            ),
                            ScoringScaleOption(
                                key="some",
                                addition_value=1.0,
                                boost_value=1.0,
                                allows_follow_up_scales=True,
                            ),
                        ],
                    )
                ],
            ),
            ScoringQuestion(
                question_key="q_child_checklist",
                mode="audit",
                constructs=[],
                domains=["Demo"],
                question_type="checklist",
                required=False,
                display_if=ScoringDisplayCondition(
                    question_key="q_parent",
                    response_key="quantity",
                    any_of_option_keys=["some"],
                ),
                options=["cups", "buckets"],
                scales=[],
            ),
        ],
    )


def test_build_audit_progress_ignores_optional_checklist_follow_up_questions(
    monkeypatch,
) -> None:
    """Optional checklist follow-ups should not block section completion or submission."""

    custom_sections = [_build_custom_section()]
    monkeypatch.setattr("app.products.playspace.scoring.get_scoring_sections", lambda: custom_sections)

    progress = build_audit_progress(
        responses_json={
            "meta": {"execution_mode": ExecutionMode.AUDIT.value},
            "pre_audit": {
                "place_size": "medium",
                "current_users_0_5": "none",
                "current_users_6_12": "some",
                "current_users_13_17": "none",
                "current_users_18_plus": "none",
                "playspace_busyness": "some",
                "season": "summer",
                "weather_conditions": ["sunshine"],
                "wind_conditions": "calm",
            },
            "sections": {
                "section_demo": {
                    "responses": {
                        "q_parent": {
                            "quantity": "some",
                        }
                    }
                }
            },
        }
    )

    assert progress.visible_section_count == 1
    assert progress.total_visible_questions == 1
    assert progress.answered_visible_questions == 1
    assert progress.ready_to_submit is True
    assert len(progress.sections) == 1
    assert progress.sections[0].visible_question_count == 1
    assert progress.sections[0].answered_question_count == 1
    assert progress.sections[0].is_complete is True


def test_score_audit_ignores_non_scored_checklist_questions(monkeypatch) -> None:
    """Checklist follow-ups should not contribute to aggregate score totals."""

    custom_sections = [_build_custom_section()]
    monkeypatch.setattr("app.products.playspace.scoring.get_scoring_sections", lambda: custom_sections)

    scores = score_audit(
        responses_json={
            "meta": {"execution_mode": ExecutionMode.AUDIT.value},
            "pre_audit": {
                "place_size": "medium",
                "current_users_0_5": "none",
                "current_users_6_12": "some",
                "current_users_13_17": "none",
                "current_users_18_plus": "none",
                "playspace_busyness": "some",
                "season": "summer",
                "weather_conditions": ["sunshine"],
                "wind_conditions": "calm",
            },
            "sections": {
                "section_demo": {
                    "responses": {
                        "q_parent": {
                            "quantity": "some",
                        },
                        "q_child_checklist": {
                            "selected_option_keys": ["cups"],
                        },
                    }
                }
            },
        }
    )

    assert scores["overall"]["quantity_total"] == 1.0
    assert scores["overall"]["usability_total"] == 1.0

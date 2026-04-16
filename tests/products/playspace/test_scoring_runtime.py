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
                        key="provision",
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
                    response_key="provision",
                    any_of_option_keys=["some"],
                ),
                options=["cups", "buckets"],
                scales=[],
            ),
        ],
    )


def _build_construct_scoring_section() -> ScoringSection:
    """Create one scaled section that exercises totals, max totals, and both constructs."""

    return ScoringSection(
        section_key="section_constructs",
        questions=[
            ScoringQuestion(
                question_key="q_construct",
                mode="audit",
                constructs=["play_value", "usability"],
                domains=["Construct Demo"],
                question_type="scaled",
                required=True,
                display_if=None,
                options=[],
                scales=[
                    ScoringScale(
                        key="provision",
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
                            ScoringScaleOption(
                                key="a_lot",
                                addition_value=2.0,
                                boost_value=2.0,
                                allows_follow_up_scales=True,
                            ),
                        ],
                    ),
                    ScoringScale(
                        key="diversity",
                        options=[
                            ScoringScaleOption(
                                key="not_applicable",
                                addition_value=0.0,
                                boost_value=1.0,
                                allows_follow_up_scales=False,
                            ),
                            ScoringScaleOption(
                                key="some_diversity",
                                addition_value=2.0,
                                boost_value=2.0,
                                allows_follow_up_scales=False,
                            ),
                            ScoringScaleOption(
                                key="a_lot_of_diversity",
                                addition_value=3.0,
                                boost_value=3.0,
                                allows_follow_up_scales=False,
                            ),
                        ],
                    ),
                    ScoringScale(
                        key="challenge",
                        options=[
                            ScoringScaleOption(
                                key="not_applicable",
                                addition_value=0.0,
                                boost_value=1.0,
                                allows_follow_up_scales=False,
                            ),
                            ScoringScaleOption(
                                key="some_challenge",
                                addition_value=2.0,
                                boost_value=2.0,
                                allows_follow_up_scales=False,
                            ),
                            ScoringScaleOption(
                                key="a_lot_of_challenge",
                                addition_value=3.0,
                                boost_value=3.0,
                                allows_follow_up_scales=False,
                            ),
                        ],
                    ),
                    ScoringScale(
                        key="sociability",
                        options=[
                            ScoringScaleOption(
                                key="none",
                                addition_value=1.0,
                                boost_value=1.0,
                                allows_follow_up_scales=False,
                            ),
                            ScoringScaleOption(
                                key="pairs",
                                addition_value=2.0,
                                boost_value=2.0,
                                allows_follow_up_scales=False,
                            ),
                            ScoringScaleOption(
                                key="groups",
                                addition_value=3.0,
                                boost_value=3.0,
                                allows_follow_up_scales=False,
                            ),
                        ],
                    ),
                ],
            )
        ],
    )


def test_build_audit_progress_ignores_optional_checklist_follow_up_questions(
    monkeypatch,
) -> None:
    """Optional checklist follow-ups should not block section completion or submission."""

    custom_sections = [_build_custom_section()]
    monkeypatch.setattr(
        "app.products.playspace.scoring.get_scoring_sections",
        lambda: custom_sections,
    )

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
                            "provision": "some",
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
    monkeypatch.setattr(
        "app.products.playspace.scoring.get_scoring_sections",
        lambda: custom_sections,
    )

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
                            "provision": "some",
                        },
                        "q_child_checklist": {
                            "selected_option_keys": ["cups"],
                        },
                    }
                }
            },
        },
        include_maximums=True,
    )

    assert scores["overall"]["provision_total"] == 1.0
    assert scores["overall"]["usability_total"] == 1.0


def test_score_audit_tracks_maximum_totals_for_scales_and_constructs(monkeypatch) -> None:
    """Scoring should expose raw totals and max-possible totals for the same question."""

    custom_sections = [_build_construct_scoring_section()]
    monkeypatch.setattr(
        "app.products.playspace.scoring.get_scoring_sections",
        lambda: custom_sections,
    )

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
                "section_constructs": {
                    "responses": {
                        "q_construct": {
                            "provision": "some",
                            "diversity": "some_diversity",
                            "challenge": "a_lot_of_challenge",
                            "sociability": "pairs",
                        }
                    }
                }
            },
        },
        include_maximums=True,
    )

    overall = scores["overall"]

    assert overall["provision_total"] == 1.0
    assert overall["provision_total_max"] == 2.0
    assert overall["diversity_total"] == 1.0
    assert overall["diversity_total_max"] == 2.0
    assert overall["challenge_total"] == 2.0
    assert overall["challenge_total_max"] == 2.0
    assert overall["sociability_total"] == 1.0
    assert overall["sociability_total_max"] == 2.0
    assert overall["play_value_total"] == 6.0
    assert overall["play_value_total_max"] == 18.0
    assert overall["usability_total"] == 6.0
    assert overall["usability_total_max"] == 18.0

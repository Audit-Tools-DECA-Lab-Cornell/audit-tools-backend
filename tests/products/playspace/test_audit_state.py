"""Regression tests for normalized Playspace audit state synchronization."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.models import (
    Audit,
    AuditStatus,
    PlayspaceAuditSection,
    PlayspacePreAuditAnswer,
    PlayspaceQuestionResponse,
    PlayspaceScaleAnswer,
)
from app.products.playspace.audit_state import (
    _replace_sections_from_cache,
    apply_draft_patch_to_relations,
)
from app.products.playspace.schemas.audit import (
    AuditDraftPatchRequest,
    PreAuditPatchRequest,
    SectionDraftPatchRequest,
)


def _build_audit() -> Audit:
    """Create an in-memory audit shell for relationship synchronization tests."""

    return Audit(
        place_id=uuid.uuid4(),
        auditor_profile_id=uuid.uuid4(),
        audit_code=f"AUDIT-{uuid.uuid4()}",
        instrument_key="pvua_v5_2",
        instrument_version="5.2",
        status=AuditStatus.IN_PROGRESS,
        started_at=datetime.now(timezone.utc),
        responses_json={"meta": {}, "pre_audit": {}, "sections": {}},
        scores_json={},
    )


def test_apply_draft_patch_reuses_existing_pre_audit_rows() -> None:
    """Repeated pre-audit saves should update matching rows instead of recreating them."""

    audit = _build_audit()
    existing_season = PlayspacePreAuditAnswer(
        field_key="season",
        selected_value="spring",
        sort_order=0,
    )
    existing_weather = PlayspacePreAuditAnswer(
        field_key="weather_conditions",
        selected_value="windy",
        sort_order=0,
    )
    audit.playspace_pre_audit_answers = [existing_season, existing_weather]

    patch = AuditDraftPatchRequest(
        pre_audit=PreAuditPatchRequest(
            season="spring",
            weather_conditions=["windy", "windy", "cloudy"],
            users_present=["adults", "adults"],
            user_count="a_lot",
            age_groups=["age_11_plus", "age_11_plus"],
            place_size="large",
        )
    )

    apply_draft_patch_to_relations(audit=audit, patch=patch)
    answers_by_key = {
        (answer.field_key, answer.selected_value): answer
        for answer in audit.playspace_pre_audit_answers
    }
    assert answers_by_key[("season", "spring")] is existing_season
    assert answers_by_key[("weather_conditions", "windy")] is existing_weather
    assert sorted(
        (answer.field_key, answer.selected_value, answer.sort_order)
        for answer in audit.playspace_pre_audit_answers
    ) == [
        ("age_groups", "age_11_plus", 0),
        ("place_size", "large", 0),
        ("season", "spring", 0),
        ("user_count", "a_lot", 0),
        ("users_present", "adults", 0),
        ("weather_conditions", "cloudy", 1),
        ("weather_conditions", "windy", 0),
    ]

    object_ids_before = {
        (answer.field_key, answer.selected_value): id(answer)
        for answer in audit.playspace_pre_audit_answers
    }

    apply_draft_patch_to_relations(audit=audit, patch=patch)

    assert {
        (answer.field_key, answer.selected_value): id(answer)
        for answer in audit.playspace_pre_audit_answers
    } == object_ids_before


def test_apply_draft_patch_reuses_existing_scale_answers() -> None:
    """Dirty section saves should update scale rows in place by their unique scale key."""

    audit = _build_audit()
    section = PlayspaceAuditSection(section_key="section_a", note=None)
    question = PlayspaceQuestionResponse(question_key="question_a")
    quantity = PlayspaceScaleAnswer(scale_key="quantity", option_key="some")
    question.scale_answers = [quantity]
    section.question_responses = [question]
    audit.playspace_sections = [section]

    patch = AuditDraftPatchRequest(
        sections={
            "section_a": SectionDraftPatchRequest(
                responses={
                    "question_a": {
                        "quantity": "a_lot",
                        "diversity": "some_diversity",
                    }
                },
                note="Updated note",
            )
        }
    )

    apply_draft_patch_to_relations(audit=audit, patch=patch)

    updated_section = audit.playspace_sections[0]
    updated_question = updated_section.question_responses[0]
    answers_by_scale = {
        answer.scale_key: answer for answer in updated_question.scale_answers
    }

    assert updated_section is section
    assert updated_question is question
    assert answers_by_scale["quantity"] is quantity
    assert answers_by_scale["quantity"].option_key == "a_lot"
    assert sorted(answers_by_scale) == ["diversity", "quantity"]

    object_ids_before = {
        answer.scale_key: id(answer) for answer in updated_question.scale_answers
    }

    apply_draft_patch_to_relations(audit=audit, patch=patch)

    assert {
        answer.scale_key: id(answer)
        for answer in audit.playspace_sections[0].question_responses[0].scale_answers
    } == object_ids_before


def test_apply_draft_patch_preserves_omitted_pre_audit_fields_and_clears_note() -> None:
    """Partial draft patches should preserve omitted fields and allow explicit note clearing."""

    audit = _build_audit()
    audit.playspace_pre_audit_answers = [
        PlayspacePreAuditAnswer(field_key="season", selected_value="spring", sort_order=0),
        PlayspacePreAuditAnswer(
            field_key="weather_conditions",
            selected_value="windy",
            sort_order=0,
        ),
    ]
    section = PlayspaceAuditSection(section_key="section_a", note="Keep me?")
    audit.playspace_sections = [section]

    patch = AuditDraftPatchRequest(
        pre_audit=PreAuditPatchRequest(season="summer"),
        sections={
            "section_a": SectionDraftPatchRequest(
                note=None,
            )
        },
    )

    apply_draft_patch_to_relations(audit=audit, patch=patch)

    assert sorted(
        (answer.field_key, answer.selected_value)
        for answer in audit.playspace_pre_audit_answers
    ) == [
        ("season", "summer"),
        ("weather_conditions", "windy"),
    ]
    assert audit.playspace_sections[0].note is None


def test_replace_sections_from_cache_reuses_existing_section_tree() -> None:
    """Cache hydration should reuse matching section, question, and scale ORM rows."""

    audit = _build_audit()
    section = PlayspaceAuditSection(section_key="section_a", note="Before")
    question = PlayspaceQuestionResponse(question_key="question_a")
    quantity = PlayspaceScaleAnswer(scale_key="quantity", option_key="some")
    question.scale_answers = [quantity]
    section.question_responses = [question]
    audit.playspace_sections = [section]
    audit.responses_json = {
        "meta": {},
        "pre_audit": {},
        "sections": {
            "section_a": {
                "note": "After",
                "responses": {
                    "question_a": {
                        "quantity": "some",
                        "diversity": "no_diversity",
                    }
                },
            }
        },
    }

    _replace_sections_from_cache(audit)

    updated_section = audit.playspace_sections[0]
    updated_question = updated_section.question_responses[0]
    answers_by_scale = {
        answer.scale_key: answer for answer in updated_question.scale_answers
    }

    assert updated_section is section
    assert updated_section.note == "After"
    assert updated_question is question
    assert answers_by_scale["quantity"] is quantity
    assert sorted(answers_by_scale) == ["diversity", "quantity"]

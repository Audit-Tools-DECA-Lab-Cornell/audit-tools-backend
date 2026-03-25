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
    replace_audit_aggregate,
)
from app.products.playspace.schemas.audit import (
    AuditAggregateWriteRequest,
    AuditDraftPatchRequest,
    PreAuditPatchRequest,
    SectionDraftPatchRequest,
)


def _build_audit() -> Audit:
    """Create an in-memory audit shell for relationship synchronization tests."""

    return Audit(
        project_id=uuid.uuid4(),
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


def test_apply_draft_patch_merges_pre_audit_into_canonical_aggregate() -> None:
    """Pre-audit saves should update the canonical responses_json aggregate."""

    audit = _build_audit()
    audit.responses_json = {
        "meta": {"execution_mode": "audit"},
        "pre_audit": {
            "season": "spring",
            "weather_conditions": ["windy"],
        },
        "sections": {},
    }

    patch = AuditDraftPatchRequest(
        pre_audit=PreAuditPatchRequest(
            season="summer",
            weather_conditions=["windy", "cloudy"],
            users_present=["adults"],
            user_count="a_lot",
            age_groups=["age_11_plus"],
            place_size="large",
        )
    )

    apply_draft_patch_to_relations(audit=audit, patch=patch)

    assert audit.responses_json["meta"] == {"execution_mode": "audit"}
    assert audit.responses_json["pre_audit"] == {
        "season": "summer",
        "weather_conditions": ["windy", "cloudy"],
        "users_present": ["adults"],
        "user_count": "a_lot",
        "age_groups": ["age_11_plus"],
        "place_size": "large",
    }


def test_apply_draft_patch_merges_section_answers_into_canonical_aggregate() -> None:
    """Section saves should replace one question answer-set inside responses_json."""

    audit = _build_audit()
    audit.responses_json = {
        "meta": {},
        "pre_audit": {},
        "sections": {
            "section_a": {
                "note": "Before",
                "responses": {
                    "question_a": {
                        "quantity": "some",
                    }
                },
            }
        },
    }

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

    assert audit.responses_json["sections"] == {
        "section_a": {
            "note": "Updated note",
            "responses": {
                "question_a": {
                    "quantity": "a_lot",
                    "diversity": "some_diversity",
                }
            },
        }
    }


def test_apply_draft_patch_preserves_omitted_fields_and_allows_clearing_note() -> None:
    """Partial draft patches should preserve aggregate values and allow explicit note clearing."""

    audit = _build_audit()
    audit.responses_json = {
        "meta": {"execution_mode": "survey"},
        "pre_audit": {
            "season": "spring",
            "weather_conditions": ["windy"],
        },
        "sections": {
            "section_a": {
                "note": "Keep me?",
                "responses": {
                    "question_a": {
                        "quantity": "some",
                    }
                },
            }
        },
    }

    patch = AuditDraftPatchRequest(
        pre_audit=PreAuditPatchRequest(season="summer"),
        sections={
            "section_a": SectionDraftPatchRequest(
                note=None,
            )
        },
    )

    apply_draft_patch_to_relations(audit=audit, patch=patch)

    assert audit.responses_json["meta"] == {"execution_mode": "survey"}
    assert audit.responses_json["pre_audit"] == {
        "season": "summer",
        "weather_conditions": ["windy"],
    }
    assert audit.responses_json["sections"]["section_a"]["note"] is None


def test_replace_audit_aggregate_preserves_revision_and_replaces_payload() -> None:
    """Whole-aggregate writes should preserve server-managed revision and replace the body."""

    audit = _build_audit()
    audit.responses_json = {
        "schema_version": 1,
        "revision": 4,
        "meta": {"execution_mode": "audit"},
        "pre_audit": {"season": "spring"},
        "sections": {
            "section_a": {
                "note": "Before",
                "responses": {"question_a": {"quantity": "some"}},
            }
        },
    }

    replace_audit_aggregate(
        audit=audit,
        aggregate=AuditAggregateWriteRequest(
            schema_version=1,
            meta={"execution_mode": "survey"},
            pre_audit={"season": "winter", "weather_conditions": ["windy"]},
            sections={
                "section_b": SectionDraftPatchRequest(
                    note="After",
                    responses={"question_b": {"quantity": "a_lot"}},
                )
            },
        ),
    )

    assert audit.responses_json == {
        "schema_version": 1,
        "revision": 4,
        "meta": {"execution_mode": "survey"},
        "pre_audit": {
            "season": "winter",
            "weather_conditions": ["windy"],
            "users_present": [],
            "user_count": None,
            "age_groups": [],
            "place_size": None,
        },
        "sections": {
            "section_b": {
                "note": "After",
                "responses": {"question_b": {"quantity": "a_lot"}},
            }
        },
    }


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
    answers_by_scale = {answer.scale_key: answer for answer in updated_question.scale_answers}

    assert updated_section is section
    assert updated_section.note == "After"
    assert updated_question is question
    assert answers_by_scale["quantity"] is quantity
    assert sorted(answers_by_scale) == ["diversity", "quantity"]

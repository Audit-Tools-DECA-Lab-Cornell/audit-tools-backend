"""Regression tests for Playspace instrument schema extensions."""

from __future__ import annotations

from copy import deepcopy

from app.products.playspace.instrument import get_canonical_instrument_payload
from app.products.playspace.schemas.instrument import PlayspaceInstrumentResponse


def test_instrument_schema_accepts_checklist_follow_up_questions() -> None:
    """The backend instrument schema should accept checklist-style follow-up questions."""

    payload = deepcopy(get_canonical_instrument_payload())
    first_section = payload["sections"][0]
    section_key = first_section["section_key"]

    first_section["questions"].append(
        {
            "question_key": "q_demo_checklist_follow_up",
            "mode": "audit",
            "constructs": [],
            "domains": ["Demo Domain"],
            "section_key": section_key,
            "prompt": "Check all loose parts that are present.",
            "question_type": "checklist",
            "required": False,
            "options": [
                {
                    "key": "cups",
                    "label": "Cups",
                    "description": None,
                },
                {
                    "key": "other",
                    "label": "Other, please describe",
                    "description": None,
                },
            ],
            "display_if": {
                "question_key": "q_1_1",
                "response_key": "quantity",
                "any_of_option_keys": ["a_little_bit", "a_lot"],
            },
        }
    )

    parsed = PlayspaceInstrumentResponse.model_validate(payload)
    checklist_question = parsed.sections[0].questions[-1]

    assert checklist_question.question_key == "q_demo_checklist_follow_up"
    assert checklist_question.question_type == "checklist"
    assert checklist_question.required is False
    assert [option.key for option in checklist_question.options] == ["cups", "other"]
    assert checklist_question.display_if is not None
    assert checklist_question.display_if.question_key == "q_1_1"

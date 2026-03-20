"""
Helpers for mapping Playspace audits between relational rows and runtime payloads.

The current scoring/progress engine still consumes the nested audit document
shape, so these helpers rebuild that shape from normalized tables while writes
flow into the relational tables first.
"""

from __future__ import annotations

from collections.abc import Iterable

from app.models import (
    Audit,
    JSONDict,
    PlayspaceAuditContext,
    PlayspaceAuditSection,
    PlayspacePreAuditAnswer,
    PlayspaceQuestionResponse,
    PlayspaceScaleAnswer,
)
from app.products.playspace.schemas.audit import AuditDraftPatchRequest

PRE_AUDIT_FIELD_ORDER = (
    "season",
    "weather_conditions",
    "users_present",
    "user_count",
    "age_groups",
    "place_size",
)
MULTI_SELECT_PRE_AUDIT_FIELDS = {
    "weather_conditions",
    "users_present",
    "age_groups",
}


def build_responses_json_from_relations(audit: Audit) -> JSONDict:
    """Rebuild the legacy nested audit payload from normalized Playspace rows."""

    return {
        "meta": _build_meta_payload(audit=audit),
        "pre_audit": _build_pre_audit_payload(audit=audit),
        "sections": _build_sections_payload(audit=audit),
    }


def get_execution_mode_value(audit: Audit) -> str | None:
    """Read the selected execution mode from normalized rows or cache fallback."""

    if audit.playspace_context is not None and audit.playspace_context.execution_mode is not None:
        return audit.playspace_context.execution_mode

    meta = _read_json_dict(_read_json_dict(audit.responses_json).get("meta"))
    raw_execution_mode = meta.get("execution_mode")
    if isinstance(raw_execution_mode, str) and raw_execution_mode.strip():
        return raw_execution_mode
    return None


def get_draft_progress_percent(audit: Audit) -> float | None:
    """Read the last stored draft progress percentage from normalized rows or cache fallback."""

    if (
        audit.playspace_context is not None
        and audit.playspace_context.draft_progress_percent is not None
    ):
        return float(audit.playspace_context.draft_progress_percent)

    raw_scores = _read_json_dict(audit.scores_json)
    raw_progress_percent = raw_scores.get("draft_progress_percent")
    if isinstance(raw_progress_percent, int | float):
        return float(raw_progress_percent)
    return None


def apply_draft_patch_to_relations(audit: Audit, patch: AuditDraftPatchRequest) -> None:
    """Apply one typed draft patch into normalized Playspace child rows."""

    if patch.meta is not None and patch.meta.execution_mode is not None:
        _get_or_create_context(audit=audit).execution_mode = patch.meta.execution_mode.value

    if patch.pre_audit is not None:
        current_pre_audit = _build_pre_audit_payload(audit=audit)
        next_pre_audit = {
            **current_pre_audit,
            "season": patch.pre_audit.season,
            "weather_conditions": list(patch.pre_audit.weather_conditions),
            "users_present": list(patch.pre_audit.users_present),
            "user_count": patch.pre_audit.user_count,
            "age_groups": list(patch.pre_audit.age_groups),
            "place_size": patch.pre_audit.place_size,
        }
        _replace_pre_audit_answers(audit=audit, payload=next_pre_audit)

    for section_key, section_patch in patch.sections.items():
        section = _get_or_create_section(audit=audit, section_key=section_key)
        if section_patch.note is not None:
            section.note = section_patch.note

        for question_key, scale_answers in section_patch.responses.items():
            question_response = _get_or_create_question_response(
                section=section,
                question_key=question_key,
            )
            _replace_scale_answers(
                question_response=question_response,
                scale_answers=scale_answers,
            )


def hydrate_relations_from_cached_json(audit: Audit) -> None:
    """Populate normalized Playspace child rows from the transitional JSONB cache."""

    execution_mode = get_execution_mode_value(audit)
    draft_progress_percent = get_draft_progress_percent(audit)
    if execution_mode is not None or draft_progress_percent is not None:
        context = _get_or_create_context(audit=audit)
        context.execution_mode = execution_mode
        context.draft_progress_percent = draft_progress_percent

    _replace_pre_audit_answers(audit=audit, payload=_read_pre_audit_payload_from_cache(audit=audit))
    _replace_sections_from_cache(audit=audit)


def set_execution_mode_value(audit: Audit, execution_mode: str | None) -> None:
    """Write the selected execution mode into normalized storage."""

    if execution_mode is None:
        context = audit.playspace_context
        if context is not None:
            context.execution_mode = None
        return

    _get_or_create_context(audit=audit).execution_mode = execution_mode


def set_draft_progress_percent(audit: Audit, draft_progress_percent: float | None) -> None:
    """Persist the current draft percentage into normalized storage for list views."""

    context = _get_or_create_context(audit=audit)
    context.draft_progress_percent = draft_progress_percent


def _build_meta_payload(audit: Audit) -> JSONDict:
    """Build the legacy `meta` payload used by the current scoring helpers."""

    execution_mode = get_execution_mode_value(audit)
    if execution_mode is None:
        return {}
    return {"execution_mode": execution_mode}


def _build_pre_audit_payload(audit: Audit) -> JSONDict:
    """Build the legacy `pre_audit` payload from normalized rows with cache fallback."""

    if audit.playspace_pre_audit_answers:
        payload: JSONDict = {}
        grouped_values: dict[str, list[tuple[int, str]]] = {}
        for answer in audit.playspace_pre_audit_answers:
            grouped_values.setdefault(answer.field_key, []).append(
                (answer.sort_order, answer.selected_value)
            )

        for field_key, values in grouped_values.items():
            ordered_values = [
                value for _sort_order, value in sorted(values, key=lambda item: item[0])
            ]
            if field_key in MULTI_SELECT_PRE_AUDIT_FIELDS:
                payload[field_key] = ordered_values
                continue
            payload[field_key] = ordered_values[0] if ordered_values else None
        return payload

    return _read_pre_audit_payload_from_cache(audit=audit)


def _build_sections_payload(audit: Audit) -> JSONDict:
    """Build the legacy `sections` payload from normalized rows with cache fallback."""

    if audit.playspace_sections:
        payload: JSONDict = {}
        ordered_sections = sorted(audit.playspace_sections, key=lambda section: section.section_key)
        for section in ordered_sections:
            section_payload: JSONDict = {"responses": {}}
            if section.note is not None:
                section_payload["note"] = section.note

            ordered_questions = sorted(
                section.question_responses,
                key=lambda question_response: question_response.question_key,
            )
            responses_payload: dict[str, dict[str, str]] = {}
            for question_response in ordered_questions:
                answers_payload: dict[str, str] = {}
                ordered_scale_answers = sorted(
                    question_response.scale_answers,
                    key=lambda scale_answer: scale_answer.scale_key,
                )
                for scale_answer in ordered_scale_answers:
                    answers_payload[scale_answer.scale_key] = scale_answer.option_key
                responses_payload[question_response.question_key] = answers_payload

            section_payload["responses"] = responses_payload
            payload[section.section_key] = section_payload
        return payload

    cached_responses = _read_json_dict(audit.responses_json)
    return _read_json_dict(cached_responses.get("sections"))


def _replace_pre_audit_answers(audit: Audit, payload: JSONDict) -> None:
    """Replace all normalized pre-audit rows from a normalized payload snapshot."""

    next_rows: list[PlayspacePreAuditAnswer] = []
    for field_key in PRE_AUDIT_FIELD_ORDER:
        raw_value = payload.get(field_key)
        if isinstance(raw_value, list):
            for sort_order, selected_value in enumerate(_string_values(raw_value)):
                next_rows.append(
                    PlayspacePreAuditAnswer(
                        field_key=field_key,
                        selected_value=selected_value,
                        sort_order=sort_order,
                    )
                )
            continue

        if isinstance(raw_value, str) and raw_value.strip():
            next_rows.append(
                PlayspacePreAuditAnswer(
                    field_key=field_key,
                    selected_value=raw_value,
                    sort_order=0,
                )
            )

    audit.playspace_pre_audit_answers = next_rows


def _replace_sections_from_cache(audit: Audit) -> None:
    """Replace normalized section rows from the current legacy cache payload."""

    cached_responses = _read_json_dict(audit.responses_json)
    sections_payload = _read_json_dict(cached_responses.get("sections"))
    next_sections: list[PlayspaceAuditSection] = []

    for section_key, raw_section_value in sections_payload.items():
        section_payload = _read_json_dict(raw_section_value)
        section = PlayspaceAuditSection(
            section_key=section_key,
            note=section_payload.get("note")
            if isinstance(section_payload.get("note"), str)
            else None,
        )

        responses_payload = _read_json_dict(section_payload.get("responses"))
        question_responses: list[PlayspaceQuestionResponse] = []
        for question_key, raw_question_value in responses_payload.items():
            question_response = PlayspaceQuestionResponse(question_key=question_key)
            _replace_scale_answers(
                question_response=question_response,
                scale_answers=_read_string_dict(raw_question_value),
            )
            question_responses.append(question_response)

        section.question_responses = question_responses
        next_sections.append(section)

    audit.playspace_sections = next_sections


def _replace_scale_answers(
    *,
    question_response: PlayspaceQuestionResponse,
    scale_answers: dict[str, str],
) -> None:
    """Replace one question's normalized scale-answer rows."""

    question_response.scale_answers = [
        PlayspaceScaleAnswer(scale_key=scale_key, option_key=option_key)
        for scale_key, option_key in sorted(scale_answers.items(), key=lambda item: item[0])
        if scale_key.strip() and option_key.strip()
    ]


def _get_or_create_context(audit: Audit) -> PlayspaceAuditContext:
    """Return the existing context row or create one in-memory."""

    if audit.playspace_context is None:
        audit.playspace_context = PlayspaceAuditContext()
    return audit.playspace_context


def _get_or_create_section(audit: Audit, section_key: str) -> PlayspaceAuditSection:
    """Return the matching section row, creating one when needed."""

    for section in audit.playspace_sections:
        if section.section_key == section_key:
            return section

    next_section = PlayspaceAuditSection(section_key=section_key)
    audit.playspace_sections.append(next_section)
    return next_section


def _get_or_create_question_response(
    *,
    section: PlayspaceAuditSection,
    question_key: str,
) -> PlayspaceQuestionResponse:
    """Return the matching question row, creating one when needed."""

    for question_response in section.question_responses:
        if question_response.question_key == question_key:
            return question_response

    next_question_response = PlayspaceQuestionResponse(question_key=question_key)
    section.question_responses.append(next_question_response)
    return next_question_response


def _read_pre_audit_payload_from_cache(audit: Audit) -> JSONDict:
    """Read the legacy pre-audit payload directly from the JSONB cache."""

    cached_responses = _read_json_dict(audit.responses_json)
    return _read_json_dict(cached_responses.get("pre_audit"))


def _read_json_dict(value: object) -> JSONDict:
    """Safely coerce unknown JSON-like values into plain dictionaries."""

    return dict(value) if isinstance(value, dict) else {}


def _read_string_dict(value: object) -> dict[str, str]:
    """Safely coerce unknown JSON-like values into a string-only dictionary."""

    if not isinstance(value, dict):
        return {}

    next_payload: dict[str, str] = {}
    for entry_key, entry_value in value.items():
        if isinstance(entry_value, str):
            next_payload[entry_key] = entry_value
    return next_payload


def _string_values(values: Iterable[object]) -> list[str]:
    """Filter one iterable down to its string members only."""

    return [value for value in values if isinstance(value, str) and value.strip()]

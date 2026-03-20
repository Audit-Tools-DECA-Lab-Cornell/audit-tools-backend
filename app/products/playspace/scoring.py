"""
Playspace audit runtime helpers for execution-mode filtering, progress, and scoring.

The scoring model uses raw totals rather than normalized percentages:
quantity is summed directly, diversity and challenge contribute both domain
column totals and construct multipliers, and sociability is tracked as a
separate score stream alongside play value and usability.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Audit
from app.products.playspace.schemas import (
    AssignmentRole,
    AuditDraftPatchRequest,
    AuditProgressResponse,
    AuditSectionProgressResponse,
    ExecutionMode,
    JsonDict,
    PreAuditPatchRequest,
)
from app.products.playspace.scoring_metadata import (
    SCORING_SECTIONS,
    ScoringQuestion,
    ScoringScaleOption,
    ScoringSection,
)

PRE_AUDIT_REQUIRED_KEYS = [
    "season",
    "weather_conditions",
    "users_present",
    "user_count",
    "age_groups",
    "place_size",
]
MULTI_SELECT_PRE_AUDIT_FIELDS = {
    "weather_conditions",
    "users_present",
    "age_groups",
}


@dataclass(frozen=True)
class ScoreTotals:
    """Internal aggregate for one section, domain, or overall audit score bucket."""

    quantity_total: float = 0.0
    diversity_total: float = 0.0
    challenge_total: float = 0.0
    sociability_total: float = 0.0
    play_value_total: float = 0.0
    usability_total: float = 0.0


@dataclass(frozen=True)
class AuditStateSnapshot:
    """Minimal in-memory scoring state independent from storage format."""

    execution_mode_value: str | None
    pre_audit_payload: JsonDict
    sections_payload: dict[str, JsonDict]


def get_allowed_execution_modes(assignment_roles: list[AssignmentRole]) -> list[ExecutionMode]:
    """Map place-scoped assignment capabilities to visible execution modes."""

    role_set = set(assignment_roles)
    has_auditor = AssignmentRole.AUDITOR in role_set
    has_place_admin = AssignmentRole.PLACE_ADMIN in role_set

    if has_auditor and has_place_admin:
        return [ExecutionMode.AUDIT, ExecutionMode.SURVEY, ExecutionMode.BOTH]
    if has_place_admin:
        return [ExecutionMode.SURVEY]
    return [ExecutionMode.AUDIT]


def resolve_execution_mode(
    *,
    assignment_roles: list[AssignmentRole],
    responses_json: JsonDict,
) -> ExecutionMode | None:
    """Resolve the effective execution mode from saved metadata and assignment defaults."""

    snapshot = _build_snapshot_from_json(responses_json)
    return _resolve_execution_mode_from_value(
        assignment_roles=assignment_roles,
        execution_mode_value=snapshot.execution_mode_value,
    )


def resolve_execution_mode_for_audit(
    *,
    assignment_roles: list[AssignmentRole],
    audit: Audit,
) -> ExecutionMode | None:
    """Resolve execution mode directly from normalized audit relations."""

    snapshot = _build_snapshot_from_audit(audit)
    return _resolve_execution_mode_from_value(
        assignment_roles=assignment_roles,
        execution_mode_value=snapshot.execution_mode_value,
    )


def _resolve_execution_mode_from_value(
    *,
    assignment_roles: list[AssignmentRole],
    execution_mode_value: str | None,
) -> ExecutionMode | None:
    """Resolve execution mode from one stored string value and assignment rules."""

    allowed_modes = get_allowed_execution_modes(assignment_roles)
    if isinstance(execution_mode_value, str):
        try:
            parsed_mode = ExecutionMode(execution_mode_value)
        except ValueError:
            parsed_mode = None
        if parsed_mode is not None and parsed_mode in allowed_modes:
            return parsed_mode
    if len(allowed_modes) == 1:
        return allowed_modes[0]
    return None


def merge_draft_patch(
    *,
    current_responses_json: JsonDict,
    patch: AuditDraftPatchRequest,
) -> JsonDict:
    """Merge a typed draft patch into the stored JSON structure."""

    next_payload = {
        "meta": _read_json_dict(current_responses_json.get("meta")),
        "pre_audit": _read_json_dict(current_responses_json.get("pre_audit")),
        "sections": _read_json_dict(current_responses_json.get("sections")),
    }

    if patch.meta is not None:
        next_payload["meta"].update(patch.meta.model_dump(exclude_none=True))

    if patch.pre_audit is not None:
        next_payload["pre_audit"].update(_serialize_pre_audit_patch(patch.pre_audit))

    if patch.sections:
        for section_key, section_patch in patch.sections.items():
            existing_section = _read_json_dict(next_payload["sections"].get(section_key))
            existing_responses = _read_json_dict(existing_section.get("responses"))
            for question_key, scale_answers in section_patch.responses.items():
                existing_responses[question_key] = dict(scale_answers.items())
            existing_section["responses"] = existing_responses
            if section_patch.note is not None:
                existing_section["note"] = section_patch.note
            next_payload["sections"][section_key] = existing_section

    return next_payload


def build_audit_progress(
    *,
    assignment_roles: list[AssignmentRole],
    responses_json: JsonDict,
) -> AuditProgressResponse:
    """Build user-facing progress for the current draft state."""

    snapshot = _build_snapshot_from_json(responses_json)
    return _build_audit_progress_from_snapshot(
        assignment_roles=assignment_roles,
        snapshot=snapshot,
    )


def build_audit_progress_for_audit(
    *,
    assignment_roles: list[AssignmentRole],
    audit: Audit,
) -> AuditProgressResponse:
    """Build user-facing progress directly from normalized audit relations."""

    snapshot = _build_snapshot_from_audit(audit)
    return _build_audit_progress_from_snapshot(
        assignment_roles=assignment_roles,
        snapshot=snapshot,
    )


def _build_audit_progress_from_snapshot(
    *,
    assignment_roles: list[AssignmentRole],
    snapshot: AuditStateSnapshot,
) -> AuditProgressResponse:
    """Build user-facing progress from one storage-agnostic audit snapshot."""

    execution_mode = _resolve_execution_mode_from_value(
        assignment_roles=assignment_roles,
        execution_mode_value=snapshot.execution_mode_value,
    )
    pre_audit_payload = snapshot.pre_audit_payload
    sections_payload = snapshot.sections_payload

    required_pre_audit_complete = _is_pre_audit_complete(pre_audit_payload)
    section_progress: list[AuditSectionProgressResponse] = []
    visible_section_count = 0
    completed_section_count = 0
    total_visible_questions = 0
    answered_visible_questions = 0

    for section in SCORING_SECTIONS:
        visible_questions = _get_visible_questions(
            section=section,
            execution_mode=execution_mode,
        )
        if not visible_questions:
            continue

        visible_section_count += 1
        section_answers = _read_json_dict(sections_payload.get(section.section_key))
        answered_count = 0
        for question in visible_questions:
            if _is_question_complete(question=question, section_answers=section_answers):
                answered_count += 1

        total_visible_questions += len(visible_questions)
        answered_visible_questions += answered_count
        is_complete = answered_count == len(visible_questions)
        if is_complete:
            completed_section_count += 1

        section_progress.append(
            AuditSectionProgressResponse(
                section_key=section.section_key,
                title=section.section_key,
                visible_question_count=len(visible_questions),
                answered_question_count=answered_count,
                is_complete=is_complete,
            )
        )

    ready_to_submit = (
        execution_mode is not None
        and required_pre_audit_complete
        and visible_section_count > 0
        and completed_section_count == visible_section_count
    )

    return AuditProgressResponse(
        required_pre_audit_complete=required_pre_audit_complete,
        visible_section_count=visible_section_count,
        completed_section_count=completed_section_count,
        total_visible_questions=total_visible_questions,
        answered_visible_questions=answered_visible_questions,
        ready_to_submit=ready_to_submit,
        sections=section_progress,
    )


def score_audit(
    *,
    assignment_roles: list[AssignmentRole],
    responses_json: JsonDict,
) -> JsonDict:
    """Calculate Playspace total buckets for a completed audit draft."""

    snapshot = _build_snapshot_from_json(responses_json)
    return _score_audit_from_snapshot(
        assignment_roles=assignment_roles,
        snapshot=snapshot,
    )


def score_audit_for_audit(
    *,
    assignment_roles: list[AssignmentRole],
    audit: Audit,
) -> JsonDict:
    """Calculate Playspace total buckets directly from normalized audit relations."""

    snapshot = _build_snapshot_from_audit(audit)
    return _score_audit_from_snapshot(
        assignment_roles=assignment_roles,
        snapshot=snapshot,
    )


def _score_audit_from_snapshot(
    *,
    assignment_roles: list[AssignmentRole],
    snapshot: AuditStateSnapshot,
) -> JsonDict:
    """Calculate scores from one storage-agnostic audit snapshot."""

    execution_mode = _resolve_execution_mode_from_value(
        assignment_roles=assignment_roles,
        execution_mode_value=snapshot.execution_mode_value,
    )
    if execution_mode is None:
        raise ValueError("Execution mode must be selected before scoring the audit.")

    section_scores: dict[str, JsonDict] = {}
    domain_scores: dict[str, ScoreTotals] = {}
    sections_payload = snapshot.sections_payload

    for section in SCORING_SECTIONS:
        visible_questions = _get_visible_questions(
            section=section,
            execution_mode=execution_mode,
        )
        if not visible_questions:
            continue

        section_answers = _read_json_dict(sections_payload.get(section.section_key))
        section_totals = ScoreTotals()

        for question in visible_questions:
            question_totals = _score_question(
                question=question,
                section_answers=section_answers,
            )
            section_totals = _add_score_totals(section_totals, question_totals)

            for domain_label in question.domains:
                current_domain_score = domain_scores.get(domain_label, ScoreTotals())
                domain_scores[domain_label] = _add_score_totals(
                    current_domain_score,
                    question_totals,
                )

        section_scores[section.section_key] = _serialize_score_totals(section_totals)

    serialized_domain_scores = {
        domain_key: _serialize_score_totals(score_totals)
        for domain_key, score_totals in domain_scores.items()
    }
    overall_totals = ScoreTotals()
    for domain_totals in domain_scores.values():
        overall_totals = _add_score_totals(overall_totals, domain_totals)

    return {
        "overall": _serialize_score_totals(overall_totals),
        "by_section": section_scores,
        "by_domain": serialized_domain_scores,
        "execution_mode": execution_mode.value,
    }


######################################################################################
################################## Internal Helpers ##################################
######################################################################################


def _build_snapshot_from_json(responses_json: JsonDict) -> AuditStateSnapshot:
    """Build a scoring snapshot from the legacy nested audit document."""

    meta = _read_json_dict(responses_json.get("meta"))
    raw_sections = _read_json_dict(responses_json.get("sections"))
    section_payloads = {
        section_key: _read_json_dict(_read_json_dict(section_value).get("responses"))
        for section_key, section_value in raw_sections.items()
    }
    execution_mode_value = meta.get("execution_mode")
    return AuditStateSnapshot(
        execution_mode_value=(
            execution_mode_value if isinstance(execution_mode_value, str) else None
        ),
        pre_audit_payload=_read_json_dict(responses_json.get("pre_audit")),
        sections_payload=section_payloads,
    )


def _build_snapshot_from_audit(audit: Audit) -> AuditStateSnapshot:
    """Build a scoring snapshot directly from normalized Playspace audit relations."""

    return AuditStateSnapshot(
        execution_mode_value=_read_execution_mode_value_from_audit(audit),
        pre_audit_payload=_build_pre_audit_payload_from_audit(audit),
        sections_payload=_build_sections_payload_from_audit(audit),
    )


def _get_visible_questions(
    *,
    section: ScoringSection,
    execution_mode: ExecutionMode | None,
) -> list[ScoringQuestion]:
    """Filter section questions down to the active execution mode."""

    if execution_mode is None:
        return []

    mode_value = execution_mode.value
    return [
        question
        for question in section.questions
        if question.mode == "both" or question.mode == mode_value
    ]


def _is_question_complete(
    *,
    question: ScoringQuestion,
    section_answers: JsonDict,
) -> bool:
    """Determine whether a question has all answers required by quantity gating."""

    question_answers = _read_json_dict(section_answers.get(question.question_key))
    quantity_scale = next(
        (scale for scale in question.scales if scale.key == "quantity"),
        None,
    )
    if quantity_scale is None:
        return False

    raw_quantity_answer = question_answers.get("quantity")
    if not isinstance(raw_quantity_answer, str):
        return False

    quantity_option = _find_option_by_key(quantity_scale.options, raw_quantity_answer)
    if quantity_option is None:
        return False

    if not quantity_option.allows_follow_up_scales:
        return True

    for scale in question.scales:
        if scale.key == "quantity":
            continue
        raw_answer = question_answers.get(scale.key)
        if not isinstance(raw_answer, str):
            return False
        if _find_option_by_key(scale.options, raw_answer) is None:
            return False

    return True


def _serialize_pre_audit_patch(pre_audit: PreAuditPatchRequest) -> JsonDict:
    """Serialize the pre-audit patch with JSON-safe primitives only."""

    return {
        "season": pre_audit.season,
        "weather_conditions": list(pre_audit.weather_conditions),
        "users_present": list(pre_audit.users_present),
        "user_count": pre_audit.user_count,
        "age_groups": list(pre_audit.age_groups),
        "place_size": pre_audit.place_size,
    }


def _read_json_dict(value: object) -> JsonDict:
    """Safely coerce arbitrary JSON-like values to dictionaries."""

    return dict(value) if isinstance(value, dict) else {}


def _read_execution_mode_value_from_audit(audit: Audit) -> str | None:
    """Read the selected execution mode directly from normalized rows or cache fallback."""

    if audit.playspace_context is not None and audit.playspace_context.execution_mode is not None:
        return audit.playspace_context.execution_mode

    meta = _read_json_dict(_read_json_dict(audit.responses_json).get("meta"))
    raw_execution_mode = meta.get("execution_mode")
    if isinstance(raw_execution_mode, str) and raw_execution_mode.strip():
        return raw_execution_mode
    return None


def _build_pre_audit_payload_from_audit(audit: Audit) -> JsonDict:
    """Build pre-audit values from normalized rows or cache fallback."""

    if audit.playspace_pre_audit_answers:
        grouped_values: dict[str, list[tuple[int, str]]] = {}
        for answer in audit.playspace_pre_audit_answers:
            grouped_values.setdefault(answer.field_key, []).append(
                (answer.sort_order, answer.selected_value)
            )

        payload: JsonDict = {}
        for field_key, ordered_pairs in grouped_values.items():
            ordered_values = [
                value for _sort_order, value in sorted(ordered_pairs, key=lambda item: item[0])
            ]
            if field_key in MULTI_SELECT_PRE_AUDIT_FIELDS:
                payload[field_key] = ordered_values
                continue
            payload[field_key] = ordered_values[0] if ordered_values else None
        return payload

    return _read_json_dict(_read_json_dict(audit.responses_json).get("pre_audit"))


def _build_sections_payload_from_audit(audit: Audit) -> dict[str, JsonDict]:
    """Build section answer lookups from normalized rows or cache fallback."""

    if audit.playspace_sections:
        section_payloads: dict[str, JsonDict] = {}
        ordered_sections = sorted(audit.playspace_sections, key=lambda section: section.section_key)
        for section in ordered_sections:
            question_payloads: JsonDict = {}
            ordered_questions = sorted(
                section.question_responses,
                key=lambda question_response: question_response.question_key,
            )
            for question_response in ordered_questions:
                scale_answers: JsonDict = {}
                for scale_answer in sorted(
                    question_response.scale_answers,
                    key=lambda answer: answer.scale_key,
                ):
                    scale_answers[scale_answer.scale_key] = scale_answer.option_key
                question_payloads[question_response.question_key] = scale_answers
            section_payloads[section.section_key] = question_payloads
        return section_payloads

    cached_sections = _read_json_dict(_read_json_dict(audit.responses_json).get("sections"))
    return {
        section_key: _read_json_dict(_read_json_dict(section_value).get("responses"))
        for section_key, section_value in cached_sections.items()
    }


def _is_pre_audit_complete(pre_audit_payload: JsonDict) -> bool:
    """Validate that all manual pre-audit prompts are filled."""

    for field_name in PRE_AUDIT_REQUIRED_KEYS:
        value = pre_audit_payload.get(field_name)
        if isinstance(value, list):
            if len(value) == 0:
                return False
            continue
        if isinstance(value, str):
            if not value.strip():
                return False
            continue
        return False
    return True


def _find_option_by_key(
    options: list[ScoringScaleOption],
    option_key: str,
) -> ScoringScaleOption | None:
    """Look up a scoring option by its stable key."""

    for option in options:
        if option.key == option_key:
            return option
    return None


def _score_question(
    *,
    question: ScoringQuestion,
    section_answers: JsonDict,
) -> ScoreTotals:
    """Score one question according to the client-approved Playspace rules."""

    question_answers = _read_json_dict(section_answers.get(question.question_key))
    quantity_scale = next(scale for scale in question.scales if scale.key == "quantity")
    quantity_answer_key = question_answers.get("quantity")
    if not isinstance(quantity_answer_key, str):
        return ScoreTotals()

    quantity_option = _find_option_by_key(quantity_scale.options, quantity_answer_key)
    if quantity_option is None:
        return ScoreTotals()

    quantity_total = float(quantity_option.addition_value)
    diversity_total = 0.0
    challenge_total = 0.0
    sociability_total = 0.0
    diversity_multiplier = 1.0
    challenge_multiplier = 1.0

    if quantity_option.allows_follow_up_scales:
        diversity_total, diversity_multiplier = _read_multiplier_scale_score(
            question=question,
            question_answers=question_answers,
            scale_key="diversity",
        )
        challenge_total, challenge_multiplier = _read_multiplier_scale_score(
            question=question,
            question_answers=question_answers,
            scale_key="challenge",
        )
        sociability_total = _read_sociability_scale_score(
            question=question,
            question_answers=question_answers,
        )

    construct_score = quantity_total * diversity_multiplier * challenge_multiplier
    play_value_total = construct_score if "play_value" in question.constructs else 0.0
    usability_total = construct_score if "usability" in question.constructs else 0.0

    return ScoreTotals(
        quantity_total=round(quantity_total, 2),
        diversity_total=round(diversity_total, 2),
        challenge_total=round(challenge_total, 2),
        sociability_total=round(sociability_total, 2),
        play_value_total=round(play_value_total, 2),
        usability_total=round(usability_total, 2),
    )


def _read_multiplier_scale_score(
    *,
    question: ScoringQuestion,
    question_answers: JsonDict,
    scale_key: str,
) -> tuple[float, float]:
    """Read one diversity/challenge answer as both a domain total and multiplier."""

    scale = next(
        (current_scale for current_scale in question.scales if current_scale.key == scale_key),
        None,
    )
    if scale is None:
        return 0.0, 1.0

    answer_key = question_answers.get(scale_key)
    if not isinstance(answer_key, str):
        return 0.0, 1.0

    selected_option = _find_option_by_key(scale.options, answer_key)
    if selected_option is None:
        return 0.0, 1.0

    column_total = max(float(selected_option.addition_value) - 1.0, 0.0)
    if selected_option.addition_value <= 0:
        return column_total, 1.0
    return column_total, float(selected_option.boost_value)


def _read_sociability_scale_score(
    *,
    question: ScoringQuestion,
    question_answers: JsonDict,
) -> float:
    """Read one sociability answer using the client-specified 0/1/2 mapping."""

    scale = next(
        (
            current_scale
            for current_scale in question.scales
            if current_scale.key == "sociability"
        ),
        None,
    )
    if scale is None:
        return 0.0

    answer_key = question_answers.get("sociability")
    if not isinstance(answer_key, str):
        return 0.0

    selected_option = _find_option_by_key(scale.options, answer_key)
    if selected_option is None:
        return 0.0

    return max(float(selected_option.addition_value) - 1.0, 0.0)


def _add_score_totals(left: ScoreTotals, right: ScoreTotals) -> ScoreTotals:
    """Sum two immutable Playspace score-total buckets."""

    return ScoreTotals(
        quantity_total=left.quantity_total + right.quantity_total,
        diversity_total=left.diversity_total + right.diversity_total,
        challenge_total=left.challenge_total + right.challenge_total,
        sociability_total=left.sociability_total + right.sociability_total,
        play_value_total=left.play_value_total + right.play_value_total,
        usability_total=left.usability_total + right.usability_total,
    )


def _serialize_score_totals(score_totals: ScoreTotals) -> JsonDict:
    """Convert one score-total bucket into a JSON-safe response payload."""

    return {
        "quantity_total": round(score_totals.quantity_total, 2),
        "diversity_total": round(score_totals.diversity_total, 2),
        "challenge_total": round(score_totals.challenge_total, 2),
        "sociability_total": round(score_totals.sociability_total, 2),
        "play_value_total": round(score_totals.play_value_total, 2),
        "usability_total": round(score_totals.usability_total, 2),
    }

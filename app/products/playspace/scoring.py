"""
Playspace audit runtime helpers for execution-mode filtering, progress, and scoring.

The scoring model uses raw totals rather than normalized percentages:
provision is summed directly, diversity and challenge contribute both domain
column totals and construct multipliers, and sociability is tracked as a
separate score stream alongside play value and usability.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models import Audit
from app.products.playspace.audit_state import build_responses_json_from_relations
from app.products.playspace.instrument import get_canonical_instrument_response
from app.products.playspace.schemas import (
    AuditDraftPatchRequest,
    AuditProgressResponse,
    AuditSectionProgressResponse,
    ExecutionMode,
    JsonDict,
    PreAuditPatchRequest,
)
from app.products.playspace.schemas.instrument import PreAuditInputType
from app.products.playspace.scoring_metadata import (
    ScoringQuestion,
    ScoringScaleOption,
    ScoringSection,
    get_scoring_sections,
)

MULTI_SELECT_PRE_AUDIT_FIELDS = {
    "weather_conditions",
}
ALL_EXECUTION_MODES = [
    ExecutionMode.AUDIT,
    ExecutionMode.SURVEY,
    ExecutionMode.BOTH,
]


@dataclass(frozen=True)
class ScoreTotals:
    """Internal aggregate for one section, domain, or overall audit score bucket."""

    provision_total: float = 0.0
    provision_total_max: float = 0.0
    diversity_total: float = 0.0
    diversity_total_max: float = 0.0
    challenge_total: float = 0.0
    challenge_total_max: float = 0.0
    sociability_total: float = 0.0
    sociability_total_max: float = 0.0
    play_value_total: float = 0.0
    play_value_total_max: float = 0.0
    usability_total: float = 0.0
    usability_total_max: float = 0.0


@dataclass(frozen=True)
class AuditStateSnapshot:
    """Minimal in-memory scoring state independent from storage format."""

    execution_mode_value: str | None
    pre_audit_payload: JsonDict
    sections_payload: dict[str, JsonDict]


def get_allowed_execution_modes() -> list[ExecutionMode]:
    """Return the auditor-selectable execution modes."""

    return list(ALL_EXECUTION_MODES)


def resolve_execution_mode(
    *,
    responses_json: JsonDict,
) -> ExecutionMode | None:
    """Resolve the effective execution mode from saved metadata."""

    snapshot = _build_snapshot_from_json(responses_json)
    return _resolve_execution_mode_from_value(execution_mode_value=snapshot.execution_mode_value)


def resolve_execution_mode_for_audit(
    *,
    audit: Audit,
) -> ExecutionMode | None:
    """Resolve execution mode directly from normalized audit relations."""

    snapshot = _build_snapshot_from_audit(audit)
    return _resolve_execution_mode_from_value(execution_mode_value=snapshot.execution_mode_value)


def _resolve_execution_mode_from_value(
    *,
    execution_mode_value: str | None,
) -> ExecutionMode | None:
    """Resolve execution mode from one stored string value."""

    if isinstance(execution_mode_value, str):
        try:
            parsed_mode = ExecutionMode(execution_mode_value)
        except ValueError:
            parsed_mode = None
        if parsed_mode is not None:
            return parsed_mode
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
    responses_json: JsonDict,
) -> AuditProgressResponse:
    """Build user-facing progress for the current draft state."""

    snapshot = _build_snapshot_from_json(responses_json)
    return _build_audit_progress_from_snapshot(snapshot=snapshot)


def build_audit_progress_for_audit(
    *,
    audit: Audit,
) -> AuditProgressResponse:
    """Build user-facing progress directly from normalized audit relations."""

    snapshot = _build_snapshot_from_audit(audit)
    return _build_audit_progress_from_snapshot(snapshot=snapshot)


def _build_audit_progress_from_snapshot(
    *,
    snapshot: AuditStateSnapshot,
) -> AuditProgressResponse:
    """Build user-facing progress from one storage-agnostic audit snapshot."""

    execution_mode = _resolve_execution_mode_from_value(
        execution_mode_value=snapshot.execution_mode_value
    )
    pre_audit_payload = snapshot.pre_audit_payload
    sections_payload = snapshot.sections_payload

    required_pre_audit_complete = _is_pre_audit_complete(pre_audit_payload, execution_mode)
    section_progress: list[AuditSectionProgressResponse] = []
    visible_section_count = 0
    completed_section_count = 0
    total_visible_questions = 0
    answered_visible_questions = 0

    for section in get_scoring_sections():
        section_answers = _read_json_dict(sections_payload.get(section.section_key))
        visible_questions = _get_visible_questions(
            section=section,
            execution_mode=execution_mode,
            section_answers=section_answers,
        )
        if not visible_questions:
            continue

        visible_section_count += 1
        answered_count = 0
        for question in visible_questions:
            if not _question_counts_toward_completion(question):
                continue
            if _is_question_complete(question=question, section_answers=section_answers):
                answered_count += 1

        total_visible_questions += sum(
            1 for question in visible_questions if _question_counts_toward_completion(question)
        )
        answered_visible_questions += answered_count
        required_visible_question_count = sum(
            1 for question in visible_questions if _question_counts_toward_completion(question)
        )
        is_complete = answered_count == required_visible_question_count
        if is_complete:
            completed_section_count += 1

        section_progress.append(
            AuditSectionProgressResponse(
                section_key=section.section_key,
                title=section.section_key,
                visible_question_count=required_visible_question_count,
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
    responses_json: JsonDict,
    include_maximums: bool = False,
) -> JsonDict:
    """Calculate Playspace total buckets for a completed audit draft."""

    snapshot = _build_snapshot_from_json(responses_json)
    return _score_audit_from_snapshot(snapshot=snapshot, include_maximums=include_maximums)


def score_audit_for_audit(
    *,
    audit: Audit,
    include_maximums: bool = False,
) -> JsonDict:
    """Calculate Playspace total buckets directly from normalized audit relations."""

    snapshot = _build_snapshot_from_audit(audit)
    return _score_audit_from_snapshot(snapshot=snapshot, include_maximums=include_maximums)


def _score_audit_from_snapshot(
    *,
    snapshot: AuditStateSnapshot,
    include_maximums: bool,
) -> JsonDict:
    """Calculate scores from one storage-agnostic audit snapshot."""

    execution_mode = _resolve_execution_mode_from_value(
        execution_mode_value=snapshot.execution_mode_value
    )
    if execution_mode is None:
        raise ValueError("Execution mode must be selected before scoring the audit.")

    section_scores: dict[str, JsonDict] = {}
    domain_scores: dict[str, ScoreTotals] = {}
    sections_payload = snapshot.sections_payload

    for section in get_scoring_sections():
        section_answers = _read_json_dict(sections_payload.get(section.section_key))
        visible_questions = _get_visible_questions(
            section=section,
            execution_mode=execution_mode,
            section_answers=section_answers,
        )
        if not visible_questions:
            continue

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

        section_scores[section.section_key] = _serialize_score_totals(
            section_totals,
            include_maximums=include_maximums,
        )

    serialized_domain_scores = {
        domain_key: _serialize_score_totals(
            score_totals,
            include_maximums=include_maximums,
        )
        for domain_key, score_totals in domain_scores.items()
    }
    overall_totals = ScoreTotals()
    for domain_totals in domain_scores.values():
        overall_totals = _add_score_totals(overall_totals, domain_totals)

    return {
        "overall": _serialize_score_totals(overall_totals, include_maximums=include_maximums),
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
    """Build a scoring snapshot from the canonical aggregate with legacy fallback."""

    return _build_snapshot_from_json(build_responses_json_from_relations(audit))


def _get_visible_questions(
    *,
    section: ScoringSection,
    execution_mode: ExecutionMode | None,
    section_answers: JsonDict,
) -> list[ScoringQuestion]:
    """Filter section questions down to the active execution mode and display rules."""

    if execution_mode is None:
        return []

    mode_value = execution_mode.value
    visible_questions: list[ScoringQuestion] = []
    for question in section.questions:
        if mode_value != "both" and question.mode not in {mode_value, "both"}:
            continue
        if not _is_question_visible(question=question, section_answers=section_answers):
            continue
        visible_questions.append(question)
    return visible_questions


def _is_question_visible(*, question: ScoringQuestion, section_answers: JsonDict) -> bool:
    """Evaluate simple intra-section display logic for one question."""

    if question.display_if is None:
        return True

    parent_answers = _read_json_dict(section_answers.get(question.display_if.question_key))
    selected_value = parent_answers.get(question.display_if.response_key)
    if isinstance(selected_value, str):
        return selected_value in question.display_if.any_of_option_keys
    if isinstance(selected_value, list):
        return any(
            isinstance(entry, str) and entry in question.display_if.any_of_option_keys
            for entry in selected_value
        )
    return False


def _question_counts_toward_completion(question: ScoringQuestion) -> bool:
    """Return whether a visible question should block section completion."""

    return question.required


def _is_question_complete(
    *,
    question: ScoringQuestion,
    section_answers: JsonDict,
) -> bool:
    """Determine whether a question has all answers required under its question type."""

    question_answers = _read_json_dict(section_answers.get(question.question_key))
    if question.question_type == "checklist":
        selected_option_keys = question_answers.get("selected_option_keys")
        return isinstance(selected_option_keys, list) and any(
            isinstance(option_key, str) and option_key.strip()
            for option_key in selected_option_keys
        )

    provision_scale = next(
        (scale for scale in question.scales if scale.key == "provision"),
        None,
    )
    if provision_scale is None:
        return False

    raw_provision_answer = question_answers.get("provision")
    if not isinstance(raw_provision_answer, str):
        return False

    provision_option = _find_option_by_key(provision_scale.options, raw_provision_answer)
    if provision_option is None:
        return False

    if not provision_option.allows_follow_up_scales:
        return True

    for scale in question.scales:
        if scale.key == "provision":
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
        "place_size": pre_audit.place_size,
        "current_users_0_5": pre_audit.current_users_0_5,
        "current_users_6_12": pre_audit.current_users_6_12,
        "current_users_13_17": pre_audit.current_users_13_17,
        "current_users_18_plus": pre_audit.current_users_18_plus,
        "playspace_busyness": pre_audit.playspace_busyness,
        "season": pre_audit.season,
        "weather_conditions": list(pre_audit.weather_conditions),
        "wind_conditions": pre_audit.wind_conditions,
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


def _is_pre_audit_complete(
    pre_audit_payload: JsonDict,
    execution_mode: ExecutionMode | None,
) -> bool:
    """Validate that all manual pre-audit prompts are filled."""

    if execution_mode is None:
        return False

    instrument = get_canonical_instrument_response()
    for question in instrument.pre_audit_questions:
        if not question.required or execution_mode not in question.visible_modes:
            continue

        if question.input_type == PreAuditInputType.AUTO_TIMESTAMP:
            continue

        value = pre_audit_payload.get(question.key)
        if question.input_type == PreAuditInputType.MULTI_SELECT:
            if not isinstance(value, list) or len(value) == 0:
                return False
            continue

        if not isinstance(value, str) or not value.strip():
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

    if question.question_type != "scaled" or len(question.scales) == 0:
        return ScoreTotals()

    question_answers = _read_json_dict(section_answers.get(question.question_key))
    provision_scale = next(scale for scale in question.scales if scale.key == "provision")
    provision_answer_key = question_answers.get("provision")
    if not isinstance(provision_answer_key, str):
        return ScoreTotals()

    provision_option = _find_option_by_key(provision_scale.options, provision_answer_key)
    if provision_option is None:
        return ScoreTotals()

    provision_total = float(provision_option.addition_value)
    provision_total_max = _read_provision_scale_maximum(question=question)
    diversity_total = 0.0
    diversity_total_max, diversity_multiplier_max = _read_multiplier_scale_maximum(
        question=question,
        scale_key="diversity",
    )
    challenge_total = 0.0
    challenge_total_max, challenge_multiplier_max = _read_multiplier_scale_maximum(
        question=question,
        scale_key="challenge",
    )
    sociability_total = 0.0
    sociability_total_max = _read_sociability_scale_maximum(question=question)
    diversity_multiplier = 1.0
    challenge_multiplier = 1.0

    if provision_option.allows_follow_up_scales:
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

    construct_score = provision_total * diversity_multiplier * challenge_multiplier
    construct_score_max = provision_total_max * diversity_multiplier_max * challenge_multiplier_max
    play_value_total = construct_score if "play_value" in question.constructs else 0.0
    play_value_total_max = construct_score_max if "play_value" in question.constructs else 0.0
    usability_total = construct_score if "usability" in question.constructs else 0.0
    usability_total_max = construct_score_max if "usability" in question.constructs else 0.0

    return ScoreTotals(
        provision_total=round(provision_total, 2),
        provision_total_max=round(provision_total_max, 2),
        diversity_total=round(diversity_total, 2),
        diversity_total_max=round(diversity_total_max, 2),
        challenge_total=round(challenge_total, 2),
        challenge_total_max=round(challenge_total_max, 2),
        sociability_total=round(sociability_total, 2),
        sociability_total_max=round(sociability_total_max, 2),
        play_value_total=round(play_value_total, 2),
        play_value_total_max=round(play_value_total_max, 2),
        usability_total=round(usability_total, 2),
        usability_total_max=round(usability_total_max, 2),
    )


def _read_provision_scale_maximum(*, question: ScoringQuestion) -> float:
    """Return the highest provision score available for one question."""

    provision_scale = next(
        (current_scale for current_scale in question.scales if current_scale.key == "provision"),
        None,
    )
    if provision_scale is None:
        return 0.0
    return max(
        (float(option.addition_value) for option in provision_scale.options),
        default=0.0,
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


def _read_multiplier_scale_maximum(
    *,
    question: ScoringQuestion,
    scale_key: str,
) -> tuple[float, float]:
    """Return the highest available column score and construct multiplier for one scale."""

    scale = next(
        (current_scale for current_scale in question.scales if current_scale.key == scale_key),
        None,
    )
    if scale is None:
        return 0.0, 1.0

    max_column_total = max(
        (max(float(option.addition_value) - 1.0, 0.0) for option in scale.options),
        default=0.0,
    )
    max_multiplier = max((float(option.boost_value) for option in scale.options), default=1.0)
    return max_column_total, max(max_multiplier, 1.0)


def _read_sociability_scale_score(
    *,
    question: ScoringQuestion,
    question_answers: JsonDict,
) -> float:
    """Read one sociability answer using the client-specified 0/1/2 mapping."""

    scale = next(
        (current_scale for current_scale in question.scales if current_scale.key == "sociability"),
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


def _read_sociability_scale_maximum(*, question: ScoringQuestion) -> float:
    """Return the highest available sociability column score for one question."""

    scale = next(
        (current_scale for current_scale in question.scales if current_scale.key == "sociability"),
        None,
    )
    if scale is None:
        return 0.0
    return max(
        (max(float(option.addition_value) - 1.0, 0.0) for option in scale.options),
        default=0.0,
    )


def _add_score_totals(left: ScoreTotals, right: ScoreTotals) -> ScoreTotals:
    """Sum two immutable Playspace score-total buckets."""

    return ScoreTotals(
        provision_total=left.provision_total + right.provision_total,
        provision_total_max=left.provision_total_max + right.provision_total_max,
        diversity_total=left.diversity_total + right.diversity_total,
        diversity_total_max=left.diversity_total_max + right.diversity_total_max,
        challenge_total=left.challenge_total + right.challenge_total,
        challenge_total_max=left.challenge_total_max + right.challenge_total_max,
        sociability_total=left.sociability_total + right.sociability_total,
        sociability_total_max=left.sociability_total_max + right.sociability_total_max,
        play_value_total=left.play_value_total + right.play_value_total,
        play_value_total_max=left.play_value_total_max + right.play_value_total_max,
        usability_total=left.usability_total + right.usability_total,
        usability_total_max=left.usability_total_max + right.usability_total_max,
    )


def _serialize_score_totals(
    score_totals: ScoreTotals,
    *,
    include_maximums: bool,
) -> JsonDict:
    """Convert one score-total bucket into a JSON-safe response payload."""

    payload = {
        "provision_total": round(score_totals.provision_total, 2),
        "diversity_total": round(score_totals.diversity_total, 2),
        "challenge_total": round(score_totals.challenge_total, 2),
        "sociability_total": round(score_totals.sociability_total, 2),
        "play_value_total": round(score_totals.play_value_total, 2),
        "usability_total": round(score_totals.usability_total, 2),
    }
    if not include_maximums:
        return payload

    return {
        **payload,
        "provision_total_max": round(score_totals.provision_total_max, 2),
        "diversity_total_max": round(score_totals.diversity_total_max, 2),
        "challenge_total_max": round(score_totals.challenge_total_max, 2),
        "sociability_total_max": round(score_totals.sociability_total_max, 2),
        "play_value_total_max": round(score_totals.play_value_total_max, 2),
        "usability_total_max": round(score_totals.usability_total_max, 2),
    }

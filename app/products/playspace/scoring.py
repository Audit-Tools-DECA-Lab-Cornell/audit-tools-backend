"""
Playspace audit runtime helpers for execution-mode filtering, progress, and scoring.

The scoring model uses raw totals rather than normalized percentages:
provision is summed directly, variety and challenge contribute both domain
column totals and construct multipliers, and sociability is tracked as a
separate score stream alongside play value and usability.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from app.models import PlayspaceSubmission
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
from app.products.playspace.schemas.instrument import PlayspaceInstrumentResponse, PreAuditInputType
from app.products.playspace.scoring_metadata import (
	ScoringQuestion,
	ScoringScaleOption,
	ScoringSection,
	build_scoring_sections_from_instrument,
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
SOCIABILITY_MULTI_SELECT_KEYS = ("play_alone", "small_group", "large_group")


class UnsurePolicy(str, Enum):
	"""Supported interpretations for Unsure scale answers."""

	EXCLUDED = "unsure_as_excluded"
	ZERO = "unsure_as_zero"
	MAX = "unsure_as_max"


@dataclass(frozen=True)
class SociabilityCategoryTotals:
	total: float = 0.0
	max: float = 0.0


@dataclass(frozen=True)
class SociabilityBreakdown:
	play_alone: SociabilityCategoryTotals = SociabilityCategoryTotals()
	small_group: SociabilityCategoryTotals = SociabilityCategoryTotals()
	large_group: SociabilityCategoryTotals = SociabilityCategoryTotals()
	captured_question_count: int = 0
	eligible_question_count: int = 0


@dataclass(frozen=True)
class ScoreTotals:
	"""Internal aggregate for one section, domain, or overall audit score bucket."""

	provision_total: float = 0.0
	provision_total_max: float = 0.0
	variety_total: float = 0.0
	variety_total_max: float = 0.0
	challenge_total: float = 0.0
	challenge_total_max: float = 0.0
	sociability_total: float = 0.0
	sociability_total_max: float = 0.0
	sociability_breakdown: SociabilityBreakdown | None = None
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
	audit: PlayspaceSubmission,
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

	next_payload: JsonDict = {
		"meta": _read_json_dict(current_responses_json.get("meta")),
		"pre_audit": _read_json_dict(current_responses_json.get("pre_audit")),
		"sections": _read_json_dict(current_responses_json.get("sections")),
	}
	meta_payload = _read_json_dict(next_payload.get("meta"))
	pre_audit_payload = _read_json_dict(next_payload.get("pre_audit"))
	sections_payload = _read_json_dict(next_payload.get("sections"))

	if patch.meta is not None:
		meta_payload.update(patch.meta.model_dump(exclude_none=True))

	if patch.pre_audit is not None:
		pre_audit_payload.update(_serialize_pre_audit_patch(patch.pre_audit))

	if patch.sections:
		for section_key, section_patch in patch.sections.items():
			existing_section = _read_json_dict(sections_payload.get(section_key))
			existing_responses = _read_json_dict(existing_section.get("responses"))
			for question_key, scale_answers in section_patch.responses.items():
				existing_responses[question_key] = dict(scale_answers.items())
			existing_section["responses"] = existing_responses
			if section_patch.note is not None:
				existing_section["note"] = section_patch.note
			sections_payload[section_key] = existing_section

	next_payload["meta"] = meta_payload
	next_payload["pre_audit"] = pre_audit_payload
	next_payload["sections"] = sections_payload

	return next_payload


def build_audit_progress(
	*,
	responses_json: JsonDict,
	instrument: PlayspaceInstrumentResponse | None = None,
) -> AuditProgressResponse:
	"""Build user-facing progress for the current draft state."""

	snapshot = _build_snapshot_from_json(responses_json)
	if instrument is None:
		scoring_sections = get_scoring_sections()
		pre_audit_instrument = get_canonical_instrument_response()
	else:
		scoring_sections = build_scoring_sections_from_instrument(instrument)
		pre_audit_instrument = instrument
	return _build_audit_progress_from_snapshot(
		snapshot=snapshot,
		scoring_sections=scoring_sections,
		pre_audit_instrument=pre_audit_instrument,
	)


def build_audit_progress_for_audit(
	*,
	audit: PlayspaceSubmission,
	instrument: PlayspaceInstrumentResponse | None = None,
) -> AuditProgressResponse:
	"""Build user-facing progress directly from normalized audit relations."""

	snapshot = _build_snapshot_from_audit(audit)
	if instrument is None:
		scoring_sections = get_scoring_sections()
		pre_audit_instrument = get_canonical_instrument_response()
	else:
		scoring_sections = build_scoring_sections_from_instrument(instrument)
		pre_audit_instrument = instrument
	return _build_audit_progress_from_snapshot(
		snapshot=snapshot,
		scoring_sections=scoring_sections,
		pre_audit_instrument=pre_audit_instrument,
	)


def _build_audit_progress_from_snapshot(
	*,
	snapshot: AuditStateSnapshot,
	scoring_sections: list[ScoringSection],
	pre_audit_instrument: PlayspaceInstrumentResponse,
) -> AuditProgressResponse:
	"""Build user-facing progress from one storage-agnostic audit snapshot."""

	execution_mode = _resolve_execution_mode_from_value(execution_mode_value=snapshot.execution_mode_value)
	pre_audit_payload = snapshot.pre_audit_payload
	sections_payload = snapshot.sections_payload

	required_pre_audit_complete = _is_pre_audit_complete(
		pre_audit_payload,
		execution_mode,
		pre_audit_instrument,
	)
	section_progress: list[AuditSectionProgressResponse] = []
	visible_section_count = 0
	completed_section_count = 0
	total_visible_questions = 0
	answered_visible_questions = 0

	for section in scoring_sections:
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
	instrument: PlayspaceInstrumentResponse | None = None,
) -> JsonDict:
	"""Calculate Playspace total buckets for a completed audit draft."""

	snapshot = _build_snapshot_from_json(responses_json)
	scoring_sections = (
		get_scoring_sections() if instrument is None else build_scoring_sections_from_instrument(instrument)
	)
	return _score_audit_from_snapshot(
		snapshot=snapshot,
		include_maximums=include_maximums,
		scoring_sections=scoring_sections,
		unsure_policy=UnsurePolicy.EXCLUDED,
		include_unsure_variants=True,
	)


def score_audit_for_audit(
	*,
	audit: PlayspaceSubmission,
	include_maximums: bool = False,
	instrument: PlayspaceInstrumentResponse | None = None,
) -> JsonDict:
	"""Calculate Playspace total buckets directly from normalized audit relations."""

	snapshot = _build_snapshot_from_audit(audit)
	scoring_sections = (
		get_scoring_sections() if instrument is None else build_scoring_sections_from_instrument(instrument)
	)
	return _score_audit_from_snapshot(
		snapshot=snapshot,
		include_maximums=include_maximums,
		scoring_sections=scoring_sections,
		unsure_policy=UnsurePolicy.EXCLUDED,
		include_unsure_variants=True,
	)


def _score_audit_from_snapshot(
	*,
	snapshot: AuditStateSnapshot,
	include_maximums: bool,
	scoring_sections: list[ScoringSection],
	unsure_policy: UnsurePolicy,
	include_unsure_variants: bool,
) -> JsonDict:
	"""Calculate scores from one storage-agnostic audit snapshot."""

	execution_mode = _resolve_execution_mode_from_value(execution_mode_value=snapshot.execution_mode_value)
	if execution_mode is None:
		raise ValueError("Execution mode must be selected before scoring the audit.")

	section_scores: dict[str, JsonDict] = {}
	domain_scores: dict[str, ScoreTotals] = {}
	partition_scores: dict[str, ScoreTotals] = {
		"audit": ScoreTotals(),
		"survey": ScoreTotals(),
	}
	partition_has_questions = {
		"audit": False,
		"survey": False,
	}
	sections_payload = snapshot.sections_payload

	for section in scoring_sections:
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
				unsure_policy=unsure_policy,
			)
			section_totals = _add_score_totals(section_totals, question_totals)

			for partition_key in ("audit", "survey"):
				if _question_contributes_to_partition(question=question, partition_key=partition_key):
					partition_scores[partition_key] = _add_score_totals(
						partition_scores[partition_key],
						question_totals,
					)
					partition_has_questions[partition_key] = True

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

	result: JsonDict = {
		"overall": _serialize_score_totals(overall_totals, include_maximums=include_maximums),
		"audit": (
			_serialize_score_totals(partition_scores["audit"], include_maximums=include_maximums)
			if partition_has_questions["audit"]
			else None
		),
		"survey": (
			_serialize_score_totals(partition_scores["survey"], include_maximums=include_maximums)
			if partition_has_questions["survey"]
			else None
		),
		"by_section": section_scores,
		"by_domain": serialized_domain_scores,
		"execution_mode": execution_mode.value,
	}

	if include_maximums and include_unsure_variants:
		unsure_answer_count = _count_unsure_answers(
			snapshot=snapshot,
			execution_mode=execution_mode,
			scoring_sections=scoring_sections,
		)
		result["unsure_answer_count"] = unsure_answer_count
		if unsure_answer_count > 0:
			result["unsure_variants"] = {
				UnsurePolicy.ZERO.value: _score_audit_from_snapshot(
					snapshot=snapshot,
					include_maximums=include_maximums,
					scoring_sections=scoring_sections,
					unsure_policy=UnsurePolicy.ZERO,
					include_unsure_variants=False,
				),
				UnsurePolicy.MAX.value: _score_audit_from_snapshot(
					snapshot=snapshot,
					include_maximums=include_maximums,
					scoring_sections=scoring_sections,
					unsure_policy=UnsurePolicy.MAX,
					include_unsure_variants=False,
				),
			}
		else:
			result["unsure_variants"] = None

	return result


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
		execution_mode_value=(execution_mode_value if isinstance(execution_mode_value, str) else None),
		pre_audit_payload=_read_json_dict(responses_json.get("pre_audit")),
		sections_payload=section_payloads,
	)


def _build_snapshot_from_audit(audit: PlayspaceSubmission) -> AuditStateSnapshot:
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
			isinstance(entry, str) and entry in question.display_if.any_of_option_keys for entry in selected_value
		)
	return False


def _question_contributes_to_partition(*, question: ScoringQuestion, partition_key: str) -> bool:
	"""Return whether one scored question feeds the audit or survey partition."""

	if partition_key == "audit":
		return question.mode in {"audit", "both"}
	if partition_key == "survey":
		return question.mode in {"survey", "both"}
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
			isinstance(option_key, str) and option_key.strip() for option_key in selected_option_keys
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
		if scale.selection_mode == "multiple":
			if not isinstance(raw_answer, list) or len(raw_answer) == 0:
				return False
			if not all(isinstance(answer_key, str) for answer_key in raw_answer):
				return False
			answer_keys = [answer_key for answer_key in raw_answer if isinstance(answer_key, str)]
			if len(answer_keys) != len(set(answer_keys)):
				return False
			if any(_find_option_by_key(scale.options, answer_key) is None for answer_key in answer_keys):
				return False
		else:
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


# def _read_execution_mode_value_from_audit(audit: PlayspaceSubmission) -> str | None:
# 	"""Read the selected execution mode directly from normalized rows or cache fallback."""

# 	if audit.playspace_context is not None and audit.playspace_context.execution_mode is not None:
# 		return audit.playspace_context.execution_mode

# 	meta = _read_json_dict(_read_json_dict(audit.responses_json).get("meta"))
# 	raw_execution_mode = meta.get("execution_mode")
# 	if isinstance(raw_execution_mode, str) and raw_execution_mode.strip():
# 		return raw_execution_mode
# 	return None


def _build_pre_audit_payload_from_audit(audit: PlayspaceSubmission) -> JsonDict:
	"""Build pre-audit values from the cached aggregate payload."""

	return _read_json_dict(_read_json_dict(audit.responses_json).get("pre_audit"))


def _build_sections_payload_from_audit(audit: PlayspaceSubmission) -> dict[str, JsonDict]:
	"""Build section answer lookups from the cached aggregate payload."""

	cached_sections = _read_json_dict(_read_json_dict(audit.responses_json).get("sections"))
	return {
		section_key: _read_json_dict(_read_json_dict(section_value).get("responses"))
		for section_key, section_value in cached_sections.items()
	}


def _is_pre_audit_complete(
	pre_audit_payload: JsonDict,
	execution_mode: ExecutionMode | None,
	instrument: PlayspaceInstrumentResponse,
) -> bool:
	"""Validate that all manual pre-audit prompts are filled."""

	if execution_mode is None:
		return False

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
	unsure_policy: UnsurePolicy,
) -> ScoreTotals:
	"""Score one question according to the client-approved Playspace rules."""

	sociability_scale = next(
		(scale for scale in question.scales if scale.key == "sociability"),
		None,
	)
	empty_sociability_breakdown = (
		SociabilityBreakdown()
		if sociability_scale is not None and sociability_scale.selection_mode == "multiple"
		else None
	)

	if question.question_type != "scaled" or len(question.scales) == 0:
		return ScoreTotals(sociability_breakdown=empty_sociability_breakdown)

	question_answers = _read_json_dict(section_answers.get(question.question_key))
	provision_scale = next(scale for scale in question.scales if scale.key == "provision")
	provision_answer_key = question_answers.get("provision")
	if not isinstance(provision_answer_key, str):
		return ScoreTotals(sociability_breakdown=empty_sociability_breakdown)

	provision_option = _find_option_by_key(provision_scale.options, provision_answer_key)
	if provision_option is None:
		return ScoreTotals(sociability_breakdown=empty_sociability_breakdown)

	if _is_excluding_option(provision_option, unsure_policy):
		return ScoreTotals(sociability_breakdown=empty_sociability_breakdown)

	provision_total_max = _read_provision_scale_maximum(question=question)
	variety_total_max, variety_multiplier_max = _read_multiplier_scale_maximum(
		question=question,
		scale_key="variety",
	)
	challenge_total_max, challenge_multiplier_max = _read_multiplier_scale_maximum(
		question=question,
		scale_key="challenge",
	)
	sociability_total_max = _read_sociability_scale_maximum(question=question)
	sociability_breakdown = empty_sociability_breakdown

	if provision_option.is_unsure and unsure_policy is UnsurePolicy.MAX:
		provision_total = provision_total_max
		variety_total = variety_total_max
		variety_multiplier = variety_multiplier_max
		challenge_total = challenge_total_max
		challenge_multiplier = challenge_multiplier_max
		sociability_total = sociability_total_max
		if sociability_scale is not None and sociability_scale.selection_mode == "multiple":
			sociability_breakdown = SociabilityBreakdown(
				play_alone=SociabilityCategoryTotals(total=1.0, max=1.0),
				small_group=SociabilityCategoryTotals(total=1.0, max=1.0),
				large_group=SociabilityCategoryTotals(total=1.0, max=1.0),
				eligible_question_count=1,
			)
	else:
		provision_total = float(provision_option.addition_value)
		variety_total = 0.0
		challenge_total = 0.0
		sociability_total = 0.0
		variety_multiplier = 1.0
		challenge_multiplier = 1.0
		if (
			sociability_scale is not None
			and sociability_scale.selection_mode == "multiple"
			and not provision_option.allows_follow_up_scales
			and not provision_option.is_unsure
		):
			sociability_total_max = 0.0
		if (
			sociability_scale is not None
			and sociability_scale.selection_mode == "multiple"
			and provision_option.is_unsure
		):
			sociability_breakdown = SociabilityBreakdown(
				play_alone=SociabilityCategoryTotals(max=1.0),
				small_group=SociabilityCategoryTotals(max=1.0),
				large_group=SociabilityCategoryTotals(max=1.0),
				eligible_question_count=1,
			)

		if provision_option.allows_follow_up_scales:
			(
				variety_total,
				variety_total_max,
				variety_multiplier,
				variety_multiplier_max,
			) = _read_multiplier_scale_result(
				question=question,
				question_answers=question_answers,
				scale_key="variety",
				unsure_policy=unsure_policy,
			)
			challenge_total, challenge_total_max, challenge_multiplier, challenge_multiplier_max = (
				_read_multiplier_scale_result(
					question=question,
					question_answers=question_answers,
					scale_key="challenge",
					unsure_policy=unsure_policy,
				)
			)
			sociability_total, sociability_total_max, sociability_breakdown = _read_sociability_scale_result(
				question=question,
				question_answers=question_answers,
				unsure_policy=unsure_policy,
			)

	construct_score = provision_total * variety_multiplier * challenge_multiplier
	construct_score_max = provision_total_max * variety_multiplier_max * challenge_multiplier_max
	play_value_total = construct_score if "play_value" in question.constructs else 0.0
	play_value_total_max = construct_score_max if "play_value" in question.constructs else 0.0
	usability_total = construct_score if "usability" in question.constructs else 0.0
	usability_total_max = construct_score_max if "usability" in question.constructs else 0.0

	return ScoreTotals(
		provision_total=round(provision_total, 2),
		provision_total_max=round(provision_total_max, 2),
		variety_total=round(variety_total, 2),
		variety_total_max=round(variety_total_max, 2),
		challenge_total=round(challenge_total, 2),
		challenge_total_max=round(challenge_total_max, 2),
		sociability_total=round(sociability_total, 2),
		sociability_total_max=round(sociability_total_max, 2),
		sociability_breakdown=sociability_breakdown,
		play_value_total=round(play_value_total, 2),
		play_value_total_max=round(play_value_total_max, 2),
		usability_total=round(usability_total, 2),
		usability_total_max=round(usability_total_max, 2),
	)


def _is_excluding_option(option: ScoringScaleOption, unsure_policy: UnsurePolicy) -> bool:
	"""Return whether an option removes its score and denominator for this policy."""

	return option.is_not_applicable or (option.is_unsure and unsure_policy is UnsurePolicy.EXCLUDED)


def _max_candidate_options(options: list[ScoringScaleOption]) -> list[ScoringScaleOption]:
	"""Return options that can define a normal maximum denominator."""

	return [option for option in options if not option.is_not_applicable and not option.is_unsure]


def _read_provision_scale_maximum(*, question: ScoringQuestion) -> float:
	"""Return the highest provision score available for one question."""

	provision_scale = next(
		(current_scale for current_scale in question.scales if current_scale.key == "provision"),
		None,
	)
	if provision_scale is None:
		return 0.0
	return max(
		(float(option.addition_value) for option in _max_candidate_options(provision_scale.options)),
		default=0.0,
	)


def _read_multiplier_scale_result(
	*,
	question: ScoringQuestion,
	question_answers: JsonDict,
	scale_key: str,
	unsure_policy: UnsurePolicy,
) -> tuple[float, float, float, float]:
	"""Read one variety/challenge answer as total, max, multiplier, and multiplier max."""

	scale = next(
		(current_scale for current_scale in question.scales if current_scale.key == scale_key),
		None,
	)
	if scale is None:
		return 0.0, 0.0, 1.0, 1.0

	max_column_total, max_multiplier = _read_multiplier_scale_maximum(question=question, scale_key=scale_key)
	answer_key = question_answers.get(scale_key)
	if not isinstance(answer_key, str):
		return 0.0, max_column_total, 1.0, max_multiplier

	selected_option = _find_option_by_key(scale.options, answer_key)
	if selected_option is None:
		return 0.0, max_column_total, 1.0, max_multiplier

	if _is_excluding_option(selected_option, unsure_policy):
		return 0.0, 0.0, 1.0, 1.0

	if selected_option.is_unsure:
		if unsure_policy is UnsurePolicy.MAX:
			return max_column_total, max_column_total, max_multiplier, max_multiplier
		return 0.0, max_column_total, 1.0, max_multiplier

	column_total = max(float(selected_option.addition_value) - 1.0, 0.0)
	if selected_option.addition_value <= 0:
		return column_total, max_column_total, 1.0, max_multiplier
	return column_total, max_column_total, float(selected_option.boost_value), max_multiplier


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

	candidate_options = _max_candidate_options(scale.options)
	max_column_total = max(
		(max(float(option.addition_value) - 1.0, 0.0) for option in candidate_options),
		default=0.0,
	)
	max_multiplier = max((float(option.boost_value) for option in candidate_options), default=1.0)
	return max_column_total, max(max_multiplier, 1.0)


def _read_sociability_scale_result(
	*,
	question: ScoringQuestion,
	question_answers: JsonDict,
	unsure_policy: UnsurePolicy,
) -> tuple[float, float, SociabilityBreakdown | None]:
	"""Read one sociability answer as total and response-aware max."""

	scale = next(
		(current_scale for current_scale in question.scales if current_scale.key == "sociability"),
		None,
	)
	if scale is None:
		return 0.0, 0.0, None

	max_total = _read_sociability_scale_maximum(question=question)
	if scale.selection_mode == "multiple":
		return _read_multiple_sociability_scale_result(
			question=question,
			question_answers=question_answers,
		)

	answer_key = question_answers.get("sociability")
	if not isinstance(answer_key, str):
		return 0.0, max_total, None

	selected_option = _find_option_by_key(scale.options, answer_key)
	if selected_option is None:
		return 0.0, max_total, None

	if _is_excluding_option(selected_option, unsure_policy):
		return 0.0, 0.0, None

	if selected_option.is_unsure:
		if unsure_policy is UnsurePolicy.MAX:
			return max_total, max_total, None
		return 0.0, max_total, None

	return max(float(selected_option.addition_value) - 1.0, 0.0), max_total, None


def _read_multiple_sociability_scale_result(
	*,
	question: ScoringQuestion,
	question_answers: JsonDict,
) -> tuple[float, float, SociabilityBreakdown]:
	scale = next(scale for scale in question.scales if scale.key == "sociability")
	option_keys = [option.key for option in scale.options]
	if option_keys != list(SOCIABILITY_MULTI_SELECT_KEYS):
		raise ValueError(
			f"Question {question.question_key!r} multiple Sociability options must use the canonical ordered keys."
		)

	if "sociability" not in question_answers:
		return (
			0.0,
			3.0,
			SociabilityBreakdown(
				play_alone=SociabilityCategoryTotals(max=1.0),
				small_group=SociabilityCategoryTotals(max=1.0),
				large_group=SociabilityCategoryTotals(max=1.0),
				eligible_question_count=1,
			),
		)

	raw_answer = question_answers["sociability"]
	if not isinstance(raw_answer, list):
		raise ValueError(f"Question {question.question_key!r} multiple Sociability answer must be a list.")
	if len(raw_answer) == 0:
		raise ValueError(f"Question {question.question_key!r} multiple Sociability answer must be non-empty.")
	if not all(isinstance(answer_key, str) for answer_key in raw_answer):
		raise ValueError(f"Question {question.question_key!r} multiple Sociability answer must contain strings only.")

	selected_keys = [answer_key for answer_key in raw_answer if isinstance(answer_key, str)]
	if len(selected_keys) != len(set(selected_keys)):
		raise ValueError(f"Question {question.question_key!r} multiple Sociability answer contains duplicate keys.")
	unknown_keys = [answer_key for answer_key in selected_keys if answer_key not in SOCIABILITY_MULTI_SELECT_KEYS]
	if unknown_keys:
		raise ValueError(
			f"Question {question.question_key!r} multiple Sociability answer contains unknown keys: {unknown_keys!r}."
		)

	selected_key_set = set(selected_keys)
	return (
		float(len(selected_keys)),
		3.0,
		SociabilityBreakdown(
			play_alone=SociabilityCategoryTotals(
				total=float("play_alone" in selected_key_set),
				max=1.0,
			),
			small_group=SociabilityCategoryTotals(
				total=float("small_group" in selected_key_set),
				max=1.0,
			),
			large_group=SociabilityCategoryTotals(
				total=float("large_group" in selected_key_set),
				max=1.0,
			),
			captured_question_count=1,
			eligible_question_count=1,
		),
	)


def _read_sociability_scale_maximum(*, question: ScoringQuestion) -> float:
	"""Return the highest available sociability column score for one question."""

	scale = next(
		(current_scale for current_scale in question.scales if current_scale.key == "sociability"),
		None,
	)
	if scale is None:
		return 0.0
	if scale.selection_mode == "multiple":
		return 3.0
	return max(
		(max(float(option.addition_value) - 1.0, 0.0) for option in _max_candidate_options(scale.options)),
		default=0.0,
	)


def _count_unsure_answers(
	*,
	snapshot: AuditStateSnapshot,
	execution_mode: ExecutionMode,
	scoring_sections: list[ScoringSection],
) -> int:
	"""Count visible Unsure answers, ignoring follow-ups hidden by the provision answer."""

	count = 0
	sections_payload = snapshot.sections_payload
	for section in scoring_sections:
		section_answers = _read_json_dict(sections_payload.get(section.section_key))
		for question in _get_visible_questions(
			section=section,
			execution_mode=execution_mode,
			section_answers=section_answers,
		):
			if question.question_type != "scaled" or len(question.scales) == 0:
				continue

			question_answers = _read_json_dict(section_answers.get(question.question_key))
			provision_scale = next((scale for scale in question.scales if scale.key == "provision"), None)
			if provision_scale is None:
				continue

			provision_answer_key = question_answers.get("provision")
			if not isinstance(provision_answer_key, str):
				continue
			provision_option = _find_option_by_key(provision_scale.options, provision_answer_key)
			if provision_option is None:
				continue
			if provision_option.is_unsure:
				count += 1
			if not provision_option.allows_follow_up_scales:
				continue

			for scale in question.scales:
				if scale.key == "provision":
					continue
				answer_key = question_answers.get(scale.key)
				if not isinstance(answer_key, str):
					continue
				selected_option = _find_option_by_key(scale.options, answer_key)
				if selected_option is not None and selected_option.is_unsure:
					count += 1
	return count


def _add_score_totals(left: ScoreTotals, right: ScoreTotals) -> ScoreTotals:
	"""Sum two immutable Playspace score-total buckets."""

	return ScoreTotals(
		provision_total=left.provision_total + right.provision_total,
		provision_total_max=left.provision_total_max + right.provision_total_max,
		variety_total=left.variety_total + right.variety_total,
		variety_total_max=left.variety_total_max + right.variety_total_max,
		challenge_total=left.challenge_total + right.challenge_total,
		challenge_total_max=left.challenge_total_max + right.challenge_total_max,
		sociability_total=left.sociability_total + right.sociability_total,
		sociability_total_max=left.sociability_total_max + right.sociability_total_max,
		sociability_breakdown=_add_sociability_breakdowns(
			left.sociability_breakdown,
			right.sociability_breakdown,
		),
		play_value_total=left.play_value_total + right.play_value_total,
		play_value_total_max=left.play_value_total_max + right.play_value_total_max,
		usability_total=left.usability_total + right.usability_total,
		usability_total_max=left.usability_total_max + right.usability_total_max,
	)


def _add_sociability_breakdowns(
	left: SociabilityBreakdown | None,
	right: SociabilityBreakdown | None,
) -> SociabilityBreakdown | None:
	if left is None:
		return right
	if right is None:
		return left
	return SociabilityBreakdown(
		play_alone=SociabilityCategoryTotals(
			total=left.play_alone.total + right.play_alone.total,
			max=left.play_alone.max + right.play_alone.max,
		),
		small_group=SociabilityCategoryTotals(
			total=left.small_group.total + right.small_group.total,
			max=left.small_group.max + right.small_group.max,
		),
		large_group=SociabilityCategoryTotals(
			total=left.large_group.total + right.large_group.total,
			max=left.large_group.max + right.large_group.max,
		),
		captured_question_count=left.captured_question_count + right.captured_question_count,
		eligible_question_count=left.eligible_question_count + right.eligible_question_count,
	)


def _serialize_sociability_breakdown(breakdown: SociabilityBreakdown | None) -> JsonDict | None:
	if breakdown is None:
		return None
	return {
		"model": "multi_select_v1",
		"play_alone": {"total": round(breakdown.play_alone.total, 2), "max": round(breakdown.play_alone.max, 2)},
		"small_group": {"total": round(breakdown.small_group.total, 2), "max": round(breakdown.small_group.max, 2)},
		"large_group": {"total": round(breakdown.large_group.total, 2), "max": round(breakdown.large_group.max, 2)},
		"captured_question_count": breakdown.captured_question_count,
		"eligible_question_count": breakdown.eligible_question_count,
	}


def _serialize_score_totals(
	score_totals: ScoreTotals,
	*,
	include_maximums: bool,
) -> JsonDict:
	"""Convert one score-total bucket into a JSON-safe response payload."""

	payload: JsonDict = {
		"provision_total": round(score_totals.provision_total, 2),
		"variety_total": round(score_totals.variety_total, 2),
		"challenge_total": round(score_totals.challenge_total, 2),
		"sociability_total": round(score_totals.sociability_total, 2),
		"sociability_breakdown": _serialize_sociability_breakdown(score_totals.sociability_breakdown),
		"play_value_total": round(score_totals.play_value_total, 2),
		"usability_total": round(score_totals.usability_total, 2),
	}
	if not include_maximums:
		return payload

	return {
		**payload,
		"provision_total_max": round(score_totals.provision_total_max, 2),
		"variety_total_max": round(score_totals.variety_total_max, 2),
		"challenge_total_max": round(score_totals.challenge_total_max, 2),
		"sociability_total_max": round(score_totals.sociability_total_max, 2),
		"play_value_total_max": round(score_totals.play_value_total_max, 2),
		"usability_total_max": round(score_totals.usability_total_max, 2),
	}

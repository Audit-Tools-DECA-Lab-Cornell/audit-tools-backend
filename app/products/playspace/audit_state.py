"""
Helpers for mapping Playspace audits between the canonical aggregate document and
legacy normalized Playspace rows.

`responses_json` is now the canonical draft aggregate. The normalized Playspace
tables remain available only for migration and compatibility flows while
progress and scoring projections are derived from the aggregate document.
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
from app.products.playspace.schemas.audit import (
	AuditAggregateWriteRequest,
	AuditDraftPatchRequest,
	PreAuditPatchRequest,
	SectionDraftPatchRequest,
)

CURRENT_AUDIT_SCHEMA_VERSION = 1
PRE_AUDIT_FIELD_ORDER = (
	"place_size",
	"current_users_0_5",
	"current_users_6_12",
	"current_users_13_17",
	"current_users_18_plus",
	"playspace_busyness",
	"season",
	"weather_conditions",
	"wind_conditions",
)
MULTI_SELECT_PRE_AUDIT_FIELDS = {
	"weather_conditions",
}


def build_responses_json_from_relations(audit: Audit) -> JSONDict:
	"""Return the canonical aggregate, falling back to legacy relations when needed."""

	cached_payload = _read_json_dict(audit.responses_json)
	has_cached_structure = any(
		aggregate_key in cached_payload
		for aggregate_key in (
			"schema_version",
			"revision",
			"meta",
			"pre_audit",
			"sections",
		)
	)
	if has_cached_structure:
		return _normalize_responses_payload(cached_payload)

	return _read_json_dict(
		normalize_legacy_provision_payload(
			{
				"meta": _build_meta_payload(audit=audit),
				"pre_audit": _build_pre_audit_payload(audit=audit),
				"sections": _build_sections_payload(audit=audit),
			}
		)
	)


def build_legacy_responses_json_from_relations(audit: Audit) -> JSONDict:
	"""Rebuild the pre-migration Playspace payload directly from legacy relations."""

	return _read_json_dict(
		normalize_legacy_provision_payload(
			{
				"meta": _build_meta_payload(audit=audit),
				"pre_audit": _build_pre_audit_payload(audit=audit),
				"sections": _build_sections_payload(audit=audit),
			}
		)
	)


def get_execution_mode_value(audit: Audit) -> str | None:
	"""Read the selected execution mode from the aggregate with legacy fallback."""

	meta = _read_json_dict(build_responses_json_from_relations(audit).get("meta"))
	raw_execution_mode = meta.get("execution_mode")
	if isinstance(raw_execution_mode, str) and raw_execution_mode.strip():
		return raw_execution_mode

	if audit.playspace_context is not None and audit.playspace_context.execution_mode is not None:
		return audit.playspace_context.execution_mode

	return None


def get_draft_progress_percent(audit: Audit) -> float | None:
	"""Read the stored draft progress percentage from projections with legacy fallback."""

	raw_scores = _read_json_dict(audit.scores_json)
	raw_progress_percent = raw_scores.get("draft_progress_percent")
	if isinstance(raw_progress_percent, int | float):
		return float(raw_progress_percent)

	if audit.playspace_context is not None and audit.playspace_context.draft_progress_percent is not None:
		return float(audit.playspace_context.draft_progress_percent)

	return None


def get_aggregate_schema_version(audit: Audit) -> int:
	"""Read the current aggregate schema version from the canonical payload."""

	payload = build_responses_json_from_relations(audit)
	return _read_positive_int(payload.get("schema_version"), default=CURRENT_AUDIT_SCHEMA_VERSION)


def get_aggregate_revision(audit: Audit) -> int:
	"""Read the current aggregate revision from the canonical payload."""

	payload = build_responses_json_from_relations(audit)
	return _read_non_negative_int(payload.get("revision"), default=0)


def apply_draft_patch_to_relations(audit: Audit, patch: AuditDraftPatchRequest) -> None:
	"""Apply one typed draft patch into the canonical aggregate document."""

	if patch.meta is not None and "execution_mode" in patch.meta.model_fields_set:
		set_execution_mode_value(
			audit=audit,
			execution_mode=(patch.meta.execution_mode.value if patch.meta.execution_mode is not None else None),
		)

	next_payload = _normalize_responses_payload(build_responses_json_from_relations(audit))

	if patch.pre_audit is not None:
		_merge_pre_audit_patch_into_payload(
			payload=next_payload,
			pre_audit_patch=patch.pre_audit,
		)

	for section_key, section_patch in patch.sections.items():
		_merge_section_patch_into_payload(
			payload=next_payload,
			section_key=section_key,
			section_patch=section_patch,
		)

	audit.responses_json = next_payload


def replace_audit_aggregate(
	*,
	audit: Audit,
	aggregate: AuditAggregateWriteRequest,
) -> None:
	"""Replace the canonical aggregate body while preserving server-managed revision."""

	current_payload = _normalize_responses_payload(build_responses_json_from_relations(audit))
	schema_version = (
		aggregate.schema_version
		if aggregate.schema_version is not None
		else _read_positive_int(
			current_payload.get("schema_version"),
			default=CURRENT_AUDIT_SCHEMA_VERSION,
		)
	)
	next_payload: JSONDict = {
		"schema_version": schema_version,
		"revision": _read_non_negative_int(current_payload.get("revision"), default=0),
		"meta": _serialize_meta_request(aggregate.meta),
		"pre_audit": _serialize_pre_audit_request(aggregate.pre_audit),
		"sections": _serialize_sections_request(aggregate.sections),
	}
	audit.responses_json = next_payload


def set_aggregate_revision(audit: Audit, revision: int) -> None:
	"""Persist one server-managed aggregate revision number."""

	responses_payload = _normalize_responses_payload(build_responses_json_from_relations(audit))
	responses_payload["revision"] = max(0, revision)
	audit.responses_json = responses_payload


def _normalize_responses_payload(value: object) -> JSONDict:
	"""Normalize a cached aggregate payload into the stable responses_json shape."""

	payload = _read_json_dict(value)
	return _read_json_dict(
		normalize_legacy_provision_payload(
			{
				"schema_version": _read_positive_int(
					payload.get("schema_version"),
					default=CURRENT_AUDIT_SCHEMA_VERSION,
				),
				"revision": _read_non_negative_int(payload.get("revision"), default=0),
				"meta": _read_json_dict(payload.get("meta")),
				"pre_audit": _read_json_dict(payload.get("pre_audit")),
				"sections": _read_json_dict(payload.get("sections")),
			}
		)
	)


def normalize_legacy_provision_payload(value: object) -> object:
	"""Recursively normalize legacy quantity keys in Playspace payloads."""

	if isinstance(value, dict):
		next_payload: JSONDict = {}
		for key, item in value.items():
			next_key = "provision" if key == "quantity" else key
			next_payload[next_key] = normalize_legacy_provision_payload(item)
		return next_payload

	if isinstance(value, list):
		return [normalize_legacy_provision_payload(item) for item in value]

	if value == "quantity":
		return "provision"

	return value


def _merge_pre_audit_patch_into_payload(
	*,
	payload: JSONDict,
	pre_audit_patch: PreAuditPatchRequest,
) -> None:
	"""Merge one partial pre-audit patch into the aggregate payload."""

	next_pre_audit = dict(_read_json_dict(payload.get("pre_audit")))
	fields_set = pre_audit_patch.model_fields_set

	if "season" in fields_set:
		next_pre_audit["season"] = pre_audit_patch.season
	if "place_size" in fields_set:
		next_pre_audit["place_size"] = pre_audit_patch.place_size
	if "current_users_0_5" in fields_set:
		next_pre_audit["current_users_0_5"] = pre_audit_patch.current_users_0_5
	if "current_users_6_12" in fields_set:
		next_pre_audit["current_users_6_12"] = pre_audit_patch.current_users_6_12
	if "current_users_13_17" in fields_set:
		next_pre_audit["current_users_13_17"] = pre_audit_patch.current_users_13_17
	if "current_users_18_plus" in fields_set:
		next_pre_audit["current_users_18_plus"] = pre_audit_patch.current_users_18_plus
	if "playspace_busyness" in fields_set:
		next_pre_audit["playspace_busyness"] = pre_audit_patch.playspace_busyness
	if "weather_conditions" in fields_set:
		next_pre_audit["weather_conditions"] = list(pre_audit_patch.weather_conditions)
	if "wind_conditions" in fields_set:
		next_pre_audit["wind_conditions"] = pre_audit_patch.wind_conditions

	payload["pre_audit"] = next_pre_audit


def _merge_section_patch_into_payload(
	*,
	payload: JSONDict,
	section_key: str,
	section_patch: SectionDraftPatchRequest,
) -> None:
	"""Merge one section patch into the aggregate payload."""

	sections_payload = _read_json_dict(payload.get("sections"))
	next_section = dict(_read_json_dict(sections_payload.get(section_key)))
	next_responses = dict(_read_json_dict(next_section.get("responses")))

	for question_key, scale_answers in section_patch.responses.items():
		next_responses[question_key] = dict(scale_answers)

	next_section["responses"] = next_responses
	if "note" in section_patch.model_fields_set:
		next_section["note"] = section_patch.note

	sections_payload[section_key] = next_section
	payload["sections"] = sections_payload


def _serialize_meta_request(meta: object) -> JSONDict:
	"""Serialize one request-side aggregate meta block into canonical JSON."""

	if not isinstance(meta, object) or meta is None:
		return {}

	execution_mode = getattr(meta, "execution_mode", None)
	if isinstance(execution_mode, str):
		return {"execution_mode": execution_mode}
	if execution_mode is not None and hasattr(execution_mode, "value"):
		return {"execution_mode": str(execution_mode.value)}
	return {}


def _serialize_pre_audit_request(pre_audit: object) -> JSONDict:
	"""Serialize one request-side pre-audit block into canonical JSON."""

	if not isinstance(pre_audit, object) or pre_audit is None:
		return {}

	return {
		"place_size": getattr(pre_audit, "place_size", None),
		"current_users_0_5": getattr(pre_audit, "current_users_0_5", None),
		"current_users_6_12": getattr(pre_audit, "current_users_6_12", None),
		"current_users_13_17": getattr(pre_audit, "current_users_13_17", None),
		"current_users_18_plus": getattr(pre_audit, "current_users_18_plus", None),
		"playspace_busyness": getattr(pre_audit, "playspace_busyness", None),
		"season": getattr(pre_audit, "season", None),
		"weather_conditions": list(getattr(pre_audit, "weather_conditions", [])),
		"wind_conditions": getattr(pre_audit, "wind_conditions", None),
	}


def _serialize_sections_request(
	sections: dict[str, SectionDraftPatchRequest],
) -> JSONDict:
	"""Serialize one request-side section map into canonical JSON."""

	serialized_sections: JSONDict = {}
	for section_key, section_state in sections.items():
		serialized_sections[section_key] = {
			"note": section_state.note,
			"responses": {
				question_key: dict(scale_answers) for question_key, scale_answers in section_state.responses.items()
			},
		}
	return serialized_sections


def _apply_pre_audit_patch(
	*,
	audit: Audit,
	pre_audit_patch: PreAuditPatchRequest,
) -> None:
	"""Merge one partial pre-audit patch into the current normalized row snapshot."""

	current_pre_audit = _build_pre_audit_payload(audit=audit)
	next_pre_audit = dict(current_pre_audit)
	fields_set = pre_audit_patch.model_fields_set

	if "season" in fields_set:
		next_pre_audit["season"] = pre_audit_patch.season
	if "place_size" in fields_set:
		next_pre_audit["place_size"] = pre_audit_patch.place_size
	if "current_users_0_5" in fields_set:
		next_pre_audit["current_users_0_5"] = pre_audit_patch.current_users_0_5
	if "current_users_6_12" in fields_set:
		next_pre_audit["current_users_6_12"] = pre_audit_patch.current_users_6_12
	if "current_users_13_17" in fields_set:
		next_pre_audit["current_users_13_17"] = pre_audit_patch.current_users_13_17
	if "current_users_18_plus" in fields_set:
		next_pre_audit["current_users_18_plus"] = pre_audit_patch.current_users_18_plus
	if "playspace_busyness" in fields_set:
		next_pre_audit["playspace_busyness"] = pre_audit_patch.playspace_busyness
	if "weather_conditions" in fields_set:
		next_pre_audit["weather_conditions"] = list(pre_audit_patch.weather_conditions)
	if "wind_conditions" in fields_set:
		next_pre_audit["wind_conditions"] = pre_audit_patch.wind_conditions

	_replace_pre_audit_answers(audit=audit, payload=next_pre_audit)


def _apply_section_patch(
	*,
	audit: Audit,
	section_key: str,
	section_patch: SectionDraftPatchRequest,
) -> None:
	"""Merge one section patch into the normalized section/question answer rows."""

	section = _get_or_create_section(audit=audit, section_key=section_key)
	if "note" in section_patch.model_fields_set:
		section.note = section_patch.note

	for question_key, scale_answers in section_patch.responses.items():
		question_response = _get_or_create_question_response(
			section=section,
			question_key=question_key,
		)
		_replace_scale_answers(
			question_response=question_response,
			scale_answers=_read_string_dict(scale_answers),
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
	"""Write the selected execution mode into the aggregate and summary projection."""

	responses_payload = _normalize_responses_payload(build_responses_json_from_relations(audit))
	meta_payload = dict(_read_json_dict(responses_payload.get("meta")))
	if execution_mode is None:
		meta_payload.pop("execution_mode", None)
	else:
		meta_payload["execution_mode"] = execution_mode
	responses_payload["meta"] = meta_payload
	audit.responses_json = responses_payload

	context = audit.playspace_context
	if execution_mode is None:
		if context is not None:
			context.execution_mode = None
		return

	_get_or_create_context(audit=audit).execution_mode = execution_mode


def set_draft_progress_percent(audit: Audit, draft_progress_percent: float | None) -> None:
	"""Persist the current draft percentage into projections for list views."""

	scores_payload = _read_json_dict(audit.scores_json)
	if draft_progress_percent is None:
		scores_payload.pop("draft_progress_percent", None)
	else:
		scores_payload["draft_progress_percent"] = draft_progress_percent
	audit.scores_json = scores_payload

	context = audit.playspace_context
	if draft_progress_percent is None:
		if context is not None:
			context.draft_progress_percent = None
		return

	_get_or_create_context(audit=audit).draft_progress_percent = draft_progress_percent


def _build_meta_payload(audit: Audit) -> JSONDict:
	"""Build the legacy `meta` payload used by the current scoring helpers."""

	execution_mode: str | None = None
	if audit.playspace_context is not None and audit.playspace_context.execution_mode is not None:
		execution_mode = audit.playspace_context.execution_mode
	else:
		cached_meta = _read_json_dict(_read_json_dict(audit.responses_json).get("meta"))
		raw_execution_mode = cached_meta.get("execution_mode")
		if isinstance(raw_execution_mode, str) and raw_execution_mode.strip():
			execution_mode = raw_execution_mode
	if execution_mode is None:
		return {}
	return {"execution_mode": execution_mode}


def _build_pre_audit_payload(audit: Audit) -> JSONDict:
	"""Build the legacy `pre_audit` payload from normalized rows with cache fallback."""

	if audit.playspace_pre_audit_answers:
		payload: JSONDict = {}
		grouped_values: dict[str, list[tuple[int, str]]] = {}
		for answer in audit.playspace_pre_audit_answers:
			grouped_values.setdefault(answer.field_key, []).append((answer.sort_order, answer.selected_value))

		for field_key, values in grouped_values.items():
			ordered_values = [value for _sort_order, value in sorted(values, key=lambda item: item[0])]
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

	existing_by_key = {
		(answer.field_key, answer.selected_value): answer for answer in audit.playspace_pre_audit_answers
	}
	next_rows: list[PlayspacePreAuditAnswer] = []
	for field_key in PRE_AUDIT_FIELD_ORDER:
		raw_value = payload.get(field_key)
		if isinstance(raw_value, list):
			for sort_order, selected_value in enumerate(_string_values(raw_value)):
				next_rows.append(
					_get_or_create_pre_audit_answer(
						existing_by_key=existing_by_key,
						field_key=field_key,
						selected_value=selected_value,
						sort_order=sort_order,
					)
				)
			continue

		if isinstance(raw_value, str) and raw_value.strip():
			selected_value = raw_value.strip()
			next_rows.append(
				_get_or_create_pre_audit_answer(
					existing_by_key=existing_by_key,
					field_key=field_key,
					selected_value=selected_value,
					sort_order=0,
				)
			)

	audit.playspace_pre_audit_answers = next_rows


def _get_or_create_pre_audit_answer(
	*,
	existing_by_key: dict[tuple[str, str], PlayspacePreAuditAnswer],
	field_key: str,
	selected_value: str,
	sort_order: int,
) -> PlayspacePreAuditAnswer:
	"""Reuse an existing pre-audit row when the logical answer key still matches."""

	existing_answer = existing_by_key.pop((field_key, selected_value), None)
	if existing_answer is None:
		return PlayspacePreAuditAnswer(
			field_key=field_key,
			selected_value=selected_value,
			sort_order=sort_order,
		)

	existing_answer.sort_order = sort_order
	return existing_answer


def _replace_sections_from_cache(audit: Audit) -> None:
	"""Replace normalized section rows from the current legacy cache payload."""

	cached_responses = _read_json_dict(audit.responses_json)
	sections_payload = _read_json_dict(cached_responses.get("sections"))
	existing_sections_by_key = {section.section_key: section for section in audit.playspace_sections}
	next_sections: list[PlayspaceAuditSection] = []

	for section_key, raw_section_value in sections_payload.items():
		section_payload = _read_json_dict(raw_section_value)
		existing_section = existing_sections_by_key.pop(section_key, None)
		section = existing_section if existing_section is not None else PlayspaceAuditSection(section_key=section_key)
		note_value = section_payload.get("note")
		section.note = note_value if isinstance(note_value, str) else None

		responses_payload = _read_json_dict(section_payload.get("responses"))
		_replace_question_responses(section=section, responses_payload=responses_payload)
		next_sections.append(section)

	audit.playspace_sections = next_sections


def _replace_question_responses(
	*,
	section: PlayspaceAuditSection,
	responses_payload: JSONDict,
) -> None:
	"""Replace one section's question rows while reusing matching existing children."""

	existing_questions_by_key = {
		question_response.question_key: question_response for question_response in section.question_responses
	}
	next_question_responses: list[PlayspaceQuestionResponse] = []
	for question_key, raw_question_value in responses_payload.items():
		existing_question = existing_questions_by_key.pop(question_key, None)
		question_response = (
			existing_question if existing_question is not None else PlayspaceQuestionResponse(question_key=question_key)
		)
		_replace_scale_answers(
			question_response=question_response,
			scale_answers=_read_string_dict(raw_question_value),
		)
		next_question_responses.append(question_response)

	section.question_responses = next_question_responses


def _replace_scale_answers(
	*,
	question_response: PlayspaceQuestionResponse,
	scale_answers: dict[str, str],
) -> None:
	"""Replace one question's normalized scale-answer rows."""

	existing_answers_by_key = {scale_answer.scale_key: scale_answer for scale_answer in question_response.scale_answers}
	next_scale_answers: list[PlayspaceScaleAnswer] = []
	for scale_key, option_key in sorted(scale_answers.items(), key=lambda item: item[0]):
		normalized_scale_key = scale_key.strip()
		normalized_option_key = option_key.strip()
		if not normalized_scale_key or not normalized_option_key:
			continue

		existing_answer = existing_answers_by_key.pop(normalized_scale_key, None)
		if existing_answer is None:
			next_scale_answers.append(
				PlayspaceScaleAnswer(
					scale_key=normalized_scale_key,
					option_key=normalized_option_key,
				)
			)
			continue

		existing_answer.option_key = normalized_option_key
		next_scale_answers.append(existing_answer)

	question_response.scale_answers = next_scale_answers


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


def _read_positive_int(value: object, *, default: int) -> int:
	"""Read a positive integer from unknown JSON-like input."""

	if isinstance(value, int) and value >= 1:
		return value
	return default


def _read_non_negative_int(value: object, *, default: int) -> int:
	"""Read a non-negative integer from unknown JSON-like input."""

	if isinstance(value, int) and value >= 0:
		return value
	return default


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
	"""Filter one iterable down to unique non-empty string members while preserving order."""

	unique_values: list[str] = []
	seen: set[str] = set()
	for value in values:
		if not isinstance(value, str):
			continue
		normalized_value = value.strip()
		if not normalized_value or normalized_value in seen:
			continue
		seen.add(normalized_value)
		unique_values.append(normalized_value)
	return unique_values

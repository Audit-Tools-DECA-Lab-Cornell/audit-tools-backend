"""Helpers for working with the Playspace canonical audit aggregate."""

from __future__ import annotations

from app.models import Audit, JSONDict, PlayspaceAuditContext
from app.products.playspace.schemas.audit import (
	AuditAggregateWriteRequest,
	AuditDraftPatchRequest,
	PreAuditPatchRequest,
	SectionDraftPatchRequest,
)

CURRENT_AUDIT_SCHEMA_VERSION = 1


def build_responses_json_from_relations(audit: Audit) -> JSONDict:
	"""Return the canonical aggregate payload stored in `Audit.responses_json`."""

	return _normalize_responses_payload(audit.responses_json)


def get_execution_mode_value(audit: Audit) -> str | None:
	"""Read the selected execution mode from the canonical aggregate."""

	meta = _read_json_dict(build_responses_json_from_relations(audit).get("meta"))
	raw_execution_mode = meta.get("execution_mode")
	if isinstance(raw_execution_mode, str) and raw_execution_mode.strip():
		return raw_execution_mode

	return None


def get_draft_progress_percent(audit: Audit) -> float | None:
	"""Read the stored draft progress percentage from the canonical score cache."""

	raw_scores = _read_json_dict(audit.scores_json)
	raw_progress_percent = raw_scores.get("draft_progress_percent")
	if isinstance(raw_progress_percent, int | float):
		return float(raw_progress_percent)

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
	return {
		"schema_version": _read_positive_int(
			payload.get("schema_version"),
			default=CURRENT_AUDIT_SCHEMA_VERSION,
		),
		"revision": _read_non_negative_int(payload.get("revision"), default=0),
		"meta": _read_json_dict(payload.get("meta")),
		"pre_audit": _read_json_dict(payload.get("pre_audit")),
		"sections": _read_json_dict(payload.get("sections")),
	}


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


def _get_or_create_context(audit: Audit) -> PlayspaceAuditContext:
	"""Return the existing context row or create one in-memory."""

	if audit.playspace_context is None:
		audit.playspace_context = PlayspaceAuditContext()
	return audit.playspace_context


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



"""Helpers for reading and writing Playspace audit draft state.

Storage strategy
----------------
Draft audits (IN_PROGRESS / PAUSED) that are live in a SQLAlchemy session use
the normalized tables as the authoritative write target:

    playspace_audit_contexts      — execution_mode, progress %, schema_version, revision
    playspace_pre_audit_answers   — one row per selected pre-audit field value
    playspace_audit_sections      — one row per section (holds the section note)
    playspace_question_responses  — one row per answered question within a section
    playspace_scale_answers       — one row per scale answer within a question

Submitted audits use `PlayspaceSubmission.responses_json` as an immutable JSONB
snapshot written exactly once at submission time.

Fallback: when the submission is **not** attached to a SQLAlchemy session (seed
data, in-memory test helpers) all operations fall back to the JSONB fields so
the existing seed/scoring paths continue to work without modification.
"""

from __future__ import annotations

from sqlalchemy import inspect as sa_inspect

from app.models import (
	AuditStatus,
	JSONDict,
	PlayspacePreSubmissionAnswer,
	PlayspaceQuestionResponse,
	PlayspaceScaleAnswer,
	PlayspaceSubmissionContext,
	PlayspaceSubmissionSection,
	PlayspaceSubmission,
)
from app.products.playspace.schemas.audit import (
	AuditAggregateWriteRequest,
	AuditDraftPatchRequest,
	PreAuditPatchRequest,
	SectionDraftPatchRequest,
)

CURRENT_AUDIT_SCHEMA_VERSION = 1

# Pre-audit fields that may hold more than one selected value (stored as
# multiple rows in playspace_pre_audit_answers).
_MULTI_SELECT_PRE_AUDIT_FIELDS: frozenset[str] = frozenset({"weather_conditions"})


# ── routing helpers ──────────────────────────────────────────────────────────


def _in_session(audit: PlayspaceSubmission) -> bool:
	"""Return True when the submission is attached to a live SQLAlchemy session."""

	return sa_inspect(audit).session is not None


def _use_normalized(audit: PlayspaceSubmission) -> bool:
	"""Return True when normalized tables should be used instead of JSONB.

	Conditions:
	- The audit is a draft (not yet submitted).
	- The submission is in a live DB session, meaning its ORM collections are
	eagerly loaded or will be lazy-loaded safely within that session.
	"""

	return audit.status != AuditStatus.SUBMITTED and _in_session(audit)


# ── context helpers ──────────────────────────────────────────────────────────


def _get_or_create_context(audit: PlayspaceSubmission) -> PlayspaceSubmissionContext:
	"""Return the existing audit context row or create and attach a new one."""

	if audit.submission_context is not None:
		return audit.submission_context

	ctx = PlayspaceSubmissionContext(
		submission_id=audit.id,
		schema_version=CURRENT_AUDIT_SCHEMA_VERSION,
		revision=0,
	)
	audit.submission_context = ctx
	return ctx


# ── public read API ──────────────────────────────────────────────────────────


def build_responses_json_from_relations(audit: PlayspaceSubmission) -> JSONDict:
	"""Return the canonical aggregate payload for this submission.

	- Draft in session  → reconstructed from normalized ORM relations.
	- Submitted         → read from the immutable JSONB snapshot.
	- Draft without session (seed) → read from JSONB fallback.
	"""

	if _use_normalized(audit):
		return _build_responses_from_normalized(audit)
	return _normalize_responses_payload(audit.responses_json)


def get_execution_mode_value(audit: PlayspaceSubmission) -> str | None:
	"""Return the selected execution mode string, or None when not yet chosen."""

	if _use_normalized(audit):
		ctx = audit.submission_context
		return ctx.execution_mode if ctx is not None else None

	meta = _read_json_dict(build_responses_json_from_relations(audit).get("meta"))
	raw = meta.get("execution_mode")
	return raw if isinstance(raw, str) and raw.strip() else None


def get_draft_progress_percent(audit: PlayspaceSubmission) -> float | None:
	"""Return the stored draft completion percentage, or None for submitted audits."""

	if audit.status == AuditStatus.SUBMITTED:
		return None

	if _use_normalized(audit):
		ctx = audit.submission_context
		return ctx.draft_progress_percent if ctx is not None else None

	raw = _read_json_dict(audit.scores_json).get("draft_progress_percent")
	return float(raw) if isinstance(raw, int | float) else None


def get_aggregate_schema_version(audit: PlayspaceSubmission) -> int:
	"""Return the aggregate schema version tracked for this submission."""

	if _use_normalized(audit):
		ctx = audit.submission_context
		return ctx.schema_version if ctx is not None else CURRENT_AUDIT_SCHEMA_VERSION

	payload = build_responses_json_from_relations(audit)
	return _read_positive_int(payload.get("schema_version"), default=CURRENT_AUDIT_SCHEMA_VERSION)


def get_aggregate_revision(audit: PlayspaceSubmission) -> int:
	"""Return the current aggregate revision counter for optimistic-concurrency checks."""

	if _use_normalized(audit):
		ctx = audit.submission_context
		return ctx.revision if ctx is not None else 0

	payload = build_responses_json_from_relations(audit)
	return _read_non_negative_int(payload.get("revision"), default=0)


# ── public write API ─────────────────────────────────────────────────────────


def set_execution_mode_value(audit: PlayspaceSubmission, execution_mode: str | None) -> None:
	"""Persist the chosen execution mode to the context row (or JSONB fallback)."""

	audit.execution_mode = execution_mode

	if _use_normalized(audit):
		ctx = _get_or_create_context(audit)
		ctx.execution_mode = execution_mode
		return

	# JSONB path (seed / submitted audits).
	responses = _normalize_responses_payload(audit.responses_json)
	meta = dict(_read_json_dict(responses.get("meta")))
	if execution_mode is None:
		meta.pop("execution_mode", None)
	else:
		meta["execution_mode"] = execution_mode
	responses["meta"] = meta
	audit.responses_json = responses


def set_draft_progress_percent(audit: PlayspaceSubmission, draft_progress_percent: float | None) -> None:
	"""Persist the draft completion percentage to the context row (or JSONB fallback)."""

	audit.draft_progress_percent = draft_progress_percent

	if _use_normalized(audit):
		ctx = _get_or_create_context(audit)
		ctx.draft_progress_percent = draft_progress_percent
		return

	# JSONB path (seed / submitted audits).
	scores = _read_json_dict(audit.scores_json)
	if draft_progress_percent is None:
		scores.pop("draft_progress_percent", None)
	else:
		scores["draft_progress_percent"] = draft_progress_percent
	audit.scores_json = scores


def set_aggregate_revision(audit: PlayspaceSubmission, revision: int) -> None:
	"""Advance the aggregate revision counter."""

	safe_revision = max(0, revision)

	if _use_normalized(audit):
		ctx = _get_or_create_context(audit)
		ctx.revision = safe_revision
		return

	# JSONB path (seed / submitted audits).
	responses = _normalize_responses_payload(audit.responses_json)
	responses["revision"] = safe_revision
	audit.responses_json = responses


def apply_draft_patch_to_relations(audit: PlayspaceSubmission, patch: AuditDraftPatchRequest) -> None:
	"""Merge one typed draft patch into the submission state.

	For drafts in a session the patch targets the normalized tables.
	Otherwise falls back to patching the JSONB blob directly.
	"""

	if _use_normalized(audit):
		_apply_patch_normalized(audit, patch)
		return

	# JSONB fallback.
	if patch.meta is not None and "execution_mode" in patch.meta.model_fields_set:
		set_execution_mode_value(
			audit=audit,
			execution_mode=(patch.meta.execution_mode.value if patch.meta.execution_mode is not None else None),
		)

	next_payload = _normalize_responses_payload(audit.responses_json)
	if patch.pre_audit is not None:
		_merge_pre_audit_into_payload(payload=next_payload, pre_audit_patch=patch.pre_audit)
	for section_key, section_patch in patch.sections.items():
		_merge_section_into_payload(
			payload=next_payload,
			section_key=section_key,
			section_patch=section_patch,
		)
	audit.responses_json = next_payload


def replace_audit_aggregate(
	*,
	audit: PlayspaceSubmission,
	aggregate: AuditAggregateWriteRequest,
) -> None:
	"""Replace the entire audit state from a full aggregate write request."""

	if _use_normalized(audit):
		_replace_normalized(audit, aggregate)
		return

	# JSONB fallback.
	current = _normalize_responses_payload(audit.responses_json)
	schema_version = (
		aggregate.schema_version
		if aggregate.schema_version is not None
		else _read_positive_int(current.get("schema_version"), default=CURRENT_AUDIT_SCHEMA_VERSION)
	)
	audit.responses_json = {
		"schema_version": schema_version,
		"revision": _read_non_negative_int(current.get("revision"), default=0),
		"meta": _serialize_meta_request(aggregate.meta),
		"pre_audit": _serialize_pre_audit_request(aggregate.pre_audit),
		"sections": _serialize_sections_request(aggregate.sections),
	}


# ── normalized-table read ────────────────────────────────────────────────────


def _build_responses_from_normalized(audit: PlayspaceSubmission) -> JSONDict:
	"""Reconstruct the canonical JSONB shape from normalized ORM relations."""

	ctx = audit.submission_context
	schema_version = ctx.schema_version if ctx is not None else CURRENT_AUDIT_SCHEMA_VERSION
	revision = ctx.revision if ctx is not None else 0
	execution_mode = ctx.execution_mode if ctx is not None else None

	meta: JSONDict = {}
	if execution_mode is not None:
		meta["execution_mode"] = execution_mode

	# Pre-audit: group multi-select fields into lists; single-select as scalar.
	pre_audit: JSONDict = {}
	multi: dict[str, list[tuple[int, str]]] = {}
	single: dict[str, str] = {}
	for answer in audit.pre_submission_answers or []:
		if answer.field_key in _MULTI_SELECT_PRE_AUDIT_FIELDS:
			multi.setdefault(answer.field_key, []).append((answer.sort_order, answer.selected_value))
		else:
			single[answer.field_key] = answer.selected_value
	pre_audit = {**single}
	for key, pairs in multi.items():
		pre_audit[key] = [v for _, v in sorted(pairs)]

	# Sections: rebuild nested structure from section → question → scale.
	sections: JSONDict = {}
	for section in audit.submission_sections or []:
		responses: JSONDict = {}
		for qr in section.question_responses or []:
			responses[qr.question_key] = {sa.scale_key: sa.option_key for sa in qr.scale_answers or []}
		section_data: JSONDict = {"responses": responses}
		if section.note is not None:
			section_data["note"] = section.note
		sections[section.section_key] = section_data

	return {
		"schema_version": schema_version,
		"revision": revision,
		"meta": meta,
		"pre_audit": pre_audit,
		"sections": sections,
	}


# ── normalized-table writes ──────────────────────────────────────────────────


def _apply_patch_normalized(audit: PlayspaceSubmission, patch: AuditDraftPatchRequest) -> None:
	"""Apply one draft patch to the normalized ORM relations in place."""

	# --- execution mode ---
	if patch.meta is not None and "execution_mode" in patch.meta.model_fields_set:
		new_mode = patch.meta.execution_mode.value if patch.meta.execution_mode is not None else None
		audit.execution_mode = new_mode
		ctx = _get_or_create_context(audit)
		ctx.execution_mode = new_mode

	# --- pre-audit ---
	if patch.pre_audit is not None:
		_upsert_pre_audit_normalized(audit, patch.pre_audit)

	# --- sections ---
	for section_key, section_patch in patch.sections.items():
		_upsert_section_normalized(audit, section_key, section_patch)


def _replace_normalized(audit: PlayspaceSubmission, aggregate: AuditAggregateWriteRequest) -> None:
	"""Rebuild all normalized relations from a full aggregate write request."""

	# Meta / context.
	ctx = _get_or_create_context(audit)
	if aggregate.meta is not None:
		mode = aggregate.meta.execution_mode
		ctx.execution_mode = mode.value if mode is not None else None
		audit.execution_mode = ctx.execution_mode
	if aggregate.schema_version is not None:
		ctx.schema_version = aggregate.schema_version

	# Pre-audit: clear everything and rebuild.
	audit.pre_submission_answers.clear()
	if aggregate.pre_audit is not None:
		_upsert_pre_audit_normalized(audit, aggregate.pre_audit)

	# Sections: clear everything and rebuild.
	audit.submission_sections.clear()
	for section_key, section_state in aggregate.sections.items():
		_upsert_section_normalized(audit, section_key, section_state)


def _upsert_pre_audit_normalized(audit: PlayspaceSubmission, pre_audit: PreAuditPatchRequest) -> None:
	"""Merge pre-audit patch fields into the normalized pre-audit answer rows."""

	fields_set = pre_audit.model_fields_set
	# Determine which fields are being patched so we can delete and recreate them.
	patch_map: dict[str, list[str]] = {}

	def _add(field: str, value: object) -> None:
		if value is None:
			patch_map[field] = []
		elif isinstance(value, list):
			patch_map[field] = [str(v) for v in value if v is not None]
		else:
			patch_map[field] = [str(value)]

	if "season" in fields_set:
		_add("season", pre_audit.season)
	if "place_size" in fields_set:
		_add("place_size", pre_audit.place_size)
	if "current_users_0_5" in fields_set:
		_add("current_users_0_5", pre_audit.current_users_0_5)
	if "current_users_6_12" in fields_set:
		_add("current_users_6_12", pre_audit.current_users_6_12)
	if "current_users_13_17" in fields_set:
		_add("current_users_13_17", pre_audit.current_users_13_17)
	if "current_users_18_plus" in fields_set:
		_add("current_users_18_plus", pre_audit.current_users_18_plus)
	if "playspace_busyness" in fields_set:
		_add("playspace_busyness", pre_audit.playspace_busyness)
	if "weather_conditions" in fields_set:
		_add("weather_conditions", pre_audit.weather_conditions)
	if "wind_conditions" in fields_set:
		_add("wind_conditions", pre_audit.wind_conditions)

	if not patch_map:
		return

	# Remove all existing rows for patched fields; SQLAlchemy cascades the DELETE.
	patched_keys = set(patch_map.keys())
	audit.pre_submission_answers = [a for a in audit.pre_submission_answers if a.field_key not in patched_keys]

	# Insert replacement rows.
	for field_key, values in patch_map.items():
		for sort_order, selected_value in enumerate(values):
			audit.pre_submission_answers.append(
				PlayspacePreSubmissionAnswer(
					submission_id=audit.id,
					field_key=field_key,
					selected_value=selected_value,
					sort_order=sort_order,
				)
			)


def _upsert_section_normalized(
	audit: PlayspaceSubmission,
	section_key: str,
	section_patch: SectionDraftPatchRequest,
) -> None:
	"""Upsert one section and its questions/scale-answers in normalized tables."""

	# Find or create the section row.
	section_by_key = {s.section_key: s for s in audit.submission_sections or []}
	if section_key not in section_by_key:
		new_section = PlayspaceSubmissionSection(submission_id=audit.id, section_key=section_key)
		audit.submission_sections.append(new_section)
		section_by_key[section_key] = new_section
	section = section_by_key[section_key]

	if "note" in section_patch.model_fields_set:
		section.note = section_patch.note

	if not section_patch.responses:
		return

	# Upsert question response rows.
	qr_by_key = {qr.question_key: qr for qr in section.question_responses or []}
	for question_key, scale_answers in section_patch.responses.items():
		if question_key not in qr_by_key:
			new_qr = PlayspaceQuestionResponse(
				section_id=section.id,
				question_key=question_key,
			)
			section.question_responses.append(new_qr)
			qr_by_key[question_key] = new_qr
		qr = qr_by_key[question_key]

		# Upsert scale answer rows. Scale values are always plain strings;
		# coerce to be safe (QuestionResponseValue is a wider union type).
		sa_by_key = {sa.scale_key: sa for sa in qr.scale_answers or []}
		for scale_key, raw_option_key in scale_answers.items():
			option_key = str(raw_option_key) if raw_option_key is not None else ""
			if scale_key in sa_by_key:
				sa_by_key[scale_key].option_key = option_key
			else:
				new_sa = PlayspaceScaleAnswer(
					question_response_id=qr.id,
					scale_key=scale_key,
					option_key=option_key,
				)
				qr.scale_answers.append(new_sa)
				sa_by_key[scale_key] = new_sa


# ── JSONB helpers (fallback path) ─────────────────────────────────────────────


def _normalize_responses_payload(value: object) -> JSONDict:
	"""Coerce an arbitrary JSON value into the stable responses_json shape."""

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


def _merge_pre_audit_into_payload(*, payload: JSONDict, pre_audit_patch: PreAuditPatchRequest) -> None:
	"""Merge one partial pre-audit patch into the JSONB aggregate payload."""

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


def _merge_section_into_payload(
	*,
	payload: JSONDict,
	section_key: str,
	section_patch: SectionDraftPatchRequest,
) -> None:
	"""Merge one section patch into the JSONB aggregate payload."""

	sections = _read_json_dict(payload.get("sections"))
	next_section = dict(_read_json_dict(sections.get(section_key)))
	next_responses = dict(_read_json_dict(next_section.get("responses")))

	for question_key, scale_answers in section_patch.responses.items():
		next_responses[question_key] = dict(scale_answers)

	next_section["responses"] = next_responses
	if "note" in section_patch.model_fields_set:
		next_section["note"] = section_patch.note

	sections[section_key] = next_section
	payload["sections"] = sections


def _serialize_meta_request(meta: object) -> JSONDict:
	if not isinstance(meta, object) or meta is None:
		return {}
	execution_mode = getattr(meta, "execution_mode", None)
	if isinstance(execution_mode, str):
		return {"execution_mode": execution_mode}
	if execution_mode is not None and hasattr(execution_mode, "value"):
		return {"execution_mode": str(execution_mode.value)}
	return {}


def _serialize_pre_audit_request(pre_audit: object) -> JSONDict:
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


def _serialize_sections_request(sections: dict[str, SectionDraftPatchRequest]) -> JSONDict:
	return {
		section_key: {
			"note": section_state.note,
			"responses": {
				question_key: dict(scale_answers) for question_key, scale_answers in section_state.responses.items()
			},
		}
		for section_key, section_state in sections.items()
	}


# ── low-level primitives ──────────────────────────────────────────────────────


def _read_json_dict(value: object) -> JSONDict:
	return dict(value) if isinstance(value, dict) else {}


def _read_positive_int(value: object, *, default: int) -> int:
	return value if isinstance(value, int) and value >= 1 else default


def _read_non_negative_int(value: object, *, default: int) -> int:
	return value if isinstance(value, int) and value >= 0 else default

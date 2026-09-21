"""
Service layer for managing Audit Instruments.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import Instrument, PlayspaceSubmission
from app.products.playspace.audit_state import LEGACY_OPTION_KEY_ALIASES
from app.products.playspace.schemas.instrument import (
	ExecutionMode,
	InstrumentChoiceOptionResponse,
	InstrumentQuestionResponse,
	InstrumentQuestionScaleResponse,
	InstrumentQuestionType,
	InstrumentScaleDefinitionResponse,
	InstrumentScaleOptionResponse,
	InstrumentSectionResponse,
	PlayspaceInstrumentResponse,
	ScaleKey,
)
from app.products.playspace.schemas.management import (
	InstrumentActivateRequest,
	InstrumentCreateRequest,
)

DeleteInstrumentResult = Literal["deleted", "active", "in_use", "not_found"]
SOCIABILITY_MULTI_SELECT_PROMPT = "Does this feature/environmental characteristic provide opportunities for a child to"
SOCIABILITY_MULTI_SELECT_KEYS = ["play_alone", "small_group", "large_group"]
SOCIABILITY_MULTI_SELECT_FIRST_VERSION = (5, 32)

# Scalar answers persist the chosen option key in a String(80) column, so an
# authored key longer than this cannot be stored. List answers use JSONB, but
# share the limit so every answer in the instrument behaves the same way.
MAX_OPTION_KEY_LENGTH = 80

# The editor's starting key for a freshly added answer. It identifies a row the
# admin has not named yet, so it must never reach storage: several rows carrying
# it would collapse into one answer.
PLACEHOLDER_OPTION_KEYS: frozenset[str] = frozenset({"new_option"})

# Option keys that arriving audits are rewritten away from, so a newly authored
# answer must not claim one (see app/products/playspace/audit_state.py).
RESERVED_SCALE_OPTION_KEYS: frozenset[str] = frozenset(LEGACY_OPTION_KEY_ALIASES)

# Clients read a checklist answer from this field of the stored question payload,
# so a follow-up question gated on a checklist must name it as its response key.
CHECKLIST_CONDITION_RESPONSE_KEY = "selected_option_keys"


class InstrumentValidationError(ValueError):
	pass


def _parse_localized_instrument_content(
	content: dict[str, object],
) -> dict[str, PlayspaceInstrumentResponse]:
	localized_payloads: dict[str, object]
	if "instrument_key" in content:
		localized_payloads = {"en": content}
	else:
		localized_payloads = content
	if not localized_payloads:
		raise InstrumentValidationError("Instrument content must contain at least one locale payload.")

	parsed_by_locale: dict[str, PlayspaceInstrumentResponse] = {}
	for locale, raw_payload in localized_payloads.items():
		if not isinstance(raw_payload, dict):
			raise InstrumentValidationError(f"Instrument locale {locale!r} must be a JSON object.")
		try:
			parsed_by_locale[locale] = PlayspaceInstrumentResponse.model_validate(raw_payload)
		except ValidationError as exc:
			raise InstrumentValidationError(
				f"Instrument locale {locale!r} has an invalid base structure: {exc}"
			) from exc
	return parsed_by_locale


def _validate_sociability_scale(
	scale: InstrumentScaleDefinitionResponse | InstrumentQuestionScaleResponse,
	*,
	location: str,
) -> None:
	if scale.key is not ScaleKey.SOCIABILITY:
		return
	if scale.selection_mode != "multiple":
		raise InstrumentValidationError(f"{location} Sociability scale must declare selection_mode='multiple'.")
	if scale.prompt != SOCIABILITY_MULTI_SELECT_PROMPT:
		raise InstrumentValidationError(
			f"{location} multiple Sociability prompt must be {SOCIABILITY_MULTI_SELECT_PROMPT!r}."
		)

	option_keys = [option.key for option in scale.options]
	if option_keys != SOCIABILITY_MULTI_SELECT_KEYS:
		raise InstrumentValidationError(
			f"{location} multiple Sociability options must have the exact ordered keys "
			f"{SOCIABILITY_MULTI_SELECT_KEYS!r}."
		)
	for option in scale.options:
		if option.addition_value != 1:
			raise InstrumentValidationError(
				f"{location} multiple Sociability option {option.key!r} must have addition_value=1."
			)
		if option.boost_value != 1:
			raise InstrumentValidationError(
				f"{location} multiple Sociability option {option.key!r} must have boost_value=1."
			)
		if option.is_unsure:
			raise InstrumentValidationError(f"{location} multiple Sociability options cannot include Unsure.")
		if option.is_not_applicable:
			raise InstrumentValidationError(
				f"{location} multiple Sociability options cannot include a not-applicable option."
			)


def _visible_execution_modes(mode: ExecutionMode) -> frozenset[str]:
	"""Return the workflows a question is shown in, so parent/child visibility can be compared."""

	if mode is ExecutionMode.BOTH:
		return frozenset({ExecutionMode.AUDIT.value, ExecutionMode.SURVEY.value})
	return frozenset({mode.value})


def _validate_owner_keys(keys: list[str], *, location: str, label: str) -> None:
	"""Reject blank or repeated identities in one list of sibling owners.

	Owners are validated before their option lists: a duplicated question or
	scale makes every option error below it ambiguous to report and to repair.
	"""

	seen: dict[str, int] = {}
	for index, key in enumerate(keys, start=1):
		if not key.strip():
			raise InstrumentValidationError(f"{location} {label} {index} needs a key.")
		first_index = seen.get(key)
		if first_index is not None:
			raise InstrumentValidationError(
				f"{location} {label} {index} repeats the key {key!r} already used by {label.lower()} {first_index}."
			)
		seen[key] = index


def _validate_option_keys(
	keys: list[str],
	*,
	location: str,
	reject_alias_sources: bool,
) -> None:
	"""Reject option identities that cannot address one answer.

	Existing punctuation and non-ASCII characters stay valid - imported content is
	compared exactly and never trimmed, slugified, or case-folded. What is rejected
	is an identity that cannot be stored or told apart from a sibling: blank,
	surrounded by spaces, longer than the answer column, repeated within the same
	list, left on the editor placeholder, or reserved for reading older app builds.
	"""

	seen: dict[str, int] = {}
	for index, key in enumerate(keys, start=1):
		if not key.strip():
			raise InstrumentValidationError(f"{location} option {index} needs a key.")
		if key != key.strip():
			raise InstrumentValidationError(
				f"{location} option {index} key {key!r} starts or ends with a space. Remove the spaces."
			)
		if len(key) > MAX_OPTION_KEY_LENGTH:
			raise InstrumentValidationError(
				f"{location} option {index} key {key!r} is {len(key)} characters long; "
				f"the limit is {MAX_OPTION_KEY_LENGTH}."
			)
		if key in PLACEHOLDER_OPTION_KEYS:
			raise InstrumentValidationError(
				f"{location} option {index} still uses the placeholder key {key!r}. Every answer needs its own key."
			)
		if reject_alias_sources and key in RESERVED_SCALE_OPTION_KEYS:
			raise InstrumentValidationError(
				f"{location} option {index} key {key!r} is reserved: audits synced from older app builds "
				f"are rewritten to {LEGACY_OPTION_KEY_ALIASES[key]!r} on arrival. Choose a different key."
			)
		first_index = seen.get(key)
		if first_index is not None:
			raise InstrumentValidationError(
				f"{location} option {index} repeats the key {key!r} already used by option {first_index}. "
				f"Two answers that share a key cannot be told apart in a report."
			)
		seen[key] = index


def _validate_instrument_identities(locale: str, instrument: PlayspaceInstrumentResponse) -> None:
	"""Check that every stored answer in this locale can be addressed by exactly one option."""

	where = f"Instrument locale {locale!r}"
	questions = [(section, question) for section in instrument.sections for question in section.questions]

	_validate_owner_keys([section.section_key for section in instrument.sections], location=where, label="Section")
	_validate_owner_keys([question.question_key for _, question in questions], location=where, label="Question")
	_validate_owner_keys(
		[question.key for question in instrument.pre_audit_questions],
		location=where,
		label="Pre-audit question",
	)
	_validate_owner_keys(
		[guidance.key.value for guidance in instrument.scale_guidance],
		location=where,
		label="Scale guidance",
	)
	for _, question in questions:
		_validate_owner_keys(
			[scale.key.value for scale in question.scales],
			location=f"{where} question {question.question_key!r}",
			label="Scale",
		)

	for guidance in instrument.scale_guidance:
		_validate_option_keys(
			[option.key for option in guidance.options],
			location=f"{where} scale guidance {guidance.key.value!r}",
			reject_alias_sources=True,
		)
	for _, question in questions:
		for scale in question.scales:
			_validate_option_keys(
				[option.key for option in scale.options],
				location=f"{where} question {question.question_key!r} scale {scale.key.value!r}",
				reject_alias_sources=True,
			)
		if question.options:
			_validate_option_keys(
				[option.key for option in question.options],
				location=f"{where} question {question.question_key!r} checklist",
				reject_alias_sources=False,
			)
	for question in instrument.pre_audit_questions:
		if question.options:
			_validate_option_keys(
				[option.key for option in question.options],
				location=f"{where} pre-audit question {question.key!r}",
				reject_alias_sources=False,
			)


def _resolve_condition_option_keys(
	parent: InstrumentQuestionResponse,
	response_key: str,
	*,
	location: str,
) -> set[str]:
	"""Return the answers a condition may name, following how clients read a parent answer."""

	if parent.question_type is InstrumentQuestionType.CHECKLIST:
		if response_key != CHECKLIST_CONDITION_RESPONSE_KEY:
			raise InstrumentValidationError(
				f"{location} reads {response_key!r} from checklist question {parent.question_key!r}, "
				f"which answers under {CHECKLIST_CONDITION_RESPONSE_KEY!r}."
			)
		return {option.key for option in parent.options}

	scale = next((scale for scale in parent.scales if scale.key.value == response_key), None)
	if scale is None:
		available = ", ".join(sorted(scale.key.value for scale in parent.scales)) or "none"
		raise InstrumentValidationError(
			f"{location} reads {response_key!r} from question {parent.question_key!r}, "
			f"which has no such scale (available: {available})."
		)
	return {option.key for option in scale.options}


def _validate_section_conditions(locale: str, section: InstrumentSectionResponse) -> None:
	"""Check every follow-up question in one section against the answer it depends on."""

	where = f"Instrument locale {locale!r} section {section.section_key!r}"
	questions_by_key = {question.question_key: question for question in section.questions}
	parent_of: dict[str, str] = {}

	for question in section.questions:
		condition = question.display_if
		if condition is None:
			continue
		location = f"{where} question {question.question_key!r} display condition"

		if not condition.question_key.strip():
			raise InstrumentValidationError(f"{location} must name the question it depends on.")
		if condition.question_key == question.question_key:
			raise InstrumentValidationError(f"{location} cannot depend on the same question.")

		parent = questions_by_key.get(condition.question_key)
		if parent is None:
			raise InstrumentValidationError(
				f"{location} references {condition.question_key!r}, which is not in this section. "
				f"A question can only depend on an answer from its own section."
			)

		option_keys = _resolve_condition_option_keys(parent, condition.response_key, location=location)
		if not condition.any_of_option_keys:
			raise InstrumentValidationError(f"{location} must list at least one answer that reveals this question.")
		unknown = [key for key in condition.any_of_option_keys if key not in option_keys]
		if unknown:
			raise InstrumentValidationError(
				f"{location} names {', '.join(repr(key) for key in unknown)}, "
				f"which question {parent.question_key!r} no longer offers."
			)

		if not _visible_execution_modes(question.mode) <= _visible_execution_modes(parent.mode):
			raise InstrumentValidationError(
				f"{location} depends on question {parent.question_key!r}, which is not shown in every workflow "
				f"this question appears in ({question.mode.value} versus {parent.mode.value})."
			)

		parent_of[question.question_key] = condition.question_key

	for start in parent_of:
		seen = {start}
		current = parent_of[start]
		while current in parent_of:
			if current in seen:
				raise InstrumentValidationError(
					f"{where} has questions that depend on each other in a loop, starting at {start!r}."
				)
			seen.add(current)
			current = parent_of[current]


def _validate_publish_readiness(parsed_by_locale: dict[str, PlayspaceInstrumentResponse]) -> None:
	"""Check what must be finished before auditors see this instrument.

	Identity integrity is required for every save; the rules here are the ones a
	draft is allowed to be part-way through - usable labels, follow-up questions
	pointing at answers that exist, and translations that match the base structure.
	"""

	for locale, instrument in parsed_by_locale.items():
		where = f"Instrument locale {locale!r}"
		for guidance in instrument.scale_guidance:
			_require_option_labels(guidance.options, location=f"{where} scale guidance {guidance.key.value!r}")
		for section in instrument.sections:
			for question in section.questions:
				for scale in question.scales:
					_require_option_labels(
						scale.options,
						location=f"{where} question {question.question_key!r} scale {scale.key.value!r}",
					)
				_require_option_labels(
					question.options,
					location=f"{where} question {question.question_key!r} checklist",
				)
			_validate_section_conditions(locale, section)
		for question in instrument.pre_audit_questions:
			_require_option_labels(question.options, location=f"{where} pre-audit question {question.key!r}")

	_validate_locale_alignment(parsed_by_locale)


def _require_option_labels(
	options: list[InstrumentChoiceOptionResponse] | list[InstrumentScaleOptionResponse],
	*,
	location: str,
) -> None:
	"""Reject answers an auditor would see as an empty row."""

	for index, option in enumerate(options, start=1):
		if not option.label.strip():
			raise InstrumentValidationError(
				f"{location} option {index} ({option.key!r}) needs a label before auditors can choose it."
			)


def _option_key_fingerprint(instrument: PlayspaceInstrumentResponse) -> dict[str, list[str]]:
	"""Describe one locale's answer structure as owner path -> ordered option keys."""

	fingerprint: dict[str, list[str]] = {}
	for guidance in instrument.scale_guidance:
		fingerprint[f"scale_guidance/{guidance.key.value}"] = [option.key for option in guidance.options]
	for section in instrument.sections:
		for question in section.questions:
			for scale in question.scales:
				fingerprint[f"{section.section_key}/{question.question_key}/{scale.key.value}"] = [
					option.key for option in scale.options
				]
			if question.options:
				fingerprint[f"{section.section_key}/{question.question_key}/checklist"] = [
					option.key for option in question.options
				]
	for question in instrument.pre_audit_questions:
		if question.options:
			fingerprint[f"pre_audit/{question.key}"] = [option.key for option in question.options]
	return fingerprint


def _validate_locale_alignment(parsed_by_locale: dict[str, PlayspaceInstrumentResponse]) -> None:
	"""Require every translation to answer with the same keys, in the same order, as the base.

	Answers are stored by key, so a translation that drops, adds, or reorders an
	option would score differently from the language it was written in.
	"""

	if len(parsed_by_locale) < 2:
		return

	base_locale = "en" if "en" in parsed_by_locale else next(iter(parsed_by_locale))
	base_fingerprint = _option_key_fingerprint(parsed_by_locale[base_locale])
	for locale, instrument in parsed_by_locale.items():
		if locale == base_locale:
			continue
		fingerprint = _option_key_fingerprint(instrument)
		missing = sorted(set(base_fingerprint) - set(fingerprint))
		if missing:
			raise InstrumentValidationError(
				f"Instrument locale {locale!r} is missing answer lists that {base_locale!r} defines: "
				f"{', '.join(missing[:5])}."
			)
		extra = sorted(set(fingerprint) - set(base_fingerprint))
		if extra:
			raise InstrumentValidationError(
				f"Instrument locale {locale!r} defines answer lists that {base_locale!r} does not: "
				f"{', '.join(extra[:5])}."
			)
		for owner, base_keys in base_fingerprint.items():
			if fingerprint[owner] != base_keys:
				raise InstrumentValidationError(
					f"Instrument locale {locale!r} answer list {owner!r} does not match {base_locale!r}. "
					f"Translations share the base language's option keys and order."
				)


def validate_instrument_content(
	content: dict[str, object],
	*,
	strict_sociability: bool,
	expected_instrument_key: str | None = None,
	expected_instrument_version: str | None = None,
	sociability_semantics_version: str | None = None,
	allow_legacy_nonnumeric: bool = False,
	publish_checks: bool | None = None,
) -> dict[str, PlayspaceInstrumentResponse]:
	"""Validate candidate instrument content before it is stored or activated.

	Identity integrity - every answer addressable by exactly one key - is checked
	on every write, because a draft saved with colliding keys is already losing
	information. Publish checks cover what a draft is allowed to be part-way
	through and default to running whenever the candidate is being activated.
	"""

	parsed_by_locale = _parse_localized_instrument_content(content)
	run_publish_checks = strict_sociability if publish_checks is None else publish_checks
	for locale, instrument in parsed_by_locale.items():
		_validate_instrument_identities(locale, instrument)
	if run_publish_checks:
		_validate_publish_readiness(parsed_by_locale)
	for locale, instrument in parsed_by_locale.items():
		if expected_instrument_key is not None and instrument.instrument_key != expected_instrument_key:
			raise InstrumentValidationError(
				f"Instrument locale {locale!r} key {instrument.instrument_key!r} does not match "
				f"{expected_instrument_key!r}."
			)
		if expected_instrument_version is not None and instrument.instrument_version != expected_instrument_version:
			raise InstrumentValidationError(
				f"Instrument locale {locale!r} version {instrument.instrument_version!r} does not match "
				f"{expected_instrument_version!r}."
			)
		if not strict_sociability:
			continue

		sociability_guidance = [
			guidance for guidance in instrument.scale_guidance if guidance.key is ScaleKey.SOCIABILITY
		]
		if len(sociability_guidance) != 1:
			raise InstrumentValidationError(
				f"Instrument locale {locale!r} must define exactly one Sociability scale guidance block."
			)

		assigned_sociability_scales = [
			(question.question_key, scale)
			for section in instrument.sections
			for question in section.questions
			for scale in question.scales
			if scale.key is ScaleKey.SOCIABILITY
		]
		if not assigned_sociability_scales:
			raise InstrumentValidationError(
				f"Instrument locale {locale!r} must define at least one assigned Sociability scale."
			)

		semantic_version = sociability_semantics_version or instrument.instrument_version
		parsed_semantic_version = _parse_numeric_version(semantic_version)
		all_sociability_scales: list[InstrumentScaleDefinitionResponse | InstrumentQuestionScaleResponse] = [
			sociability_guidance[0],
		]
		all_sociability_scales.extend(scale for _, scale in assigned_sociability_scales)
		requires_multi_select = (
			parsed_semantic_version >= SOCIABILITY_MULTI_SELECT_FIRST_VERSION
			if parsed_semantic_version is not None
			else not allow_legacy_nonnumeric
		)
		requires_multi_select = requires_multi_select or any(
			scale.selection_mode == "multiple" for scale in all_sociability_scales
		)
		if not requires_multi_select:
			continue

		_validate_sociability_scale(
			sociability_guidance[0],
			location=f"Instrument locale {locale!r} scale guidance",
		)
		for question_key, scale in assigned_sociability_scales:
			_validate_sociability_scale(
				scale,
				location=f"Instrument locale {locale!r} question {question_key!r}",
			)
	return parsed_by_locale


def next_draft_version(parent_version: str, existing_versions: Iterable[str]) -> str:
	"""Return the next draft sub-version for a parent (e.g. 5.23 -> 5.23.1)."""

	prefix = f"{parent_version}."
	max_suffix = 0
	for version in existing_versions:
		if not version.startswith(prefix):
			continue
		suffix = version[len(prefix) :]
		if suffix.isdigit():
			max_suffix = max(max_suffix, int(suffix))
	return f"{parent_version}.{max_suffix + 1}"


def _parse_numeric_version(version: str) -> tuple[int, ...] | None:
	"""Parse a dotted numeric version into comparable integer segments, or None."""

	parts = version.split(".")
	if not parts or not all(part.isdigit() for part in parts):
		return None
	return tuple(int(part) for part in parts)


def next_published_version(published_versions: Iterable[str]) -> str:
	"""Return the next publication number: one above the highest existing publication.

	Publication numbers stay monotonic and collision-free because they are derived
	from the largest existing publication rather than from whichever version happens
	to be active. Reactivating an older version (a rollback) must not let the next
	publication reuse a number that already exists.
	"""

	highest: tuple[int, ...] | None = None
	for version in published_versions:
		parsed = _parse_numeric_version(version)
		if parsed is None:
			continue
		if highest is None or parsed > highest:
			highest = parsed
	if highest is None:
		return "1.0"
	return ".".join(str(segment) for segment in (*highest[:-1], highest[-1] + 1))


def can_delete_instrument_version(
	*,
	is_active: bool,
	parent_instrument_id: UUID | None,
	submission_count: int,
) -> bool:
	"""Draft branches can always be deleted; inactive published rows need zero submissions."""

	if is_active:
		return False
	if parent_instrument_id is not None:
		return True
	return submission_count == 0


def sync_instrument_version_in_content(content: dict[str, object], instrument_version: str) -> dict[str, object]:
	"""Ensure every localized payload carries the authoritative version string."""

	updated_content: dict[str, object] = {}
	for lang, payload in content.items():
		if isinstance(payload, dict):
			localized = dict(payload)
			localized["instrument_version"] = instrument_version
			updated_content[lang] = localized
		else:
			updated_content[lang] = payload
	return updated_content


async def get_active_instrument(
	session: AsyncSession,
	instrument_key: str = "pvua_v5_2",
) -> Instrument | None:
	"""Fetch the currently active version of an instrument."""

	stmt = (
		select(Instrument)
		.where(Instrument.instrument_key == instrument_key)
		.where(Instrument.is_active.is_(True))
		.order_by(Instrument.created_at.desc())
		.limit(1)
	)
	result = await session.execute(stmt)
	return result.scalar_one_or_none()


def build_instrument_response_from_row(
	instrument: Instrument,
	*,
	lang: str = "en",
) -> PlayspaceInstrumentResponse | None:
	"""Build a client instrument response using database row metadata as authoritative."""

	localized = instrument.content.get(lang) or instrument.content.get("en")
	if not isinstance(localized, dict):
		return None

	payload = dict(localized)
	payload["instrument_key"] = instrument.instrument_key
	payload["instrument_version"] = instrument.instrument_version
	return PlayspaceInstrumentResponse.model_validate(payload)


async def get_instrument_version(
	session: AsyncSession,
	instrument_key: str,
	instrument_version: str,
) -> Instrument | None:
	"""Fetch one specific instrument version for immutable submission rendering."""

	stmt = (
		select(Instrument)
		.where(Instrument.instrument_key == instrument_key)
		.where(Instrument.instrument_version == instrument_version)
		.order_by(Instrument.is_active.desc(), Instrument.updated_at.desc())
		.limit(1)
	)
	result = await session.execute(stmt)
	return result.scalar_one_or_none()


async def get_instrument_by_id(
	session: AsyncSession,
	instrument_id: UUID,
) -> Instrument | None:
	"""Fetch a specific instrument version by its ID."""

	stmt = select(Instrument).where(Instrument.id == instrument_id)
	result = await session.execute(stmt)
	return result.scalar_one_or_none()


async def get_submission_counts_by_version(
	session: AsyncSession,
	instrument_key: str,
) -> dict[str, int]:
	"""Count Playspace submissions stamped with each instrument version."""

	stmt = (
		select(PlayspaceSubmission.instrument_version, func.count())
		.where(PlayspaceSubmission.instrument_key == instrument_key)
		.where(PlayspaceSubmission.instrument_version.is_not(None))
		.group_by(PlayspaceSubmission.instrument_version)
	)
	result = await session.execute(stmt)
	return {version: count for version, count in result.all() if version is not None}


async def list_instrument_versions(
	session: AsyncSession,
	instrument_key: str = "pvua_v5_2",
) -> list[Instrument]:
	"""List all versions of a specific instrument, ordered by creation date."""

	stmt = select(Instrument).where(Instrument.instrument_key == instrument_key).order_by(Instrument.created_at.desc())
	result = await session.execute(stmt)
	return list(result.scalars().all())


async def create_instrument_version(
	session: AsyncSession,
	data: InstrumentCreateRequest,
	activate: bool = True,
) -> Instrument | None:
	"""
	Create a new instrument version.

	When *activate* is True, all other versions for the same key are
	deactivated in the same transaction and the version number is bumped
	to one above the highest existing publication.

	When *activate* is False and a parent is provided, the server assigns
	the next draft sub-version for that parent.
	"""

	parent_instrument: Instrument | None = None
	if data.parent_instrument_id is not None:
		parent_instrument = await get_instrument_by_id(session, data.parent_instrument_id)
		if parent_instrument is None or parent_instrument.instrument_key != data.instrument_key:
			return None

	existing_rows = await list_instrument_versions(session, data.instrument_key)
	existing_versions = [row.instrument_version for row in existing_rows]
	resolved_version = data.instrument_version

	if activate:
		published_versions = [row.instrument_version for row in existing_rows if row.parent_instrument_id is None]
		if published_versions:
			resolved_version = next_published_version(published_versions)
	elif parent_instrument is not None:
		resolved_version = next_draft_version(parent_instrument.instrument_version, existing_versions)

	if parent_instrument is None and _parse_numeric_version(resolved_version) is None:
		raise InstrumentValidationError("Root instrument versions must be numeric.")
	if activate and _parse_numeric_version(resolved_version) is None:
		raise InstrumentValidationError("New published instruments must use a numeric version.")

	resolved_content = sync_instrument_version_in_content(data.content, resolved_version)
	validate_instrument_content(
		resolved_content,
		strict_sociability=activate,
		expected_instrument_key=data.instrument_key,
		expected_instrument_version=resolved_version,
	)

	if activate:
		await session.execute(
			update(Instrument)
			.where(Instrument.instrument_key == data.instrument_key)
			.values(is_active=False, updated_at=datetime.now(timezone.utc))
		)

	new_instrument = Instrument(
		instrument_key=data.instrument_key,
		instrument_version=resolved_version,
		parent_instrument_id=None if activate else parent_instrument.id if parent_instrument is not None else None,
		is_active=activate,
		content=resolved_content,
	)

	session.add(new_instrument)
	await session.commit()
	await session.refresh(new_instrument)
	return new_instrument


async def update_instrument_status(
	session: AsyncSession,
	instrument_id: UUID,
	data: InstrumentActivateRequest,
) -> Instrument | None:
	"""Toggle the active flag on a specific instrument version."""

	instrument = await get_instrument_by_id(session, instrument_id)
	if instrument is None:
		return None

	if data.is_active:
		published_version: str | None = None
		if instrument.parent_instrument_id is not None:
			published_versions = [
				row.instrument_version
				for row in await list_instrument_versions(session, instrument.instrument_key)
				if row.parent_instrument_id is None
			]
			if published_versions:
				published_version = next_published_version(published_versions)

		validate_instrument_content(
			instrument.content,
			strict_sociability=True,
			expected_instrument_key=instrument.instrument_key,
			expected_instrument_version=instrument.instrument_version,
			sociability_semantics_version=published_version or instrument.instrument_version,
			allow_legacy_nonnumeric=instrument.parent_instrument_id is None,
		)
		# Promoting a draft (a branch with a parent) mints a fresh publication number
		# one above the highest existing publication. Reactivating an existing
		# publication - a rollback - keeps its original number unchanged.
		if instrument.parent_instrument_id is not None:
			if published_version is not None:
				instrument.instrument_version = published_version
				instrument.content = sync_instrument_version_in_content(
					instrument.content, instrument.instrument_version
				)
				flag_modified(instrument, "content")

		await session.execute(
			update(Instrument)
			.where(Instrument.instrument_key == instrument.instrument_key)
			.values(is_active=False, updated_at=datetime.now(timezone.utc))
		)
		instrument.parent_instrument_id = None

	instrument.is_active = data.is_active
	instrument.updated_at = datetime.now(timezone.utc)
	await session.commit()
	await session.refresh(instrument)
	return instrument


async def delete_instrument_version(
	session: AsyncSession,
	instrument_id: UUID,
) -> DeleteInstrumentResult:
	"""Delete an inactive instrument version when policy allows."""

	instrument = await get_instrument_by_id(session, instrument_id)
	if instrument is None:
		return "not_found"

	if instrument.is_active:
		return "active"

	if instrument.parent_instrument_id is None:
		submission_count = await count_submissions_for_instrument_version(
			session,
			instrument.instrument_key,
			instrument.instrument_version,
		)
		if submission_count > 0:
			return "in_use"

	await session.delete(instrument)
	await session.commit()
	return "deleted"


async def count_submissions_for_instrument_version(
	session: AsyncSession,
	instrument_key: str,
	instrument_version: str,
) -> int:
	"""Return how many Playspace submissions reference an instrument version."""

	stmt = (
		select(func.count())
		.select_from(PlayspaceSubmission)
		.where(PlayspaceSubmission.instrument_key == instrument_key)
		.where(PlayspaceSubmission.instrument_version == instrument_version)
	)
	result = await session.execute(stmt)
	return int(result.scalar_one())

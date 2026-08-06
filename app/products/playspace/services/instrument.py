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
from app.products.playspace.schemas.instrument import (
	InstrumentQuestionScaleResponse,
	InstrumentScaleDefinitionResponse,
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


def validate_instrument_content(
	content: dict[str, object],
	*,
	strict_sociability: bool,
	expected_instrument_key: str | None = None,
	expected_instrument_version: str | None = None,
	sociability_semantics_version: str | None = None,
	allow_legacy_nonnumeric: bool = False,
) -> dict[str, PlayspaceInstrumentResponse]:
	parsed_by_locale = _parse_localized_instrument_content(content)
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

"""
Service layer for managing Audit Instruments.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.models import Instrument, PlayspaceSubmission
from app.products.playspace.schemas.instrument import PlayspaceInstrumentResponse
from app.products.playspace.schemas.management import (
	InstrumentActivateRequest,
	InstrumentCreateRequest,
)

DeleteInstrumentResult = Literal["deleted", "active", "in_use", "not_found"]


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

	resolved_content = sync_instrument_version_in_content(data.content, resolved_version)

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
		# Promoting a draft (a branch with a parent) mints a fresh publication number
		# one above the highest existing publication. Reactivating an existing
		# publication — a rollback — keeps its original number unchanged.
		if instrument.parent_instrument_id is not None:
			published_versions = [
				row.instrument_version
				for row in await list_instrument_versions(session, instrument.instrument_key)
				if row.parent_instrument_id is None
			]
			if published_versions:
				instrument.instrument_version = next_published_version(published_versions)
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

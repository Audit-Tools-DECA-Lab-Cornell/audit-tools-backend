"""
Service layer for managing Audit Instruments.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument
from app.products.playspace.schemas.management import (
	InstrumentActivateRequest,
	InstrumentCreateRequest,
)


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


async def get_instrument_by_id(
	session: AsyncSession,
	instrument_id: UUID,
) -> Instrument | None:
	"""Fetch a specific instrument version by its ID."""

	stmt = select(Instrument).where(Instrument.id == instrument_id)
	result = await session.execute(stmt)
	return result.scalar_one_or_none()


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
) -> Instrument:
	"""
	Create a new instrument version.

	When *activate* is True, all other versions for the same key are
	deactivated in the same transaction.
	"""

	if activate:
		await session.execute(
			update(Instrument)
			.where(Instrument.instrument_key == data.instrument_key)
			.values(is_active=False, updated_at=datetime.now(timezone.utc))
		)

	new_instrument = Instrument(
		instrument_key=data.instrument_key,
		instrument_version=data.instrument_version,
		is_active=activate,
		content=data.content,
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
		await session.execute(
			update(Instrument)
			.where(Instrument.instrument_key == instrument.instrument_key)
			.values(is_active=False, updated_at=datetime.now(timezone.utc))
		)

	instrument.is_active = data.is_active
	instrument.updated_at = datetime.now(timezone.utc)
	await session.commit()
	await session.refresh(instrument)
	return instrument

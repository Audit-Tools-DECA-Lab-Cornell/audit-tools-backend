"""YEE instrument service.

Active-instrument lookup, bootstrap, version create/activate/delete, content
normalization, and site-copy handling.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy import update as sqlalchemy_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccountType, Instrument, User
from app.products.yee.schemas.instrument import (
	YeeInstrumentActivateRequest,
	YeeInstrumentCreateRequest,
)
from app.products.yee.services.scoring import get_yee_instrument_data
from app.yee_instrument_schema import YeeInstrumentResponse


def _require_admin(user: User) -> None:
	if user.account_type != AccountType.ADMIN:
		raise HTTPException(status_code=403, detail="Admin access is required.")


async def _get_active_yee_instrument(session: AsyncSession, instrument_key: str = "yee") -> Instrument | None:
	stmt = (
		select(Instrument)
		.where(Instrument.instrument_key == instrument_key)
		.where(Instrument.is_active.is_(True))
		.order_by(Instrument.created_at.desc())
		.limit(1)
	)
	return (await session.execute(stmt)).scalar_one_or_none()


async def _bootstrap_yee_instrument_if_missing(session: AsyncSession, instrument_key: str = "yee") -> Instrument | None:
	existing = await _get_active_yee_instrument(session, instrument_key)
	if existing is not None:
		return existing

	any_existing = (
		await session.execute(
			select(Instrument)
			.where(Instrument.instrument_key == instrument_key)
			.order_by(Instrument.created_at.desc())
			.limit(1)
		)
	).scalar_one_or_none()
	if any_existing is not None:
		return any_existing

	canonical = YeeInstrumentResponse.model_validate(get_yee_instrument_data()).model_dump()
	row = Instrument(
		instrument_key=instrument_key,
		instrument_version=str(canonical.get("version", "1")),
		is_active=True,
		content=canonical,
	)
	session.add(row)
	await session.commit()
	await session.refresh(row)
	return row


async def _list_yee_instrument_versions(session: AsyncSession, instrument_key: str = "yee") -> list[Instrument]:
	stmt = select(Instrument).where(Instrument.instrument_key == instrument_key).order_by(Instrument.created_at.desc())
	return list((await session.execute(stmt)).scalars().all())


async def _get_yee_instrument_by_id(session: AsyncSession, instrument_id: uuid.UUID) -> Instrument | None:
	return (await session.execute(select(Instrument).where(Instrument.id == instrument_id))).scalar_one_or_none()


def _normalize_yee_instrument_content(raw_content: Any) -> dict[str, Any]:
	canonical = YeeInstrumentResponse.model_validate(get_yee_instrument_data()).model_dump()
	if not isinstance(raw_content, dict):
		return canonical
	merged = {**canonical, **raw_content}
	return YeeInstrumentResponse.model_validate(merged).model_dump()


async def _create_yee_instrument_version(
	session: AsyncSession,
	data: YeeInstrumentCreateRequest,
	activate: bool = True,
) -> Instrument:
	if activate:
		await session.execute(
			sqlalchemy_update(Instrument)
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


async def _update_yee_instrument_status(
	session: AsyncSession,
	instrument_id: uuid.UUID,
	data: YeeInstrumentActivateRequest,
) -> Instrument | None:
	instrument = await _get_yee_instrument_by_id(session, instrument_id)
	if instrument is None:
		return None

	if data.is_active:
		await session.execute(
			sqlalchemy_update(Instrument)
			.where(Instrument.instrument_key == instrument.instrument_key)
			.values(is_active=False, updated_at=datetime.now(timezone.utc))
		)

	instrument.is_active = data.is_active
	instrument.updated_at = datetime.now(timezone.utc)
	await session.commit()
	await session.refresh(instrument)
	return instrument


async def _delete_yee_instrument_version(session: AsyncSession, instrument_id: uuid.UUID) -> Instrument | None:
	instrument = await _get_yee_instrument_by_id(session, instrument_id)
	if instrument is None:
		return None
	if instrument.is_active:
		raise HTTPException(status_code=400, detail="The active instrument version cannot be deleted.")
	await session.delete(instrument)
	await session.commit()
	return instrument

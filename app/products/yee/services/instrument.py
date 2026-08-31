"""YEE instrument service.

Active-instrument lookup, bootstrap, version create/activate/delete, content
normalization, and site-copy handling.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, or_, select
from sqlalchemy import update as sqlalchemy_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccountType, Audit, AuditStatus, Instrument, User, YeeAuditSubmission
from app.products.yee.schemas.instrument import (
	YeeInstrumentActivateRequest,
	YeeInstrumentCreateRequest,
)
from app.products.yee.services.instrument_activation import validated_activation_content
from app.products.yee.services.scoring import get_yee_instrument_data
from app.products.yee.services.scoring_contract import validate_scoring_compatibility
from app.yee_instrument_schema import YeeInstrumentResponse


def _require_admin(user: User) -> None:
	if user.account_type != AccountType.ADMIN:
		raise HTTPException(status_code=403, detail="Admin access is required.")


def _ensure_scoring_compatible(content: Any) -> None:
	report = validate_scoring_compatibility(content if isinstance(content, dict) else {})
	if not report.ok:
		raise HTTPException(
			status_code=409,
			detail={
				"message": (
					"This version is missing questions the scoring needs, so it can't be published. "
					"Restore the missing questions before publishing."
				),
				"scoring_compatibility": report.model_dump(),
			},
		)


async def _get_active_yee_instrument(session: AsyncSession, instrument_key: str = "yee") -> Instrument | None:
	stmt = (
		select(Instrument)
		.where(Instrument.instrument_key == instrument_key)
		.where(Instrument.is_active.is_(True))
		.order_by(Instrument.created_at.desc())
	)
	if instrument_key != "yee":
		return (await session.execute(stmt.limit(1))).scalar_one_or_none()
	rows = list((await session.execute(stmt)).scalars().all())
	if len(rows) > 1:
		raise HTTPException(
			status_code=409,
			detail={
				"code": "multiple_active_instruments",
				"instrument_key": "yee",
				"conflicts": [{"id": str(row.id), "instrument_version": row.instrument_version} for row in rows],
			},
		)
	return rows[0] if rows else None


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


async def _get_yee_instrument_by_stamp(
	session: AsyncSession,
	instrument_key: str,
	instrument_version: str,
) -> Instrument | None:
	rows = list(
		(
			await session.execute(
				select(Instrument).where(
					Instrument.instrument_key == instrument_key,
					Instrument.instrument_version == instrument_version,
				)
			)
		)
		.scalars()
		.all()
	)
	if len(rows) > 1:
		raise HTTPException(
			status_code=409,
			detail={
				"code": "duplicate_stamped_instrument",
				"instrument_key": instrument_key,
				"instrument_version": instrument_version,
			},
		)
	return rows[0] if rows else None


def _normalize_yee_instrument_content(raw_content: Any) -> dict[str, Any]:
	"""Return the stored instrument content, validated against the response schema.

	The database row is authoritative: what an admin publishes is exactly what
	clients receive. We validate the row against ``YeeInstrumentResponse`` (which
	fills defaults for any optional keys) but never overlay the canonical snapshot
	on top. The old overlay masked edits — a canonical key the row didn't carry
	would silently win, so editing the source alone appeared to do nothing. The
	snapshot is now only a fallback when the row is missing or cannot be validated.
	"""

	if isinstance(raw_content, dict):
		try:
			return YeeInstrumentResponse.model_validate(raw_content).model_dump()
		except ValidationError:
			pass
	return YeeInstrumentResponse.model_validate(get_yee_instrument_data()).model_dump()


async def _create_yee_instrument_version(
	session: AsyncSession,
	data: YeeInstrumentCreateRequest,
	activate: bool | None = None,
	*,
	force: bool = False,
) -> Instrument:
	should_activate = data.instrument_key != "yee" if activate is None else activate
	if force and data.instrument_key == "yee":
		raise HTTPException(
			status_code=409,
			detail={"code": "force_activation_not_allowed", "instrument_key": "yee"},
		)
	if should_activate and data.instrument_key == "yee":
		await _get_active_yee_instrument(session, "yee")
	if data.instrument_key == "yee":
		conflict = (
			await session.execute(
				select(Instrument.id, Instrument.instrument_version)
				.where(Instrument.instrument_key == "yee")
				.where(func.lower(Instrument.instrument_version) == data.instrument_version.strip().casefold())
				.limit(1)
			)
		).first()
		if conflict is not None:
			raise HTTPException(
				status_code=409,
				detail={
					"code": "instrument_version_conflict",
					"message": "An instrument version already uses this label.",
					"instrument_version": conflict.instrument_version,
				},
			)
	content = data.content
	if should_activate and data.instrument_key == "yee":
		_ensure_scoring_compatible(data.content)
		content = await validated_activation_content(
			session,
			data.content,
			data.parent_instrument_id,
		)
	if should_activate:
		await session.execute(
			sqlalchemy_update(Instrument)
			.where(Instrument.instrument_key == data.instrument_key)
			.values(is_active=False, updated_at=datetime.now(timezone.utc))
		)

	new_instrument = Instrument(
		instrument_key=data.instrument_key,
		instrument_version=data.instrument_version.strip(),
		parent_instrument_id=data.parent_instrument_id,
		is_active=should_activate,
		content=content,
	)
	session.add(new_instrument)
	await session.commit()
	await session.refresh(new_instrument)
	return new_instrument


async def _update_yee_instrument_status(
	session: AsyncSession,
	instrument_id: uuid.UUID,
	data: YeeInstrumentActivateRequest,
	*,
	force: bool = False,
) -> Instrument | None:
	instrument = await _get_yee_instrument_by_id(session, instrument_id)
	if instrument is None:
		return None
	if force and instrument.instrument_key == "yee":
		raise HTTPException(
			status_code=409,
			detail={"code": "force_activation_not_allowed", "instrument_key": "yee"},
		)

	if data.is_active and instrument.instrument_key == "yee":
		await _get_active_yee_instrument(session, "yee")
		_ensure_scoring_compatible(instrument.content)
		instrument.content = await validated_activation_content(
			session,
			instrument.content,
			instrument.parent_instrument_id,
		)

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


async def _count_yee_audits_for_instrument_version(
	session: AsyncSession,
	instrument_key: str,
	instrument_version: str,
) -> int:
	"""Count audits (submitted + active drafts) stamped with an instrument version.

	Submitted audits live in ``yee_audit_submissions``; in-progress/paused drafts
	live in the shared ``audits`` table. A version referenced by either must be
	protected from deletion, so admins never orphan a report or an in-flight audit.
	"""
	submission_count = (
		await session.execute(
			select(func.count())
			.select_from(YeeAuditSubmission)
			.where(YeeAuditSubmission.instrument_key == instrument_key)
			.where(YeeAuditSubmission.instrument_version == instrument_version)
		)
	).scalar_one()

	draft_count = (
		await session.execute(
			select(func.count())
			.select_from(Audit)
			.where(Audit.instrument_version == instrument_version)
			.where(Audit.status.in_([AuditStatus.IN_PROGRESS, AuditStatus.PAUSED]))
			.where(
				or_(
					Audit.instrument_key == instrument_key,
					Audit.instrument_key.like(f"{instrument_key}%"),
					Audit.instrument_key.is_(None),
				)
			)
		)
	).scalar_one()

	return int(submission_count) + int(draft_count)


async def _delete_yee_instrument_version(session: AsyncSession, instrument_id: uuid.UUID) -> Instrument | None:
	instrument = await _get_yee_instrument_by_id(session, instrument_id)
	if instrument is None:
		return None
	if instrument.is_active:
		raise HTTPException(status_code=400, detail="The active instrument version cannot be deleted.")
	usage = await _count_yee_audits_for_instrument_version(
		session, instrument.instrument_key, instrument.instrument_version
	)
	if usage > 0:
		raise HTTPException(
			status_code=409,
			detail="Instrument versions referenced by audits cannot be deleted.",
		)
	await session.delete(instrument)
	await session.commit()
	return instrument

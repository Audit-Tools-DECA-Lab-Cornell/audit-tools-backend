"""YEE instrument service.

Active-instrument lookup, bootstrap, version create/activate/delete, content
normalization, and site-copy handling.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy import update as sqlalchemy_update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AccountType, Audit, AuditStatus, Instrument, User, YeeAuditSubmission
from app.products.yee.schemas.instrument import (
	YeeInstrumentActivateRequest,
	YeeInstrumentCreateRequest,
)
from app.products.yee.services.scoring import get_yee_instrument_data
from app.products.yee.services.scoring_contract import validate_scoring_compatibility
from app.yee_instrument_schema import YeeInstrumentResponse


def _require_admin(user: User) -> None:
	if user.account_type != AccountType.ADMIN:
		raise HTTPException(status_code=403, detail="Admin access is required.")


def _ensure_scoring_compatible(content: Any, *, force: bool) -> None:
	"""Block publishing a scored YEE instrument the engine cannot fully score.

	Only the scored ``yee`` instrument is gated; site copy and other keys carry
	no scored questions. ``force`` lets an admin override with eyes open (the
	report still rides along in the error body for the earlier non-forced call).
	"""

	if force:
		return
	report = validate_scoring_compatibility(content if isinstance(content, dict) else {})
	if not report.ok:
		raise HTTPException(
			status_code=409,
			detail={
				"message": (
					"This version is missing questions the scoring needs, so it can't be published. "
					"Restore the missing questions, or publish again to override."
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
	*,
	force: bool = False,
) -> Instrument:
	if activate and data.instrument_key == "yee":
		_ensure_scoring_compatible(data.content, force=force)
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
	*,
	force: bool = False,
) -> Instrument | None:
	instrument = await _get_yee_instrument_by_id(session, instrument_id)
	if instrument is None:
		return None

	if data.is_active and instrument.instrument_key == "yee":
		_ensure_scoring_compatible(instrument.content, force=force)

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

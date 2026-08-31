from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument
from app.products.yee.schemas.instrument import (
	InstrumentCompatibilityStatus,
	InstrumentSchemaGeneration,
	InstrumentValidationReason,
	YeeInstrumentActivateRequest,
	YeeInstrumentDraftUpdateRequest,
	YeeInstrumentDraftValidationResponse,
	YeeInstrumentForkRequest,
	YeeInstrumentPublishRequest,
)
from app.products.yee.services.instrument import (
	_count_yee_audits_for_instrument_version,
	_get_yee_instrument_by_id,
	_update_yee_instrument_status,
)
from app.products.yee.services.instrument_activation import validate_copy_only_activation
from app.products.yee.services.instrument_authoring import legacy_to_authoring
from app.products.yee.services.scoring_contract import validate_scoring_compatibility
from app.products.yee.services.scoring_spec import ITEM_SPECS
from app.yee_instrument_schema import YeeInstrumentResponse


def _as_utc(value: datetime) -> datetime:
	if value.tzinfo is None:
		return value.replace(tzinfo=timezone.utc)
	return value.astimezone(timezone.utc)


def _raise_conflict(row: Instrument, expected: datetime) -> None:
	if _as_utc(row.updated_at) == _as_utc(expected):
		return
	raise HTTPException(
		status_code=409,
		detail={
			"code": "draft_conflict",
			"message": "This draft changed after it was opened. Reload it before saving again.",
			"expected_updated_at": expected.isoformat(),
			"actual_updated_at": row.updated_at.isoformat(),
		},
	)


async def _usage_count(session: AsyncSession, row: Instrument) -> int:
	return await _count_yee_audits_for_instrument_version(
		session,
		row.instrument_key,
		row.instrument_version,
	)


async def _require_mutable_draft(session: AsyncSession, row: Instrument) -> None:
	usage_count = await _usage_count(session, row)
	if not row.is_active and usage_count == 0:
		return
	raise HTTPException(
		status_code=409,
		detail={
			"code": "instrument_immutable",
			"message": "Active or audit-referenced instrument versions cannot be edited.",
			"lifecycle": "active" if row.is_active else "archived",
			"usage_count": usage_count,
		},
	)


async def _ensure_version_label_available(
	session: AsyncSession,
	instrument_key: str,
	version_label: str,
	*,
	exclude_id: uuid.UUID | None = None,
) -> str:
	label = version_label.strip()
	if not label:
		raise HTTPException(status_code=422, detail={"code": "instrument_version_required"})
	stmt = (
		select(Instrument.id, Instrument.instrument_version)
		.where(Instrument.instrument_key == instrument_key)
		.where(func.lower(Instrument.instrument_version) == label.casefold())
	)
	if exclude_id is not None:
		stmt = stmt.where(Instrument.id != exclude_id)
	conflict = (await session.execute(stmt.limit(1))).first()
	if conflict is not None:
		raise HTTPException(
			status_code=409,
			detail={
				"code": "instrument_version_conflict",
				"message": "An instrument version already uses this label.",
				"instrument_version": conflict.instrument_version,
			},
		)
	return label


def _validate_scored_question_deletions(content: YeeInstrumentResponse) -> None:
	authoring = content.authoring
	if authoring is None:
		return
	present = {question.id for section in authoring.sections for question in section.questions}
	missing = [spec.key for spec in ITEM_SPECS if spec.key not in present]
	if not missing:
		return
	raise HTTPException(
		status_code=422,
		detail={
			"code": "missing_scored_questions",
			"message": "Scored questions cannot be deleted from an authoring draft.",
			"question_ids": missing,
		},
	)


def _schema_generation(content: dict[str, Any]) -> InstrumentSchemaGeneration:
	return "authoring_v2" if content.get("authoring") is not None else "legacy"


async def _compatibility_status(session: AsyncSession, row: Instrument) -> InstrumentCompatibilityStatus:
	try:
		candidate = YeeInstrumentResponse.model_validate(row.content)
	except ValidationError:
		return "invalid"
	if candidate.authoring is None:
		return "legacy"
	if row.parent_instrument_id is None:
		return "migration_required"
	parent = await session.get(Instrument, row.parent_instrument_id)
	if parent is None or parent.instrument_key != "yee":
		return "migration_required"
	try:
		parent_content = YeeInstrumentResponse.model_validate(parent.content)
	except ValidationError:
		return "invalid"
	return "copy_only" if validate_copy_only_activation(candidate, parent_content).ok else "migration_required"


async def instrument_version_payload(
	session: AsyncSession,
	row: Instrument,
	*,
	include_content: bool,
) -> dict[str, Any]:
	usage_count = await _usage_count(session, row)
	payload: dict[str, Any] = {
		"id": row.id,
		"instrument_key": row.instrument_key,
		"instrument_version": row.instrument_version,
		"parent_instrument_id": row.parent_instrument_id,
		"is_active": row.is_active,
		"lifecycle": "active" if row.is_active else ("archived" if usage_count else "draft"),
		"usage_count": usage_count,
		"schema_generation": _schema_generation(row.content),
		"compatibility_status": await _compatibility_status(session, row),
		"created_at": row.created_at,
		"updated_at": row.updated_at,
	}
	if include_content:
		content = YeeInstrumentResponse.model_validate(row.content)
		if row.instrument_key == "yee" and content.authoring is None:
			content = content.model_copy(update={"authoring": legacy_to_authoring(content).authoring}, deep=True)
		payload["content"] = content.model_dump()
	return payload


async def fork_instrument_draft(
	session: AsyncSession,
	source_id: uuid.UUID,
	data: YeeInstrumentForkRequest,
) -> Instrument | None:
	source = await _get_yee_instrument_by_id(session, source_id)
	if source is None or source.instrument_key != "yee":
		return None
	label = await _ensure_version_label_available(session, "yee", data.instrument_version)
	content = YeeInstrumentResponse.model_validate(source.content)
	if content.authoring is None:
		content = content.model_copy(update={"authoring": legacy_to_authoring(content).authoring}, deep=True)
	draft = Instrument(
		instrument_key="yee",
		instrument_version=label,
		parent_instrument_id=source.id,
		is_active=False,
		content=content.model_dump(),
	)
	session.add(draft)
	await session.commit()
	await session.refresh(draft)
	return draft


async def update_instrument_draft(
	session: AsyncSession,
	instrument_id: uuid.UUID,
	data: YeeInstrumentDraftUpdateRequest,
) -> Instrument | None:
	row = await _get_yee_instrument_by_id(session, instrument_id)
	if row is None or row.instrument_key != "yee":
		return None
	await _require_mutable_draft(session, row)
	_raise_conflict(row, data.expected_updated_at)
	label = await _ensure_version_label_available(
		session,
		"yee",
		data.instrument_version,
		exclude_id=row.id,
	)
	content = YeeInstrumentResponse.model_validate(data.content)
	if content.authoring is None:
		raise HTTPException(
			status_code=422,
			detail={"code": "authoring_v2_required", "message": "Draft updates require authoring schema v2."},
		)
	_validate_scored_question_deletions(content)
	row.instrument_version = label
	row.content = content.model_dump()
	row.updated_at = datetime.now(timezone.utc)
	await session.commit()
	await session.refresh(row)
	return row


async def validate_instrument_draft(
	session: AsyncSession,
	instrument_id: uuid.UUID,
) -> YeeInstrumentDraftValidationResponse | None:
	row = await _get_yee_instrument_by_id(session, instrument_id)
	if row is None or row.instrument_key != "yee":
		return None
	try:
		content = YeeInstrumentResponse.model_validate(row.content)
	except ValidationError as exc:
		return YeeInstrumentDraftValidationResponse(
			valid=False,
			activation_ready=False,
			schema_generation=_schema_generation(row.content),
			scoring_compatibility=validate_scoring_compatibility({}),
			reasons=[InstrumentValidationReason(code="invalid_content", message=str(exc))],
		)
	report = validate_scoring_compatibility(content.model_dump())
	if content.authoring is None:
		return YeeInstrumentDraftValidationResponse(
			valid=report.ok,
			activation_ready=report.ok,
			schema_generation="legacy",
			scoring_compatibility=report,
		)
	reasons: list[InstrumentValidationReason] = []
	if row.parent_instrument_id is None:
		reasons.append(
			InstrumentValidationReason(
				code="parent_instrument_required",
				message="Authoring schema v2 requires a parent.",
			)
		)
	else:
		parent = await session.get(Instrument, row.parent_instrument_id)
		if parent is None or parent.instrument_key != "yee":
			reasons.append(
				InstrumentValidationReason(
					code="parent_instrument_invalid",
					message="The parent instrument does not exist.",
				)
			)
		else:
			try:
				validation = validate_copy_only_activation(
					content,
					YeeInstrumentResponse.model_validate(parent.content),
				)
				reasons.extend(
					InstrumentValidationReason(
						code=reason.code,
						message=reason.message,
						question_id=reason.question_id,
						item_id=reason.item_id,
					)
					for reason in validation.reasons
				)
			except ValidationError as exc:
				reasons.append(InstrumentValidationReason(code="parent_content_invalid", message=str(exc)))
	return YeeInstrumentDraftValidationResponse(
		valid=True,
		activation_ready=report.ok and not reasons,
		schema_generation="authoring_v2",
		scoring_compatibility=report,
		reasons=reasons,
	)


async def publish_instrument_draft(
	session: AsyncSession,
	instrument_id: uuid.UUID,
	data: YeeInstrumentPublishRequest,
) -> Instrument | None:
	row = await _get_yee_instrument_by_id(session, instrument_id)
	if row is None or row.instrument_key != "yee":
		return None
	await _require_mutable_draft(session, row)
	_raise_conflict(row, data.expected_updated_at)
	return await _update_yee_instrument_status(
		session,
		row.id,
		YeeInstrumentActivateRequest(is_active=True),
	)

"""YEE instrument routes.

Public instrument fetch, admin instrument versioning, and site-copy endpoints.
Thin HTTP layer over `app.products.yee.services.instrument`. Paths are declared
without the `/yee` prefix; the product router in
`app/products/yee/routes/__init__.py` supplies it.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import get_auth_session, get_current_user
from app.models import User
from app.products.yee.schemas.instrument import (
	ScoringCompatibilityReport,
	SiteCopyCreateRequest,
	SiteCopyVersionResponse,
	YeeInstrumentActivateRequest,
	YeeInstrumentCreateRequest,
	YeeInstrumentValidateRequest,
	YeeInstrumentVersionResponse,
)
from app.products.yee.services.instrument import (
	_bootstrap_yee_instrument_if_missing,
	_create_yee_instrument_version,
	_delete_yee_instrument_version,
	_get_active_yee_instrument,
	_list_yee_instrument_versions,
	_normalize_yee_instrument_content,
	_require_admin,
	_update_yee_instrument_status,
)
from app.products.yee.services.scoring import get_yee_instrument_data
from app.products.yee.services.scoring_contract import validate_scoring_compatibility
from app.yee_instrument_schema import YeeInstrumentResponse

router = APIRouter()


@router.get("/instrument")
async def get_yee_instrument(
	session: AsyncSession = Depends(get_auth_session),
) -> dict[str, object]:
	"""Return YEE instrument metadata and scoring matrix extracted from QSF."""

	active = await _get_active_yee_instrument(session)
	if active is None:
		active = await _bootstrap_yee_instrument_if_missing(session)
	if active is not None:
		try:
			return _normalize_yee_instrument_content(active.content)
		except Exception:
			pass
	return YeeInstrumentResponse.model_validate(get_yee_instrument_data()).model_dump()


@router.get("/admin/instruments", response_model=list[YeeInstrumentVersionResponse])
async def admin_list_yee_instruments(
	instrument_key: str = "yee",
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[YeeInstrumentVersionResponse]:
	_require_admin(user)
	await _bootstrap_yee_instrument_if_missing(session, instrument_key)
	rows = await _list_yee_instrument_versions(session, instrument_key)
	return [
		YeeInstrumentVersionResponse.model_validate(
			{
				"id": row.id,
				"instrument_key": row.instrument_key,
				"instrument_version": row.instrument_version,
				"is_active": row.is_active,
				"content": _normalize_yee_instrument_content(row.content) if instrument_key == "yee" else row.content,
				"created_at": row.created_at,
				"updated_at": row.updated_at,
			}
		)
		for row in rows
	]


@router.get("/site-copy")
async def get_site_copy(
	session: AsyncSession = Depends(get_auth_session),
) -> dict[str, Any]:
	active = await _get_active_yee_instrument(session, "yee_site_copy")
	if active is None or not isinstance(active.content, dict):
		return {}
	return active.content


@router.get("/admin/site-copy", response_model=list[SiteCopyVersionResponse])
async def admin_list_site_copy_versions(
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[SiteCopyVersionResponse]:
	_require_admin(user)
	rows = await _list_yee_instrument_versions(session, "yee_site_copy")
	return [SiteCopyVersionResponse.model_validate(row) for row in rows]


@router.post("/admin/site-copy", response_model=SiteCopyVersionResponse, status_code=status.HTTP_201_CREATED)
async def admin_create_site_copy_version(
	data: SiteCopyCreateRequest,
	activate: bool = True,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> SiteCopyVersionResponse:
	_require_admin(user)
	row = await _create_yee_instrument_version(
		session,
		YeeInstrumentCreateRequest(
			instrument_key="yee_site_copy",
			instrument_version=data.instrument_version,
			content=data.content,
		),
		activate,
	)
	return SiteCopyVersionResponse.model_validate(row)


@router.patch("/admin/site-copy/{copy_id}", response_model=SiteCopyVersionResponse)
async def admin_update_site_copy_version(
	copy_id: uuid.UUID,
	data: YeeInstrumentActivateRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> SiteCopyVersionResponse:
	_require_admin(user)
	row = await _update_yee_instrument_status(session, copy_id, data)
	if row is None:
		raise HTTPException(status_code=404, detail="Site copy version not found")
	return SiteCopyVersionResponse.model_validate(row)


@router.post("/admin/instruments/validate", response_model=ScoringCompatibilityReport)
async def admin_validate_yee_instrument_scoring(
	data: YeeInstrumentValidateRequest,
	user: User = Depends(get_current_user),
) -> ScoringCompatibilityReport:
	"""Dry-run scoring-compatibility check for a candidate instrument content.

	Lets the admin editor confirm a draft can be scored before publishing,
	without creating a version.
	"""

	_require_admin(user)
	return validate_scoring_compatibility(data.content)


@router.post("/admin/instruments", response_model=YeeInstrumentVersionResponse, status_code=status.HTTP_201_CREATED)
async def admin_create_yee_instrument(
	data: YeeInstrumentCreateRequest,
	activate: bool = True,
	force: bool = False,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> YeeInstrumentVersionResponse:
	_require_admin(user)
	validated_content = YeeInstrumentResponse.model_validate(data.content).model_dump()
	validated_data = YeeInstrumentCreateRequest(
		instrument_key=data.instrument_key,
		instrument_version=data.instrument_version,
		content=validated_content,
	)
	row = await _create_yee_instrument_version(session, validated_data, activate, force=force)
	return YeeInstrumentVersionResponse.model_validate(
		{
			"id": row.id,
			"instrument_key": row.instrument_key,
			"instrument_version": row.instrument_version,
			"is_active": row.is_active,
			"content": _normalize_yee_instrument_content(row.content),
			"created_at": row.created_at,
			"updated_at": row.updated_at,
		}
	)


@router.patch("/admin/instruments/{instrument_id}", response_model=YeeInstrumentVersionResponse)
async def admin_update_yee_instrument(
	instrument_id: uuid.UUID,
	data: YeeInstrumentActivateRequest,
	force: bool = False,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> YeeInstrumentVersionResponse:
	_require_admin(user)
	row = await _update_yee_instrument_status(session, instrument_id, data, force=force)
	if row is None:
		raise HTTPException(status_code=404, detail="Instrument not found")
	return YeeInstrumentVersionResponse.model_validate(
		{
			"id": row.id,
			"instrument_key": row.instrument_key,
			"instrument_version": row.instrument_version,
			"is_active": row.is_active,
			"content": _normalize_yee_instrument_content(row.content),
			"created_at": row.created_at,
			"updated_at": row.updated_at,
		}
	)


@router.delete("/admin/instruments/{instrument_id}")
async def admin_delete_yee_instrument(
	instrument_id: uuid.UUID,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> dict[str, Any]:
	_require_admin(user)
	row = await _delete_yee_instrument_version(session, instrument_id)
	if row is None:
		raise HTTPException(status_code=404, detail="Instrument not found")
	return {"deleted": True, "instrument_id": str(instrument_id)}

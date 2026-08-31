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
	YeeInstrumentDraftUpdateRequest,
	YeeInstrumentDraftValidationResponse,
	YeeInstrumentForkRequest,
	YeeInstrumentPublishRequest,
	YeeInstrumentValidateRequest,
	YeeInstrumentVersionResponse,
	YeeInstrumentVersionSummaryResponse,
)
from app.products.yee.services.instrument import (
	_bootstrap_yee_instrument_if_missing,
	_create_yee_instrument_version,
	_delete_yee_instrument_version,
	_get_active_yee_instrument,
	_get_yee_instrument_by_id,
	_get_yee_instrument_by_stamp,
	_list_yee_instrument_versions,
	public_yee_instrument_payload,
	_require_admin,
	_update_yee_instrument_status,
)
from app.products.yee.services.instrument_drafts import (
	fork_instrument_draft,
	instrument_version_payload,
	publish_instrument_draft,
	update_instrument_draft,
	validate_instrument_draft,
)
from app.products.yee.services.scoring_contract import validate_scoring_compatibility
from app.yee_instrument_schema import YeeInstrumentResponse

router = APIRouter()


def _reject_yee_force_request(force: bool, instrument_key: str) -> None:
	if force and instrument_key == "yee":
		raise HTTPException(
			status_code=409,
			detail={"code": "force_activation_not_allowed", "instrument_key": "yee"},
		)


@router.get("/instrument")
async def get_yee_instrument(
	instrument_key: str | None = None,
	instrument_version: str | None = None,
	session: AsyncSession = Depends(get_auth_session),
) -> dict[str, object]:
	"""Return YEE instrument metadata and scoring matrix extracted from QSF."""

	if (instrument_key is None) != (instrument_version is None):
		raise HTTPException(
			status_code=422,
			detail={"code": "partial_instrument_stamp", "message": "Provide both instrument key and version."},
		)
	active = (
		await _get_yee_instrument_by_stamp(session, instrument_key, instrument_version)
		if instrument_key is not None and instrument_version is not None
		else await _get_active_yee_instrument(session)
	)
	if active is None and instrument_key is not None:
		raise HTTPException(
			status_code=404,
			detail={
				"code": "missing_stamped_instrument",
				"instrument_key": instrument_key,
				"instrument_version": instrument_version,
			},
		)
	if active is None:
		active = await _bootstrap_yee_instrument_if_missing(session)
	if active is not None:
		try:
			return {
				**public_yee_instrument_payload(active.content),
				"instrument_key": active.instrument_key,
				"instrument_version": active.instrument_version,
			}
		except Exception:
			pass
	return public_yee_instrument_payload(None)


@router.get("/admin/instruments", response_model=list[YeeInstrumentVersionSummaryResponse])
async def admin_list_yee_instruments(
	instrument_key: str = "yee",
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> list[YeeInstrumentVersionSummaryResponse]:
	_require_admin(user)
	await _bootstrap_yee_instrument_if_missing(session, instrument_key)
	rows = await _list_yee_instrument_versions(session, instrument_key)
	return [
		YeeInstrumentVersionSummaryResponse.model_validate(
			await instrument_version_payload(session, row, include_content=False)
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
	activate: bool | None = None,
	force: bool = False,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> YeeInstrumentVersionResponse:
	_require_admin(user)
	_reject_yee_force_request(force, data.instrument_key)
	validated_content = YeeInstrumentResponse.model_validate(data.content).model_dump()
	validated_data = YeeInstrumentCreateRequest(
		instrument_key=data.instrument_key,
		instrument_version=data.instrument_version,
		parent_instrument_id=data.parent_instrument_id,
		content=validated_content,
	)
	row = await _create_yee_instrument_version(session, validated_data, activate, force=force)
	return YeeInstrumentVersionResponse.model_validate(
		await instrument_version_payload(session, row, include_content=True)
	)


@router.get("/admin/instruments/{instrument_id}", response_model=YeeInstrumentVersionResponse)
async def admin_get_yee_instrument(
	instrument_id: uuid.UUID,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> YeeInstrumentVersionResponse:
	_require_admin(user)
	row = await _get_yee_instrument_by_id(session, instrument_id)
	if row is None or row.instrument_key != "yee":
		raise HTTPException(status_code=404, detail="Instrument not found")
	return YeeInstrumentVersionResponse.model_validate(
		await instrument_version_payload(session, row, include_content=True)
	)


@router.post(
	"/admin/instruments/{instrument_id}/fork",
	response_model=YeeInstrumentVersionResponse,
	status_code=status.HTTP_201_CREATED,
)
async def admin_fork_yee_instrument(
	instrument_id: uuid.UUID,
	data: YeeInstrumentForkRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> YeeInstrumentVersionResponse:
	_require_admin(user)
	row = await fork_instrument_draft(session, instrument_id, data)
	if row is None:
		raise HTTPException(status_code=404, detail="Instrument not found")
	return YeeInstrumentVersionResponse.model_validate(
		await instrument_version_payload(session, row, include_content=True)
	)


@router.put("/admin/instruments/{instrument_id}/draft", response_model=YeeInstrumentVersionResponse)
async def admin_update_yee_instrument_draft(
	instrument_id: uuid.UUID,
	data: YeeInstrumentDraftUpdateRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> YeeInstrumentVersionResponse:
	_require_admin(user)
	row = await update_instrument_draft(session, instrument_id, data)
	if row is None:
		raise HTTPException(status_code=404, detail="Instrument not found")
	return YeeInstrumentVersionResponse.model_validate(
		await instrument_version_payload(session, row, include_content=True)
	)


@router.post(
	"/admin/instruments/{instrument_id}/validate",
	response_model=YeeInstrumentDraftValidationResponse,
)
async def admin_validate_yee_instrument_draft(
	instrument_id: uuid.UUID,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> YeeInstrumentDraftValidationResponse:
	_require_admin(user)
	result = await validate_instrument_draft(session, instrument_id)
	if result is None:
		raise HTTPException(status_code=404, detail="Instrument not found")
	return result


@router.post("/admin/instruments/{instrument_id}/publish", response_model=YeeInstrumentVersionResponse)
async def admin_publish_yee_instrument_draft(
	instrument_id: uuid.UUID,
	data: YeeInstrumentPublishRequest,
	user: User = Depends(get_current_user),
	session: AsyncSession = Depends(get_auth_session),
) -> YeeInstrumentVersionResponse:
	_require_admin(user)
	row = await publish_instrument_draft(session, instrument_id, data)
	if row is None:
		raise HTTPException(status_code=404, detail="Instrument not found")
	return YeeInstrumentVersionResponse.model_validate(
		await instrument_version_payload(session, row, include_content=True)
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
	if force:
		existing = await _get_yee_instrument_by_id(session, instrument_id)
		if existing is not None:
			_reject_yee_force_request(force, existing.instrument_key)
	row = await _update_yee_instrument_status(session, instrument_id, data, force=force)
	if row is None:
		raise HTTPException(status_code=404, detail="Instrument not found")
	return YeeInstrumentVersionResponse.model_validate(
		await instrument_version_payload(session, row, include_content=True)
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

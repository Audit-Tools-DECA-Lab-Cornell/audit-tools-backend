"""
Instrument metadata and management endpoints for Playspace.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actors import CurrentUserContext
from app.products.playspace.instrument import (
	get_canonical_instrument_response,
)
from app.products.playspace.routes.dependencies import (
	CURRENT_USER_DEPENDENCY,
	SESSION_DEPENDENCY,
)
from app.products.playspace.schemas.instrument import PlayspaceInstrumentResponse
from app.products.playspace.schemas.management import (
	InstrumentActivateRequest,
	InstrumentCreateRequest,
	InstrumentVersionResponse,
)
from app.products.playspace.services.instrument import (
	create_instrument_version,
	get_active_instrument,
	list_instrument_versions,
	update_instrument_status,
)

router = APIRouter(tags=["playspace-instrument"])


@router.get("/instrument")
async def get_instrument_metadata(
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
) -> PlayspaceInstrumentResponse:
	"""Return the canonical Playspace instrument contract."""

	_ = current_user
	return get_canonical_instrument_response()


@router.get(
	"/instruments/active/{instrument_key}",
	response_model=PlayspaceInstrumentResponse,
)
async def get_active_instrument_by_key(
	instrument_key: str,
	lang: str = Query("en", description="Language code for the returned instrument."),
	session: AsyncSession = SESSION_DEPENDENCY,
) -> PlayspaceInstrumentResponse:
	"""
	Return the active instrument definition for a given key and language.

	Falls back to the static canonical payload when no database record exists.
	"""

	instrument = await get_active_instrument(session, instrument_key)
	if instrument is None:
		return get_canonical_instrument_response()

	content = instrument.content
	localized = content.get(lang) or content.get("en")
	if localized is None:
		return get_canonical_instrument_response()

	return PlayspaceInstrumentResponse.model_validate(localized)


@router.get(
	"/admin/instruments",
	response_model=list[InstrumentVersionResponse],
)
async def admin_list_instruments(
	instrument_key: str = Query("pvua_v5_2"),
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	session: AsyncSession = SESSION_DEPENDENCY,
) -> list[InstrumentVersionResponse]:
	"""List all instrument versions for a given key (admin only)."""

	_ = current_user
	rows = await list_instrument_versions(session, instrument_key)
	return [InstrumentVersionResponse.model_validate(row) for row in rows]


@router.post(
	"/admin/instruments",
	response_model=InstrumentVersionResponse,
	status_code=status.HTTP_201_CREATED,
)
async def admin_create_instrument(
	data: InstrumentCreateRequest,
	activate: bool = Query(True),
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	session: AsyncSession = SESSION_DEPENDENCY,
) -> InstrumentVersionResponse:
	"""Create a new instrument version (admin only)."""

	_ = current_user
	row = await create_instrument_version(session, data, activate)
	return InstrumentVersionResponse.model_validate(row)


@router.patch(
	"/admin/instruments/{instrument_id}",
	response_model=InstrumentVersionResponse,
)
async def admin_update_instrument(
	instrument_id: UUID,
	data: InstrumentActivateRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	session: AsyncSession = SESSION_DEPENDENCY,
) -> InstrumentVersionResponse:
	"""Toggle activation status of a specific instrument version (admin only)."""

	_ = current_user
	row = await update_instrument_status(session, instrument_id, data)
	if row is None:
		raise HTTPException(
			status_code=status.HTTP_404_NOT_FOUND,
			detail="Instrument not found",
		)
	return InstrumentVersionResponse.model_validate(row)

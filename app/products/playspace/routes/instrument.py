"""
Instrument metadata endpoint for Playspace.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.actors import CurrentUserContext
from app.products.playspace.instrument import (
    get_canonical_instrument_response,
)
from app.products.playspace.routes.dependencies import CURRENT_USER_DEPENDENCY
from app.products.playspace.schemas.instrument import PlayspaceInstrumentResponse

router = APIRouter(tags=["playspace-instrument"])


@router.get("/instrument")
async def get_instrument_metadata(
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
) -> PlayspaceInstrumentResponse:
    """Return the canonical Playspace instrument contract."""

    _ = current_user
    return get_canonical_instrument_response()

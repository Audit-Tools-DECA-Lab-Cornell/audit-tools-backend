"""
Instrument metadata endpoint for Playspace.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.core.actors import CurrentUserContext
from app.products.playspace.instrument import (
    INSTRUMENT_KEY,
    INSTRUMENT_NAME,
    INSTRUMENT_VERSION,
)
from app.products.playspace.routes.dependencies import CURRENT_USER_DEPENDENCY

router = APIRouter(tags=["playspace-instrument"])


@router.get("/instrument")
async def get_instrument_metadata(
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
) -> dict[str, str]:
    """Return version metadata for the Playspace instrument."""

    _ = current_user
    return {
        "instrument_key": INSTRUMENT_KEY,
        "instrument_name": INSTRUMENT_NAME,
        "instrument_version": INSTRUMENT_VERSION,
    }

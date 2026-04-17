"""
Self-service current-user endpoints for Playspace.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status

from app.core.actors import CurrentUserContext
from app.products.playspace.routes.dependencies import (
    CURRENT_USER_DEPENDENCY,
    SESSION_DEPENDENCY,
)
from app.products.playspace.schemas.me import (
    MyAccountResponse,
    MyAuditorProfileResponse,
)
from app.products.playspace.services.me import PlayspaceMeService

router: APIRouter = APIRouter(tags=["playspace-me"])


def _require_account_id(current_user: CurrentUserContext) -> uuid.UUID:
    """Extract account_id from the current user context or raise 403."""

    if current_user.account_id is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account identity is required for self-service operations.",
        )
    return current_user.account_id


@router.get("/me")
async def get_my_account(
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    session=SESSION_DEPENDENCY,
) -> MyAccountResponse:
    """Return the current user's account details."""

    account_id = _require_account_id(current_user)
    service = PlayspaceMeService(session=session)
    account = await service.get_account(account_id=account_id)

    return MyAccountResponse(
        account_id=account.id,
        name=account.name,
        email=account.email,
        account_type=account.account_type.value,
    )


@router.get("/me/auditor-profile")
async def get_my_auditor_profile(
    current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
    session=SESSION_DEPENDENCY,
) -> MyAuditorProfileResponse:
    """Return the current user's auditor profile."""

    account_id = _require_account_id(current_user)
    service = PlayspaceMeService(session=session)
    profile = await service.get_auditor_profile(account_id=account_id)

    return MyAuditorProfileResponse(
        profile_id=profile.id,
        auditor_code=profile.auditor_code,
        full_name=profile.full_name,
        email=profile.email,
        age_range=profile.age_range,
        gender=profile.gender,
        country=profile.country,
        role=profile.role,
    )

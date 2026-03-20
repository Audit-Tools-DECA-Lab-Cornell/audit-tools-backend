"""
Profile service for read-only self-service operations.

Profile editing (account updates, password changes) is deferred until the
authentication system is complete and manager dashboard flows are designed.
"""

from __future__ import annotations

import uuid

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import Account, AuditorProfile


class PlayspaceProfileService:
    """Handles read-only profile lookups for the current user."""

    def __init__(self, *, session: AsyncSession) -> None:
        self._session = session

    async def get_account(self, *, account_id: uuid.UUID) -> Account:
        """Fetch account by ID or raise 404."""

        result = await self._session.execute(
            select(Account)
            .options(joinedload(Account.auditor_profile))
            .where(Account.id == account_id)
        )
        account = result.unique().scalar_one_or_none()
        if account is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Account not found.",
            )
        return account

    async def get_auditor_profile(self, *, account_id: uuid.UUID) -> AuditorProfile:
        """Fetch auditor profile for the given account or raise 404."""

        result = await self._session.execute(
            select(AuditorProfile).where(AuditorProfile.account_id == account_id)
        )
        profile = result.scalar_one_or_none()
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Auditor profile not found for this account.",
            )
        return profile

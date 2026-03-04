"""
Dummy authentication endpoints (REST).

These endpoints are intentionally insecure and are meant only as scaffolding while
the real authentication system is being built. For now they:
- Accept a basic JSON payload for sign up / login
- Do not verify passwords
- Do not create/read users in the database
- Always return a successful response with a deterministic "dummy" token
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.models import AccountType

router: APIRouter = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    """
    Request payload for dummy sign up.

    Notes:
    - `password` is accepted but ignored (no hashing/verification yet).
    - `account_type` is optional so clients can test both role flows early.
    """

    email: str = Field(..., max_length=320)
    password: str = Field(..., max_length=4096)
    name: str | None = Field(default=None, max_length=200)
    account_type: AccountType | None = Field(default=None)


class LoginRequest(BaseModel):
    """
    Request payload for dummy login.

    Notes:
    - `password` is accepted but ignored (no verification yet).
    """

    email: str = Field(..., max_length=320)
    password: str = Field(..., max_length=4096)


class AuthUser(BaseModel):
    """Public user information returned by the auth endpoints."""

    id: uuid.UUID
    email: str
    name: str | None = None
    account_type: AccountType


class AuthResponse(BaseModel):
    """
    Response returned for successful dummy authentication.

    `access_token` is NOT a real JWT. It is a deterministic string to make it
    easier for clients to test round-trips without storing secrets.
    """

    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: AuthUser


def _normalize_email(email: str) -> str:
    """
    Normalize an email-like identifier.

    We intentionally keep validation minimal because this is a dummy endpoint and
    should not block client development.
    """

    return email.strip().lower()


def _derive_user_id(normalized_email: str) -> uuid.UUID:
    """
    Derive a stable UUID for a given identifier.

    This lets clients receive the same `user.id` for the same email across calls
    without persisting any data.
    """

    return uuid.uuid5(uuid.NAMESPACE_DNS, normalized_email)


def _build_auth_response(
    *,
    email: str,
    name: str | None,
    account_type: AccountType,
) -> AuthResponse:
    """Build a deterministic dummy auth response."""

    normalized_email = _normalize_email(email)
    user_id = _derive_user_id(normalized_email)

    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    access_token = f"dummy-token-{user_id}"

    cleaned_name = name.strip() if name is not None and name.strip() else None

    return AuthResponse(
        access_token=access_token,
        token_type="bearer",
        expires_at=expires_at,
        user=AuthUser(
            id=user_id,
            email=normalized_email,
            name=cleaned_name,
            account_type=account_type,
        ),
    )


@router.post(
    "/signup",
    status_code=status.HTTP_201_CREATED,
)
async def signup(payload: SignupRequest) -> AuthResponse:
    """
    Dummy sign up endpoint.

    Always returns a successful response and does not persist anything.
    """

    chosen_account_type = payload.account_type if payload.account_type is not None else AccountType.MANAGER

    return _build_auth_response(
        email=payload.email,
        name=payload.name,
        account_type=chosen_account_type,
    )

@router.post("/login")
async def login(payload: LoginRequest) -> AuthResponse:
    """
    Dummy login endpoint.

    Always returns a successful response and does not verify credentials.
    """

    return _build_auth_response(
        email=payload.email,
        name=None,
        account_type=AccountType.MANAGER,
    )


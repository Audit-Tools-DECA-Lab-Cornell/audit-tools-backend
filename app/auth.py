"""Authentication endpoints with DB-backed users and email verification."""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, Request as FastAPIRequest, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth_security import (
    generate_access_token,
    generate_email_verification_token,
    get_verification_ttl_hours,
    hash_password,
    hash_verification_token,
    verify_password,
)
from app.database import ASYNC_SESSION_FACTORY_BY_PRODUCT, ProductKey
from app.email_service import send_verification_email
from app.models import AccountType, User

router: APIRouter = APIRouter(prefix="/auth", tags=["auth"])


class SignupRequest(BaseModel):
    email: str = Field(..., max_length=320)
    password: str = Field(..., min_length=8, max_length=4096)
    name: str | None = Field(default=None, max_length=200)
    account_type: AccountType | None = Field(default=None)
    captcha_token: str | None = Field(default=None, max_length=4096)
    website: str | None = Field(default=None, max_length=200)


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=320)
    password: str = Field(..., max_length=4096)
    website: str | None = Field(default=None, max_length=200)


class ResendVerificationRequest(BaseModel):
    email: str = Field(..., max_length=320)
    captcha_token: str | None = Field(default=None, max_length=4096)
    website: str | None = Field(default=None, max_length=200)


class AuthUser(BaseModel):
    id: uuid.UUID
    email: str
    name: str | None = None
    account_type: AccountType
    email_verified: bool


class AuthResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_at: datetime
    user: AuthUser


class SignupResponse(BaseModel):
    message: str
    email_verification_required: bool = True


class MessageResponse(BaseModel):
    message: str


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _clean_name(name: str | None) -> str | None:
    if name is None:
        return None
    value = name.strip()
    return value if value else None


def _get_product_from_path(path: str) -> ProductKey:
    if path.startswith("/playsafe/"):
        return ProductKey.PLAYSAFE
    return ProductKey.YEE


async def get_auth_session(request: FastAPIRequest) -> AsyncIterator[AsyncSession]:
    """Pick YEE vs Playsafe DB session from URL prefix."""

    product = _get_product_from_path(request.url.path)
    async with ASYNC_SESSION_FACTORY_BY_PRODUCT[product]() as session:
        yield session


def _build_verify_url(*, request: FastAPIRequest, token: str) -> str:
    template = os.getenv("AUTH_VERIFY_URL_TEMPLATE", "").strip()
    if template:
        return template.format(token=token)

    product_prefix = "/playsafe" if request.url.path.startswith("/playsafe/") else "/yee"
    base = str(request.base_url).rstrip("/")
    query = urlencode({"token": token})
    return f"{base}{product_prefix}/auth/verify-email?{query}"


def _verify_turnstile_if_enabled(*, captcha_token: str | None, remote_ip: str | None) -> None:
    secret = os.getenv("TURNSTILE_SECRET_KEY", "").strip()
    if not secret:
        return

    if captcha_token is None or not captcha_token.strip():
        raise HTTPException(status_code=400, detail="Captcha is required.")

    payload = urlencode(
        {
            "secret": secret,
            "response": captcha_token.strip(),
            "remoteip": remote_ip or "",
        }
    ).encode("utf-8")

    req = Request(
        "https://challenges.cloudflare.com/turnstile/v0/siteverify",
        data=payload,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )

    try:
        with urlopen(req, timeout=10) as response:
            raw = response.read().decode("utf-8")
    except Exception as err:
        raise HTTPException(status_code=503, detail="Captcha verification unavailable.") from err

    if '"success":true' not in raw.replace(" ", "").lower():
        raise HTTPException(status_code=400, detail="Captcha verification failed.")


async def _send_or_log_verification_email(
    *,
    request: FastAPIRequest,
    user: User,
    session: AsyncSession,
) -> None:
    token = generate_email_verification_token()
    user.email_verification_token_hash = hash_verification_token(token)
    user.email_verification_sent_at = datetime.now(timezone.utc)

    verify_url = _build_verify_url(request=request, token=token)
    send_verification_email(to_email=user.email, verify_url=verify_url)

    await session.commit()


@router.post("/signup", response_model=SignupResponse, status_code=status.HTTP_201_CREATED)
async def signup(
    payload: SignupRequest,
    request: FastAPIRequest,
    session: AsyncSession = Depends(get_auth_session),
) -> SignupResponse:
    """Create account in DB and send email verification link."""

    if payload.website and payload.website.strip():
        raise HTTPException(status_code=400, detail="Spam check failed.")

    _verify_turnstile_if_enabled(captcha_token=payload.captcha_token, remote_ip=request.client.host if request.client else None)

    email = _normalize_email(payload.email)
    if not email:
        raise HTTPException(status_code=400, detail="Email is required.")

    if len(payload.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

    account_type = payload.account_type or AccountType.MANAGER
    password_hash = hash_password(payload.password)

    existing_result = await session.execute(select(User).where(User.email == email))
    existing_user = existing_result.scalar_one_or_none()

    if existing_user is not None and existing_user.email_verified:
        raise HTTPException(status_code=409, detail="An account with this email already exists.")

    if existing_user is None:
        user = User(
            email=email,
            password_hash=password_hash,
            account_type=account_type,
            name=_clean_name(payload.name),
            email_verified=False,
            failed_login_attempts=0,
        )
        session.add(user)
        try:
            await session.flush()
        except IntegrityError as err:
            await session.rollback()
            raise HTTPException(status_code=409, detail="Unable to create account.") from err
    else:
        user = existing_user
        user.password_hash = password_hash
        user.account_type = account_type
        user.name = _clean_name(payload.name)
        user.email_verified = False
        user.email_verified_at = None

    await _send_or_log_verification_email(request=request, user=user, session=session)

    return SignupResponse(message="Account created. Please verify your email before logging in.")


@router.get("/verify-email", response_model=MessageResponse)
async def verify_email(
    token: str,
    session: AsyncSession = Depends(get_auth_session),
) -> MessageResponse:
    """Verify a user email address using token sent by email."""

    token_hash = hash_verification_token(token.strip())
    result = await session.execute(
        select(User).where(User.email_verification_token_hash == token_hash)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=400, detail="Invalid verification token.")

    if user.email_verified:
        return MessageResponse(message="Email already verified.")

    if user.email_verification_sent_at is None:
        raise HTTPException(status_code=400, detail="Invalid verification token state.")

    ttl_hours = get_verification_ttl_hours()
    expires_at = user.email_verification_sent_at + timedelta(hours=ttl_hours)
    if datetime.now(timezone.utc) > expires_at:
        raise HTTPException(status_code=400, detail="Verification token has expired. Request a new one.")

    user.email_verified = True
    user.email_verified_at = datetime.now(timezone.utc)
    user.email_verification_token_hash = None
    user.email_verification_sent_at = None
    user.failed_login_attempts = 0
    await session.commit()

    return MessageResponse(message="Email verified successfully.")


@router.post("/resend-verification", response_model=MessageResponse)
async def resend_verification(
    payload: ResendVerificationRequest,
    request: FastAPIRequest,
    session: AsyncSession = Depends(get_auth_session),
) -> MessageResponse:
    """Resend verification email for unverified users."""

    if payload.website and payload.website.strip():
        return MessageResponse(message="If your email exists, a verification link has been sent.")

    _verify_turnstile_if_enabled(captcha_token=payload.captcha_token, remote_ip=request.client.host if request.client else None)

    email = _normalize_email(payload.email)
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None or user.email_verified:
        return MessageResponse(message="If your email exists, a verification link has been sent.")

    await _send_or_log_verification_email(request=request, user=user, session=session)
    return MessageResponse(message="If your email exists, a verification link has been sent.")


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    session: AsyncSession = Depends(get_auth_session),
) -> AuthResponse:
    """Authenticate user with password and verified email requirement."""

    if payload.website and payload.website.strip():
        raise HTTPException(status_code=400, detail="Spam check failed.")

    email = _normalize_email(payload.email)
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()

    if user is None or not verify_password(payload.password, user.password_hash):
        if user is not None:
            user.failed_login_attempts += 1
            await session.commit()
        raise HTTPException(status_code=401, detail="Invalid email or password.")

    if not user.email_verified:
        raise HTTPException(status_code=403, detail="Email is not verified.")

    token, expires_at = generate_access_token(str(user.id))
    user.failed_login_attempts = 0
    user.last_login_at = datetime.now(timezone.utc)
    await session.commit()

    return AuthResponse(
        access_token=token,
        token_type="bearer",
        expires_at=expires_at,
        user=AuthUser(
            id=user.id,
            email=user.email,
            name=user.name,
            account_type=user.account_type,
            email_verified=user.email_verified,
        ),
    )

"""Authentication endpoints with DB-backed users and email verification."""

from __future__ import annotations

import os
import re
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta, timezone
from typing import Literal
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi import Request as FastAPIRequest
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth_security import (
	generate_access_token,
	generate_email_verification_token,
	get_verification_ttl_hours,
	hash_password,
	hash_verification_token,
	verify_access_token,
	verify_password,
)
from app.database import ASYNC_SESSION_FACTORY_BY_PRODUCT, ProductKey
from app.email_service import send_verification_email
from app.models import Account, AccountType, Auditor, AuditorInvite, User

router: APIRouter = APIRouter(prefix="/auth", tags=["auth"])
bearer_scheme = HTTPBearer(auto_error=False)


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
	account_id: uuid.UUID | None = None
	organization: str | None = None
	account_type: AccountType
	email_verified: bool
	approved: bool
	profile_completed: bool
	next_step: str
	dashboard_path: str


class AuthResponse(BaseModel):
	access_token: str
	token_type: Literal["bearer"] = "bearer"
	expires_at: datetime
	user: AuthUser


class SignupResponse(BaseModel):
	message: str
	email_verification_required: bool = True
	next_step: str = "VERIFY_EMAIL"


class MessageResponse(BaseModel):
	message: str


class SessionResponse(BaseModel):
	user: AuthUser


class CompleteProfileRequest(BaseModel):
	name: str = Field(..., min_length=1, max_length=200)


class InvitePreviewResponse(BaseModel):
	email: str
	organization: str | None = None
	expires_at: datetime
	accepted: bool


class AcceptInviteRequest(BaseModel):
	name: str = Field(..., min_length=1, max_length=200)
	password: str = Field(..., min_length=8, max_length=4096)


def _normalize_email(email: str) -> str:
	return email.strip().lower()


def _clean_name(name: str | None) -> str | None:
	if name is None:
		return None
	value = name.strip()
	return value if value else None


def _get_product_from_path(path: str) -> ProductKey:
	if path.startswith("/playspace/"):
		return ProductKey.PLAYSPACE
	return ProductKey.YEE


def _is_playspace_request(request: FastAPIRequest) -> bool:
	"""Return whether the current auth request targets the Playspace product."""

	return _get_product_from_path(request.url.path) is ProductKey.PLAYSPACE


async def get_auth_session(request: FastAPIRequest) -> AsyncIterator[AsyncSession]:
	"""Pick YEE vs Playsafe DB session from URL prefix."""

	product = _get_product_from_path(request.url.path)
	async with ASYNC_SESSION_FACTORY_BY_PRODUCT[product]() as session:
		yield session


def _dashboard_path_for_account_type(account_type: AccountType) -> str:
	if account_type == AccountType.ADMIN:
		return "/admin"
	if account_type == AccountType.AUDITOR:
		return "/my-dashboard"
	return "/dashboard"


def _next_step_for_user(user: User) -> str:
	if not user.email_verified:
		return "VERIFY_EMAIL"
	if not user.approved:
		return "WAITING_APPROVAL"
	if not user.profile_completed:
		return "COMPLETE_PROFILE"
	return "DASHBOARD"


def _serialize_auth_user(user: User) -> AuthUser:
	return AuthUser(
		id=user.id,
		email=user.email,
		name=user.name,
		account_id=user.account_id,
		organization=(user.account.name if "account" in user.__dict__ and user.account is not None else None),
		account_type=user.account_type,
		email_verified=user.email_verified,
		approved=user.approved,
		profile_completed=user.profile_completed,
		next_step=_next_step_for_user(user),
		dashboard_path=_dashboard_path_for_account_type(user.account_type),
	)


def _build_auth_response_for_user(user: User) -> AuthResponse:
	"""Create a signed auth response for one persisted user."""

	access_token, expires_at = generate_access_token(str(user.id))
	return AuthResponse(
		access_token=access_token,
		token_type="bearer",
		expires_at=expires_at,
		user=_serialize_auth_user(user),
	)


async def _find_account_by_email(
	*,
	session: AsyncSession,
	email: str,
) -> Account | None:
	"""Look up one account row by normalized email."""

	result = await session.execute(select(Account).where(Account.email == email))
	return result.scalar_one_or_none()


async def _find_user_by_email(
	*,
	session: AsyncSession,
	email: str,
) -> User | None:
	"""Look up one auth user row by normalized email."""

	result = await session.execute(select(User).options(selectinload(User.account)).where(User.email == email))
	return result.scalar_one_or_none()


async def _get_auditor_profile_for_account(
	*,
	session: AsyncSession,
	account_id: uuid.UUID,
) -> Auditor | None:
	"""Return the auditor profile tied to one account when it exists."""

	result = await session.execute(select(Auditor).where(Auditor.account_id == account_id).limit(1))
	return result.scalar_one_or_none()


async def _ensure_playspace_auditor_profile(
	*,
	session: AsyncSession,
	user: User,
	email: str,
	clean_name: str | None,
) -> None:
	"""Create or link the auditor profile required for Playspace auditor sessions."""

	if user.account_id is None:
		raise HTTPException(status_code=400, detail="Auditor accounts require an account link.")

	auditor_profile = await _get_auditor_profile_for_account(
		session=session,
		account_id=user.account_id,
	)
	full_name = clean_name or user.name or email.split("@", 1)[0]
	if auditor_profile is None:
		session.add(
			Auditor(
				account_id=user.account_id,
				user_id=user.id,
				auditor_code=await _generate_unique_auditor_code(session),
				email=email,
				full_name=full_name,
			)
		)
		return

	if auditor_profile.user_id is None:
		auditor_profile.user_id = user.id
	if auditor_profile.email is None:
		auditor_profile.email = email
	if not auditor_profile.full_name or not auditor_profile.full_name.strip():
		auditor_profile.full_name = full_name


async def _playspace_signup(
	*,
	payload: SignupRequest,
	session: AsyncSession,
) -> AuthResponse:
	"""Create or attach a Playspace user and return an authenticated session."""

	email = _normalize_email(payload.email)
	if not email:
		raise HTTPException(status_code=400, detail="Email is required.")

	if len(payload.password) < 8:
		raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

	account_type = payload.account_type or AccountType.MANAGER
	if account_type == AccountType.ADMIN:
		raise HTTPException(
			status_code=403,
			detail="Admin accounts cannot be created through public signup.",
		)

	clean_name = _clean_name(payload.name)
	password_hash = hash_password(payload.password)
	now = datetime.now(timezone.utc)
	existing_user = await _find_user_by_email(session=session, email=email)
	if existing_user is not None:
		raise HTTPException(status_code=409, detail="An account with this email already exists.")

	existing_account = await _find_account_by_email(session=session, email=email)
	resolved_account_type = existing_account.account_type if existing_account is not None else account_type
	if existing_account is not None and resolved_account_type != account_type:
		raise HTTPException(
			status_code=409,
			detail="An account with this email already exists under a different role.",
		)

	account_name = (
		_manager_account_name(clean_name, email)
		if resolved_account_type == AccountType.MANAGER
		else (clean_name or email.split("@", 1)[0])
	)
	account = existing_account
	if account is None:
		account = Account(
			name=account_name,
			email=email,
			password_hash=password_hash,
			account_type=resolved_account_type,
		)
		session.add(account)
		await session.flush()
	else:
		account.password_hash = password_hash

	user = User(
		email=email,
		password_hash=password_hash,
		account_id=account.id,
		account_type=resolved_account_type,
		name=clean_name,
		email_verified=True,
		email_verified_at=now,
		failed_login_attempts=0,
		approved=True,
		approved_at=now,
		profile_completed=clean_name is not None,
		profile_completed_at=now if clean_name is not None else None,
		last_login_at=now,
	)
	session.add(user)
	if resolved_account_type == AccountType.AUDITOR:
		await _ensure_playspace_auditor_profile(
			session=session,
			user=user,
			email=email,
			clean_name=clean_name,
		)
	try:
		await session.commit()
	except IntegrityError as err:
		await session.rollback()
		raise HTTPException(status_code=409, detail="Unable to create account.") from err

	result = await session.execute(select(User).options(selectinload(User.account)).where(User.id == user.id))
	created_user = result.scalar_one()
	return _build_auth_response_for_user(created_user)


async def _playspace_login(
	*,
	payload: LoginRequest,
	session: AsyncSession,
) -> AuthResponse:
	"""Authenticate one Playspace user with a signed user session."""

	email = _normalize_email(payload.email)
	user = await _find_user_by_email(session=session, email=email)
	if user is None or not verify_password(payload.password, user.password_hash):
		if user is not None:
			user.failed_login_attempts += 1
			await session.commit()
		raise HTTPException(status_code=401, detail="Invalid email or password.")

	user.failed_login_attempts = 0
	user.last_login_at = datetime.now(timezone.utc)
	await session.commit()
	result = await session.execute(select(User).options(selectinload(User.account)).where(User.id == user.id))
	authenticated_user = result.scalar_one()
	return _build_auth_response_for_user(authenticated_user)


def _raise_playspace_auth_not_supported(*, feature_name: str) -> None:
	"""Reject YEE-only auth endpoints when called from Playspace routes."""

	raise HTTPException(
		status_code=404,
		detail=f"{feature_name} is not supported for Playspace authentication.",
	)


async def _get_current_yee_user(
	*,
	credentials: HTTPAuthorizationCredentials | None,
	session: AsyncSession,
) -> User:
	"""Resolve the current YEE auth user from a bearer token."""

	if credentials is None or credentials.scheme.lower() != "bearer":
		raise HTTPException(status_code=401, detail="Authentication required.")

	user_id = verify_access_token(credentials.credentials)
	if user_id is None:
		raise HTTPException(status_code=401, detail="Invalid or expired access token.")

	try:
		parsed_user_id = uuid.UUID(user_id)
	except ValueError as err:
		raise HTTPException(status_code=401, detail="Invalid access token payload.") from err

	user = await session.get(User, parsed_user_id, options=(selectinload(User.account),))
	if user is None:
		raise HTTPException(status_code=401, detail="User not found.")

	return user


async def get_current_user(
	credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
	session: AsyncSession = Depends(get_auth_session),
) -> User:
	"""Resolve the current authenticated user from a bearer token."""

	return await _get_current_yee_user(credentials=credentials, session=session)


def _build_verify_url(*, request: FastAPIRequest, token: str) -> str:
	template = os.getenv("AUTH_VERIFY_URL_TEMPLATE", "").strip()
	if template:
		return template.format(token=token)

	product_prefix = "/playspace" if request.url.path.startswith("/playspace/") else "/yee"
	base = str(request.base_url).rstrip("/")
	query = urlencode({"token": token})
	return f"{base}{product_prefix}/auth/verify-email?{query}"


def _manager_account_name(name: str | None, email: str) -> str:
	if name and name.strip():
		return f"{name.strip()}'s Workspace"
	return f"{email.split('@', 1)[0]}'s Workspace"


def _build_invite_url(*, request: FastAPIRequest, token: str) -> str:
	template = os.getenv("AUTH_INVITE_URL_TEMPLATE", "").strip()
	if template:
		return template.format(token=token)

	base = str(request.base_url).rstrip("/")
	return f"{base}/invite/{token}"


async def _get_valid_invite(session: AsyncSession, token: str) -> AuditorInvite:
	token_hash = hash_verification_token(token.strip())
	result = await session.execute(select(AuditorInvite).where(AuditorInvite.token_hash == token_hash))
	invite = result.scalar_one_or_none()
	if invite is None:
		raise HTTPException(status_code=404, detail="Invite not found.")
	if invite.accepted_at is not None:
		raise HTTPException(status_code=400, detail="Invite has already been accepted.")
	if datetime.now(timezone.utc) > invite.expires_at:
		raise HTTPException(status_code=400, detail="Invite has expired.")
	return invite


async def _generate_unique_auditor_code(session: AsyncSession) -> str:
	existing_codes = (await session.execute(select(Auditor.auditor_code))).scalars().all()
	max_suffix = 0
	for existing_code in existing_codes:
		match = re.search(r"(\d+)$", existing_code or "")
		if match is not None:
			max_suffix = max(max_suffix, int(match.group(1)))
	return f"AUD{max_suffix + 1:03d}"


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


@router.post(
	"/signup",
	response_model=AuthResponse | SignupResponse,
	status_code=status.HTTP_201_CREATED,
)
async def signup(
	payload: SignupRequest,
	request: FastAPIRequest,
	session: AsyncSession = Depends(get_auth_session),
) -> AuthResponse | SignupResponse:
	"""Create account in DB and send email verification link."""

	if payload.website and payload.website.strip():
		raise HTTPException(status_code=400, detail="Spam check failed.")

	if _is_playspace_request(request):
		return await _playspace_signup(payload=payload, session=session)

	_verify_turnstile_if_enabled(
		captcha_token=payload.captcha_token,
		remote_ip=request.client.host if request.client else None,
	)

	email = _normalize_email(payload.email)
	if not email:
		raise HTTPException(status_code=400, detail="Email is required.")

	if len(payload.password) < 8:
		raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

	account_type = payload.account_type or AccountType.MANAGER
	if account_type == AccountType.ADMIN:
		raise HTTPException(
			status_code=403,
			detail="Admin accounts cannot be created through public signup.",
		)

	password_hash = hash_password(payload.password)
	now = datetime.now(timezone.utc)
	approved = account_type == AccountType.MANAGER
	account_name = (
		_manager_account_name(_clean_name(payload.name), email) if account_type == AccountType.MANAGER else None
	)

	existing_result = await session.execute(select(User).where(User.email == email))
	existing_user = existing_result.scalar_one_or_none()

	if existing_user is not None and existing_user.email_verified:
		raise HTTPException(status_code=409, detail="An account with this email already exists.")

	if existing_user is None:
		account = None
		if account_name is not None:
			account = Account(
				name=account_name,
				email=email,
				password_hash=password_hash,
				account_type=AccountType.MANAGER,
			)
			session.add(account)
			await session.flush()

		user = User(
			email=email,
			password_hash=password_hash,
			account_id=account.id if account is not None else None,
			account_type=account_type,
			name=_clean_name(payload.name),
			email_verified=False,
			failed_login_attempts=0,
			approved=approved,
			approved_at=now if approved else None,
			profile_completed=False,
		)
		session.add(user)
		try:
			await session.flush()
		except IntegrityError as err:
			await session.rollback()
			raise HTTPException(status_code=409, detail="Unable to create account.") from err
	else:
		user = existing_user
		if account_name is not None and user.account_id is None:
			account = Account(
				name=account_name,
				email=email,
				password_hash=password_hash,
				account_type=AccountType.MANAGER,
			)
			session.add(account)
			await session.flush()
			user.account_id = account.id
		user.password_hash = password_hash
		user.account_type = account_type
		user.name = _clean_name(payload.name)
		user.email_verified = False
		user.email_verified_at = None
		user.approved = approved
		user.approved_at = now if approved else None
		user.profile_completed = False
		user.profile_completed_at = None

	await _send_or_log_verification_email(request=request, user=user, session=session)

	return SignupResponse(message="Account created. Please verify your email before logging in.")


@router.get("/verify-email", response_model=MessageResponse)
async def verify_email(
	token: str,
	request: FastAPIRequest,
	session: AsyncSession = Depends(get_auth_session),
) -> MessageResponse:
	"""Verify a user email address using token sent by email."""

	if _is_playspace_request(request):
		_raise_playspace_auth_not_supported(feature_name="Email verification")

	token_hash = hash_verification_token(token.strip())
	result = await session.execute(select(User).where(User.email_verification_token_hash == token_hash))
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

	if _is_playspace_request(request):
		_raise_playspace_auth_not_supported(feature_name="Verification resend")

	_verify_turnstile_if_enabled(
		captcha_token=payload.captcha_token,
		remote_ip=request.client.host if request.client else None,
	)

	email = _normalize_email(payload.email)
	result = await session.execute(select(User).options(selectinload(User.account)).where(User.email == email))
	user = result.scalar_one_or_none()

	if user is None or user.email_verified:
		return MessageResponse(message="If your email exists, a verification link has been sent.")

	await _send_or_log_verification_email(request=request, user=user, session=session)
	return MessageResponse(message="If your email exists, a verification link has been sent.")


@router.post("/login", response_model=AuthResponse)
async def login(
	payload: LoginRequest,
	request: FastAPIRequest,
	session: AsyncSession = Depends(get_auth_session),
) -> AuthResponse:
	"""Authenticate user with password and verified email requirement."""

	if payload.website and payload.website.strip():
		raise HTTPException(status_code=400, detail="Spam check failed.")

	if _is_playspace_request(request):
		return await _playspace_login(payload=payload, session=session)

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
	result = await session.execute(select(User).options(selectinload(User.account)).where(User.id == user.id))
	user = result.scalar_one()

	return AuthResponse(
		access_token=token,
		token_type="bearer",
		expires_at=expires_at,
		user=_serialize_auth_user(user),
	)


@router.get("/me", response_model=SessionResponse)
async def get_current_session(
	request: FastAPIRequest,
	credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
	session: AsyncSession = Depends(get_auth_session),
) -> SessionResponse:
	"""Return the current authenticated user and routing state."""

	if _is_playspace_request(request):
		user = await get_current_user(credentials=credentials, session=session)
		return SessionResponse(user=_serialize_auth_user(user))

	user = await _get_current_yee_user(credentials=credentials, session=session)
	return SessionResponse(user=_serialize_auth_user(user))


@router.post("/complete-profile", response_model=SessionResponse)
async def complete_profile(
	payload: CompleteProfileRequest,
	request: FastAPIRequest,
	credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
	session: AsyncSession = Depends(get_auth_session),
) -> SessionResponse:
	"""Mark a verified user's basic profile as completed."""

	clean_name = _clean_name(payload.name)
	if clean_name is None:
		raise HTTPException(status_code=400, detail="Name is required.")

	if _is_playspace_request(request):
		user = await get_current_user(credentials=credentials, session=session)
		user.name = clean_name
		user.profile_completed = True
		user.profile_completed_at = datetime.now(timezone.utc)
		await session.commit()
		result = await session.execute(select(User).options(selectinload(User.account)).where(User.id == user.id))
		refreshed_user = result.scalar_one()
		return SessionResponse(user=_serialize_auth_user(refreshed_user))

	user = await _get_current_yee_user(credentials=credentials, session=session)
	if not user.email_verified:
		raise HTTPException(status_code=403, detail="Email must be verified before completing profile.")
	if not user.approved:
		raise HTTPException(
			status_code=403,
			detail="Account approval is required before completing profile.",
		)

	user.name = clean_name
	user.profile_completed = True
	user.profile_completed_at = datetime.now(timezone.utc)
	await session.commit()
	result = await session.execute(select(User).options(selectinload(User.account)).where(User.id == user.id))
	user = result.scalar_one()

	return SessionResponse(user=_serialize_auth_user(user))


@router.get("/invite/{token}", response_model=InvitePreviewResponse)
async def get_invite_preview(
	token: str,
	request: FastAPIRequest,
	session: AsyncSession = Depends(get_auth_session),
) -> InvitePreviewResponse:
	"""Validate an auditor invite token and return display-safe invite info."""

	if _is_playspace_request(request):
		_raise_playspace_auth_not_supported(feature_name="Invite preview")

	invite = await _get_valid_invite(session, token)
	account = await session.get(Account, invite.account_id)
	return InvitePreviewResponse(
		email=invite.email,
		organization=account.name if account is not None else None,
		expires_at=invite.expires_at,
		accepted=False,
	)


@router.post("/invite/{token}/accept", response_model=AuthResponse)
async def accept_invite(
	token: str,
	payload: AcceptInviteRequest,
	request: FastAPIRequest,
	session: AsyncSession = Depends(get_auth_session),
) -> AuthResponse:
	"""Accept an auditor invite, create/link the user, and return an authenticated session."""

	if _is_playspace_request(request):
		_raise_playspace_auth_not_supported(feature_name="Invite acceptance")

	invite = await _get_valid_invite(session, token)
	email = _normalize_email(invite.email)
	clean_name = _clean_name(payload.name)
	if clean_name is None:
		raise HTTPException(status_code=400, detail="Name is required.")

	user_result = await session.execute(select(User).options(selectinload(User.account)).where(User.email == email))
	user = user_result.scalar_one_or_none()
	if user is not None and user.account_type == AccountType.MANAGER:
		raise HTTPException(status_code=409, detail="This email is already used by a manager account.")

	now = datetime.now(timezone.utc)
	if user is None:
		user = User(
			email=email,
			password_hash=hash_password(payload.password),
			account_id=invite.account_id,
			account_type=AccountType.AUDITOR,
			name=clean_name,
			email_verified=True,
			email_verified_at=now,
			failed_login_attempts=0,
			approved=True,
			approved_at=now,
			profile_completed=False,
		)
		session.add(user)
		await session.flush()
	else:
		user.password_hash = hash_password(payload.password)
		user.account_id = invite.account_id
		user.account_type = AccountType.AUDITOR
		user.name = clean_name
		user.email_verified = True
		user.email_verified_at = now
		user.approved = True
		user.approved_at = now
		user.profile_completed = False
		user.profile_completed_at = None

	auditor = await session.get(Auditor, invite.auditor_id) if invite.auditor_id is not None else None
	if auditor is None:
		auditor = Auditor(
			account_id=invite.account_id,
			auditor_code=await _generate_unique_auditor_code(session),
			user_id=user.id,
		)
		session.add(auditor)
		await session.flush()
		invite.auditor_id = auditor.id
	else:
		auditor.user_id = user.id

	invite.accepted_at = now
	token_value, expires_at = generate_access_token(str(user.id))
	user.last_login_at = now
	await session.commit()
	result = await session.execute(select(User).options(selectinload(User.account)).where(User.id == user.id))
	user = result.scalar_one()

	return AuthResponse(
		access_token=token_value,
		token_type="bearer",
		expires_at=expires_at,
		user=_serialize_auth_user(user),
	)

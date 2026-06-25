"""Authentication endpoints with DB-backed users and email verification."""

from __future__ import annotations

import os
import re
import uuid
import hashlib
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
	generate_password_reset_token,
	get_verification_ttl_hours,
	hash_password,
	hash_verification_token,
	verify_access_token,
	verify_password_reset_token,
	verify_password,
)
from app.database import ASYNC_SESSION_FACTORY_BY_PRODUCT, ProductKey
from app.demo_accounts import is_protected_yee_demo_email
from app import email_service
from app.email_service import send_password_reset_email, send_verification_email
from app.models import (
	Account,
	AccountType,
	Auditor,
	AuditorAccessRequest,
	AuditorInvite,
	ManagerInvite,
	ManagerProfile,
	User,
)

router: APIRouter = APIRouter(prefix="/auth", tags=["auth"])
bearer_scheme = HTTPBearer(auto_error=False)
send_manager_invite_email = email_service.send_manager_invite_email


def _reject_protected_demo_user_mutation(*, email: str | None, detail: str) -> None:
	"""Block mutations that would drift protected seeded YEE demo accounts."""

	if is_protected_yee_demo_email(email):
		raise HTTPException(status_code=409, detail=detail)


class SignupRequest(BaseModel):
	email: str = Field(..., max_length=320)
	password: str = Field(..., min_length=8, max_length=4096)
	name: str | None = Field(default=None, max_length=200)
	organization: str | None = Field(default=None, max_length=200)
	account_type: AccountType | None = Field(default=None)
	confirm_new_organization: bool = False
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


class ForgotPasswordRequest(BaseModel):
	email: str = Field(..., max_length=320)
	captcha_token: str | None = Field(default=None, max_length=4096)
	website: str | None = Field(default=None, max_length=200)


class ResetPasswordRequest(BaseModel):
	token: str = Field(..., min_length=1, max_length=4096)
	password: str = Field(..., min_length=8, max_length=4096)
	website: str | None = Field(default=None, max_length=200)


class AuthUser(BaseModel):
	id: uuid.UUID
	email: str
	name: str | None = None
	account_id: uuid.UUID | None = None
	organization: str | None = None
	account_type: AccountType
	is_primary_manager: bool = False
	has_auditor_profile: bool = False
	auditor_dashboard_path: str | None = None
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
	job_title: str | None = Field(default=None, max_length=200)
	profession_disciplines: list[str] = Field(default_factory=list)
	organization: str | None = Field(default=None, max_length=200)
	phone_number: str | None = Field(default=None, max_length=50)


class InvitePreviewResponse(BaseModel):
	email: str
	organization: str | None = None
	expires_at: datetime
	accepted: bool


class AcceptInviteRequest(BaseModel):
	name: str = Field(..., min_length=1, max_length=200)
	password: str = Field(..., min_length=8, max_length=4096)


class ManagerInvitePreviewResponse(BaseModel):
	email: str
	organization: str | None = None
	invited_by_name: str | None = None
	expires_at: datetime
	accepted: bool


class AcceptManagerInviteRequest(BaseModel):
	name: str = Field(..., min_length=1, max_length=200)
	password: str = Field(..., min_length=8, max_length=4096)
	position: str | None = Field(default=None, max_length=200)


class AccessRequestRequest(BaseModel):
	name: str = Field(..., min_length=1, max_length=200)
	email: str = Field(..., max_length=320)
	password: str = Field(..., min_length=8, max_length=4096)
	manager_email: str = Field(..., max_length=320)


class AccessRequestResponse(BaseModel):
	message: str
	email: str


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


async def _is_primary_manager(session: AsyncSession, user: User) -> bool:
	if user.account_type != AccountType.MANAGER or user.account_id is None:
		return False

	result = await session.execute(
		select(ManagerProfile.is_primary).where(
			ManagerProfile.user_id == user.id,
			ManagerProfile.account_id == user.account_id,
		)
	)
	return bool(result.scalar_one_or_none())


async def _has_auditor_profile(session: AsyncSession, user: User) -> bool:
	"""Return whether the user already has an auditor profile row."""

	result = await session.execute(select(Auditor.id).where(Auditor.user_id == user.id).limit(1))
	return result.scalar_one_or_none() is not None


async def _serialize_auth_user(session: AsyncSession, user: User) -> AuthUser:
	has_auditor_profile = await _has_auditor_profile(session, user)
	return AuthUser(
		id=user.id,
		email=user.email,
		name=user.name,
		account_id=user.account_id,
		organization=(user.account.name if "account" in user.__dict__ and user.account is not None else None),
		account_type=user.account_type,
		is_primary_manager=await _is_primary_manager(session, user),
		has_auditor_profile=has_auditor_profile,
		auditor_dashboard_path="/my-dashboard" if has_auditor_profile else None,
		email_verified=user.email_verified,
		approved=user.approved,
		profile_completed=user.profile_completed,
		next_step=_next_step_for_user(user),
		dashboard_path=_dashboard_path_for_account_type(user.account_type),
	)


async def _build_auth_response_for_user(session: AsyncSession, user: User) -> AuthResponse:
	"""Create a signed auth response for one persisted user."""

	access_token, expires_at = generate_access_token(str(user.id))
	return AuthResponse(
		access_token=access_token,
		token_type="bearer",
		expires_at=expires_at,
		user=await _serialize_auth_user(session, user),
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


async def _get_auditor_profile_for_user(
	*,
	session: AsyncSession,
	user_id: uuid.UUID,
) -> Auditor | None:
	"""Return the auditor profile tied to one user when it exists.

	Keyed by ``user_id`` because multiple auditors now share the same
	``account_id`` (the manager's organisation account).
	"""

	result = await session.execute(select(Auditor).where(Auditor.user_id == user_id).limit(1))
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

	auditor_profile = await _get_auditor_profile_for_user(
		session=session,
		user_id=user.id,
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


async def _ensure_manager_profile_for_user(
	*,
	session: AsyncSession,
	user: User,
	email: str,
	clean_name: str | None,
	prefer_primary: bool,
	position: str | None = None,
	profession_disciplines: list[str] | None = None,
) -> None:
	"""Create or link one manager profile row to the authenticated manager user."""

	if user.account_type != AccountType.MANAGER or user.account_id is None:
		return

	normalized_email = _normalize_email(email)
	full_name = clean_name or _clean_name(user.name) or normalized_email.split("@", 1)[0]

	profile_result = await session.execute(select(ManagerProfile).where(ManagerProfile.user_id == user.id))
	manager_profile = profile_result.scalar_one_or_none()
	if manager_profile is None:
		by_email_result = await session.execute(
			select(ManagerProfile).where(
				ManagerProfile.account_id == user.account_id,
				ManagerProfile.email == normalized_email,
			)
		)
		manager_profile = by_email_result.scalar_one_or_none()
	if manager_profile is None and prefer_primary:
		primary_result = await session.execute(
			select(ManagerProfile).where(
				ManagerProfile.account_id == user.account_id,
				ManagerProfile.is_primary.is_(True),
				ManagerProfile.user_id.is_(None),
			)
		)
		manager_profile = primary_result.scalar_one_or_none()

	if manager_profile is None:
		has_primary_result = await session.execute(
			select(ManagerProfile.id)
			.where(
				ManagerProfile.account_id == user.account_id,
				ManagerProfile.is_primary.is_(True),
			)
			.limit(1)
		)
		has_primary = has_primary_result.scalar_one_or_none() is not None
		session.add(
			ManagerProfile(
				account_id=user.account_id,
				user_id=user.id,
				full_name=full_name,
				email=normalized_email,
				is_primary=prefer_primary and not has_primary,
				position=position,
				profession_disciplines=list(profession_disciplines or []),
				organization=user.account.name if "account" in user.__dict__ and user.account is not None else None,
			)
		)
		return

	if manager_profile.account_id != user.account_id:
		raise HTTPException(status_code=409, detail="Manager profile is linked to a different account.")
	if manager_profile.user_id is None:
		manager_profile.user_id = user.id
	if not manager_profile.full_name or not manager_profile.full_name.strip():
		manager_profile.full_name = full_name
	if not manager_profile.email or not manager_profile.email.strip():
		manager_profile.email = normalized_email
	if (profession_disciplines is not None) and (not manager_profile.profession_disciplines):
		manager_profile.profession_disciplines = list(profession_disciplines)
	if (not manager_profile.organization or not manager_profile.organization.strip()) and (
		"user" in manager_profile.__dict__ or "account" in user.__dict__
	):
		manager_profile.organization = (
			user.account.name if "account" in user.__dict__ and user.account is not None else None
		)
	if prefer_primary and not manager_profile.is_primary:
		has_primary_result = await session.execute(
			select(ManagerProfile.id)
			.where(
				ManagerProfile.account_id == user.account_id,
				ManagerProfile.is_primary.is_(True),
				ManagerProfile.id != manager_profile.id,
			)
			.limit(1)
		)
		has_other_primary = has_primary_result.scalar_one_or_none() is not None
		if not has_other_primary:
			manager_profile.is_primary = True


async def _get_manager_profile_for_user(
	*,
	session: AsyncSession,
	user: User,
) -> ManagerProfile | None:
	"""Return the current manager profile row for one manager user."""

	if user.account_type != AccountType.MANAGER:
		return None
	result = await session.execute(select(ManagerProfile).where(ManagerProfile.user_id == user.id).limit(1))
	return result.scalar_one_or_none()


async def _unlink_secondary_manager_from_account(
	*,
	session: AsyncSession,
	user: User,
	manager_profile: ManagerProfile,
) -> None:
	"""Remove one secondary manager from their current organization/account."""

	if manager_profile.is_primary:
		raise HTTPException(status_code=409, detail="Primary managers cannot create a second organization.")

	invite_rows = (
		await session.execute(
			select(ManagerInvite).where(
				ManagerInvite.account_id == manager_profile.account_id,
				ManagerInvite.email == user.email,
			)
		)
	).scalars()
	for invite in invite_rows:
		await session.delete(invite)

	await session.delete(manager_profile)
	user.account_id = None
	user.profile_completed = False
	user.profile_completed_at = None


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
	if account_type != AccountType.MANAGER:
		raise HTTPException(
			status_code=403,
			detail="Auditor accounts must be invited by a manager.",
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
			account_type=resolved_account_type,
		)
		session.add(account)
		await session.flush()

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
	await session.flush()
	if resolved_account_type == AccountType.AUDITOR:
		await _ensure_playspace_auditor_profile(
			session=session,
			user=user,
			email=email,
			clean_name=clean_name,
		)
	if resolved_account_type == AccountType.MANAGER:
		await _ensure_manager_profile_for_user(
			session=session,
			user=user,
			email=email,
			clean_name=clean_name,
			prefer_primary=True,
		)
	try:
		await session.commit()
	except IntegrityError as err:
		await session.rollback()
		raise HTTPException(status_code=409, detail="Unable to create account.") from err

	result = await session.execute(select(User).options(selectinload(User.account)).where(User.id == user.id))
	created_user = result.scalar_one()
	return await _build_auth_response_for_user(session, created_user)


async def _playspace_request_access(
	*,
	payload: AccessRequestRequest,
	session: AsyncSession,
) -> AccessRequestResponse:
	"""Create an unapproved Playspace AUDITOR account and log an access request.

	No auth token is issued.  The auditor waits until a manager creates their
	AuditorProfile (which will approve the account and issue a temporary password).
	"""

	email = _normalize_email(payload.email)
	manager_email = _normalize_email(payload.manager_email)
	if not email:
		raise HTTPException(status_code=400, detail="Email is required.")
	if not manager_email:
		raise HTTPException(status_code=400, detail="Manager email is required.")

	clean_name = _clean_name(payload.name)
	if clean_name is None:
		raise HTTPException(status_code=400, detail="Name is required.")

	if len(payload.password) < 8:
		raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

	existing_user = await _find_user_by_email(session=session, email=email)
	if existing_user is not None:
		raise HTTPException(status_code=409, detail="An account with this email already exists.")

	existing_account = await _find_account_by_email(session=session, email=email)
	if existing_account is not None:
		raise HTTPException(status_code=409, detail="An account with this email already exists.")

	now = datetime.now(timezone.utc)
	account = Account(
		name=clean_name,
		email=email,
		account_type=AccountType.AUDITOR,
	)
	session.add(account)
	await session.flush()

	user = User(
		email=email,
		password_hash=hash_password(payload.password),
		account_id=account.id,
		account_type=AccountType.AUDITOR,
		name=clean_name,
		email_verified=True,
		email_verified_at=now,
		failed_login_attempts=0,
		approved=False,
		profile_completed=False,
	)
	session.add(user)
	await session.flush()

	access_request = AuditorAccessRequest(
		id=uuid.uuid4(),
		name=clean_name,
		email=email,
		manager_email=manager_email,
		status="pending",
	)
	session.add(access_request)

	try:
		await session.commit()
	except IntegrityError as err:
		await session.rollback()
		raise HTTPException(status_code=409, detail="Unable to create account.") from err

	return AccessRequestResponse(
		message="Access request submitted. Your manager will set up your account and share your login credentials.",
		email=email,
	)


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
	await _ensure_manager_profile_for_user(
		session=session,
		user=user,
		email=user.email,
		clean_name=_clean_name(user.name),
		prefer_primary=True,
	)
	await session.commit()
	result = await session.execute(select(User).options(selectinload(User.account)).where(User.id == user.id))
	authenticated_user = result.scalar_one()
	return await _build_auth_response_for_user(session, authenticated_user)


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

	frontend_origin = (
		request.headers.get("x-frontend-origin", "").strip()
		or request.headers.get("origin", "").strip()
		or request.headers.get("referer", "").strip()
	)
	if frontend_origin:
		base = frontend_origin.rstrip("/")
		if "/verify-email" in base:
			base = base.split("/verify-email", 1)[0]
		elif "/signup" in base:
			base = base.split("/signup", 1)[0]
		elif "/login" in base:
			base = base.split("/login", 1)[0]
		query = urlencode({"token": token})
		return f"{base}/verify-email?{query}"

	product_prefix = "/playspace" if request.url.path.startswith("/playspace/") else "/yee"
	base = str(request.base_url).rstrip("/")
	query = urlencode({"token": token})
	return f"{base}{product_prefix}/auth/verify-email?{query}"


def _build_password_reset_url(*, request: FastAPIRequest, token: str) -> str:
	template = os.getenv("AUTH_PASSWORD_RESET_URL_TEMPLATE", "").strip()
	if template:
		return template.format(token=token)

	frontend_origin = (
		request.headers.get("x-frontend-origin", "").strip()
		or request.headers.get("origin", "").strip()
		or request.headers.get("referer", "").strip()
	)
	if frontend_origin:
		base = frontend_origin.rstrip("/")
		if "/reset-password" in base:
			base = base.split("/reset-password", 1)[0]
		elif "/login" in base:
			base = base.split("/login", 1)[0]
		elif "/signup" in base:
			base = base.split("/signup", 1)[0]
		query = urlencode({"token": token})
		return f"{base}/reset-password?{query}"

	product_prefix = "/playspace" if request.url.path.startswith("/playspace/") else "/yee"
	base = str(request.base_url).rstrip("/")
	query = urlencode({"token": token})
	return f"{base}{product_prefix}/auth/reset-password?{query}"


def _manager_account_name(name: str | None, email: str) -> str:
	if name and name.strip():
		return f"{name.strip()}'s Workspace"
	return f"{email.split('@', 1)[0]}'s Workspace"


def _build_invite_url(*, request: FastAPIRequest, token: str) -> str:
	template = os.getenv("AUTH_INVITE_URL_TEMPLATE", "").strip()
	if template:
		return template.format(token=token)

	frontend_origin = (
		request.headers.get("x-frontend-origin", "").strip()
		or request.headers.get("origin", "").strip()
		or request.headers.get("referer", "").strip()
	)
	if frontend_origin:
		base = frontend_origin.rstrip("/")
		if "/invite/" in base:
			base = base.split("/invite/", 1)[0]
		elif "/login" in base:
			base = base.split("/login", 1)[0]
		elif "/signup" in base:
			base = base.split("/signup", 1)[0]
		return f"{base}/invite/{token}"

	base = str(request.base_url).rstrip("/")
	return f"{base}/invite/{token}"


def _build_manager_invite_url(*, request: FastAPIRequest, token: str) -> str:
	template = os.getenv("AUTH_MANAGER_INVITE_URL_TEMPLATE", "").strip()
	if template:
		return template.format(token=token)

	frontend_origin = (
		request.headers.get("x-frontend-origin", "").strip()
		or request.headers.get("origin", "").strip()
		or request.headers.get("referer", "").strip()
	)
	if frontend_origin:
		base = frontend_origin.rstrip("/")
		if "/manager-invite/" in base:
			base = base.split("/manager-invite/", 1)[0]
		elif "/login" in base:
			base = base.split("/login", 1)[0]
		elif "/signup" in base:
			base = base.split("/signup", 1)[0]
		return f"{base}/manager-invite/{token}"

	base = str(request.base_url).rstrip("/")
	return f"{base}/manager-invite/{token}"


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


async def _get_valid_manager_invite(session: AsyncSession, token: str) -> ManagerInvite:
	token_hash = hash_verification_token(token.strip())
	result = await session.execute(select(ManagerInvite).where(ManagerInvite.token_hash == token_hash))
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
	clean_name = _clean_name(payload.name)
	clean_organization = _clean_name(payload.organization)
	profile_completed = clean_name is not None
	email_verification_required = True
	next_step = "VERIFY_EMAIL"
	account_name = clean_organization if account_type == AccountType.MANAGER else None
	if account_type == AccountType.MANAGER and clean_organization is None:
		raise HTTPException(status_code=400, detail="Organization name is required for manager signup.")

	existing_result = await session.execute(select(User).where(User.email == email))
	existing_user = existing_result.scalar_one_or_none()

	if existing_user is not None:
		_reject_protected_demo_user_mutation(
			email=existing_user.email,
			detail="Protected demo accounts cannot be modified through public signup.",
		)

	existing_account = await _find_account_by_email(session=session, email=email)
	if existing_account is not None and existing_user is None:
		_reject_protected_demo_user_mutation(
			email=existing_account.email,
			detail="Protected demo accounts cannot be modified through public signup.",
		)
		raise HTTPException(status_code=409, detail="An account with this email already exists.")

	if existing_user is not None and existing_user.email_verified:
		if account_type != AccountType.MANAGER or existing_user.account_type != AccountType.MANAGER:
			raise HTTPException(status_code=409, detail="An account with this email already exists.")
		if existing_user.account_id is not None:
			account = await session.get(Account, existing_user.account_id)
			manager_profile = await _get_manager_profile_for_user(session=session, user=existing_user)
			if manager_profile is None:
				is_effective_primary = account is not None and _normalize_email(account.email) == email
				if is_effective_primary:
					raise HTTPException(status_code=409, detail="This manager already leads an organization.")
				if not payload.confirm_new_organization:
					organization_name = account.name if account is not None else "this organization"
					raise HTTPException(
						status_code=409,
						detail=(
							f"You are currently a manager in {organization_name}. "
							"Creating a new organization will remove you from that organization. "
							"Are you sure you want to continue?"
						),
					)
			if manager_profile is not None and not manager_profile.is_primary and not payload.confirm_new_organization:
				organization_name = account.name if account is not None else "this organization"
				raise HTTPException(
					status_code=409,
					detail=(
						f"You are currently a manager in {organization_name}. "
						"Creating a new organization will remove you from that organization. "
						"Are you sure you want to continue?"
					),
				)
			if manager_profile is not None and manager_profile.is_primary:
				raise HTTPException(status_code=409, detail="This manager already leads an organization.")

	if existing_user is None:
		account = None
		if account_name is not None:
			account = Account(
				name=account_name,
				email=email,
				account_type=AccountType.MANAGER,
			)
			session.add(account)
			await session.flush()

		user = User(
			email=email,
			password_hash=password_hash,
			account_id=account.id if account is not None else None,
			account_type=account_type,
			name=clean_name,
			email_verified=False,
			failed_login_attempts=0,
			approved=approved,
			approved_at=now if approved else None,
			profile_completed=False if account_type == AccountType.MANAGER else profile_completed,
			profile_completed_at=None if account_type == AccountType.MANAGER else now if profile_completed else None,
		)
		session.add(user)
		try:
			await session.flush()
		except IntegrityError as err:
			await session.rollback()
			raise HTTPException(status_code=409, detail="Unable to create account.") from err
	else:
		user = existing_user
		if account_type == AccountType.MANAGER and user.account_id is not None:
			manager_profile = await _get_manager_profile_for_user(session=session, user=user)
			if manager_profile is not None and not manager_profile.is_primary:
				await _unlink_secondary_manager_from_account(
					session=session,
					user=user,
					manager_profile=manager_profile,
				)
		if account_name is not None:
			if user.account_id is None:
				account = Account(
					name=account_name,
					email=email,
					account_type=AccountType.MANAGER,
				)
				session.add(account)
				await session.flush()
				user.account_id = account.id
			else:
				account = await session.get(Account, user.account_id)
				if account is not None:
					account.name = account_name
		user.password_hash = password_hash
		user.account_type = account_type
		user.name = clean_name
		if existing_user.email_verified and account_type == AccountType.MANAGER and payload.confirm_new_organization:
			user.email_verified = True
			email_verification_required = False
			next_step = "COMPLETE_PROFILE"
		else:
			user.email_verified = False
			user.email_verified_at = None
		user.approved = approved
		user.approved_at = now if approved else None
		user.profile_completed = False if account_type == AccountType.MANAGER else profile_completed
		user.profile_completed_at = None if account_type == AccountType.MANAGER else now if profile_completed else None

	if user.account_type == AccountType.MANAGER:
		await _ensure_manager_profile_for_user(
			session=session,
			user=user,
			email=email,
			clean_name=clean_name,
			prefer_primary=True,
		)

	if email_verification_required:
		await _send_or_log_verification_email(request=request, user=user, session=session)
		return SignupResponse(message="Account created. Please verify your email before logging in.")

	# An already-verified manager confirming a new organization skips email
	# verification, so persist the org switch here (the request session does not
	# auto-commit). Without this, the unlink/relink is silently discarded.
	await session.commit()
	return SignupResponse(
		message="Organization created. Complete your manager profile to continue.",
		email_verification_required=False,
		next_step=next_step,
	)


@router.post(
	"/request-access",
	response_model=AccessRequestResponse,
	status_code=status.HTTP_201_CREATED,
)
async def request_access(
	payload: AccessRequestRequest,
	request: FastAPIRequest,
	session: AsyncSession = Depends(get_auth_session),
) -> AccessRequestResponse:
	"""Create an unapproved Playspace auditor account and send an access request to the manager."""

	if not _is_playspace_request(request):
		raise HTTPException(status_code=404, detail="Not found.")

	return await _playspace_request_access(payload=payload, session=session)


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


@router.post("/forgot-password", response_model=MessageResponse)
async def forgot_password(
	payload: ForgotPasswordRequest,
	request: FastAPIRequest,
	session: AsyncSession = Depends(get_auth_session),
) -> MessageResponse:
	"""Send a password-reset email when the account exists."""

	if payload.website and payload.website.strip():
		return MessageResponse(message="If your email exists, a password reset link has been sent.")

	if _is_playspace_request(request):
		_raise_playspace_auth_not_supported(feature_name="Password reset")

	_verify_turnstile_if_enabled(
		captcha_token=payload.captcha_token,
		remote_ip=request.client.host if request.client else None,
	)

	email = _normalize_email(payload.email)
	result = await session.execute(select(User).options(selectinload(User.account)).where(User.email == email))
	user = result.scalar_one_or_none()

	if user is None or not user.email_verified:
		return MessageResponse(message="If your email exists, a password reset link has been sent.")
	if is_protected_yee_demo_email(user.email):
		return MessageResponse(message="If your email exists, a password reset link has been sent.")

	reset_token, _ = generate_password_reset_token(str(user.id), user.password_hash)
	reset_url = _build_password_reset_url(request=request, token=reset_token)
	send_password_reset_email(to_email=user.email, reset_url=reset_url)
	return MessageResponse(message="If your email exists, a password reset link has been sent.")


@router.post("/reset-password", response_model=MessageResponse)
async def reset_password(
	payload: ResetPasswordRequest,
	request: FastAPIRequest,
	session: AsyncSession = Depends(get_auth_session),
) -> MessageResponse:
	"""Reset a user's password from a signed reset token."""

	if payload.website and payload.website.strip():
		raise HTTPException(status_code=400, detail="Spam check failed.")

	if _is_playspace_request(request):
		_raise_playspace_auth_not_supported(feature_name="Password reset")

	if len(payload.password) < 8:
		raise HTTPException(status_code=400, detail="Password must be at least 8 characters.")

	token_payload = verify_password_reset_token(payload.token)
	if token_payload is None:
		raise HTTPException(status_code=400, detail="Invalid or expired password reset token.")

	user_id_raw, expected_fingerprint = token_payload
	try:
		user_id = uuid.UUID(user_id_raw)
	except ValueError as err:
		raise HTTPException(status_code=400, detail="Invalid password reset token payload.") from err

	user = await session.get(User, user_id)
	if user is None:
		raise HTTPException(status_code=400, detail="Invalid or expired password reset token.")
	if is_protected_yee_demo_email(user.email):
		raise HTTPException(
			status_code=409, detail="Protected demo accounts cannot reset their password from email links."
		)

	if expected_fingerprint != hashlib.sha256(user.password_hash.encode("utf-8")).hexdigest()[:24]:
		raise HTTPException(status_code=400, detail="This password reset link is no longer valid.")

	user.password_hash = hash_password(payload.password)
	user.failed_login_attempts = 0
	await session.commit()

	return MessageResponse(message="Password reset successful. You can now log in with your new password.")


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
	await _ensure_manager_profile_for_user(
		session=session,
		user=user,
		email=user.email,
		clean_name=_clean_name(user.name),
		prefer_primary=True,
	)
	await session.commit()
	result = await session.execute(select(User).options(selectinload(User.account)).where(User.id == user.id))
	user = result.scalar_one()

	return AuthResponse(
		access_token=token,
		token_type="bearer",
		expires_at=expires_at,
		user=await _serialize_auth_user(session, user),
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
		await _ensure_manager_profile_for_user(
			session=session,
			user=user,
			email=user.email,
			clean_name=_clean_name(user.name),
			prefer_primary=True,
		)
		await session.commit()
		return SessionResponse(user=await _serialize_auth_user(session, user))

	user = await _get_current_yee_user(credentials=credentials, session=session)
	await _ensure_manager_profile_for_user(
		session=session,
		user=user,
		email=user.email,
		clean_name=_clean_name(user.name),
		prefer_primary=True,
	)
	await session.commit()
	return SessionResponse(user=await _serialize_auth_user(session, user))


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
		return SessionResponse(user=await _serialize_auth_user(session, refreshed_user))

	user = await _get_current_yee_user(credentials=credentials, session=session)
	if not user.email_verified:
		raise HTTPException(status_code=403, detail="Email must be verified before completing profile.")
	if not user.approved:
		raise HTTPException(
			status_code=403,
			detail="Account approval is required before completing profile.",
		)

	user.name = clean_name
	if user.account_type == AccountType.MANAGER:
		manager_profile = await _get_manager_profile_for_user(session=session, user=user)
		if manager_profile is None:
			raise HTTPException(status_code=404, detail="Manager profile not found.")

		job_title = _clean_name(payload.job_title)
		organization_name = _clean_name(payload.organization)
		phone_number = _clean_name(payload.phone_number)
		profession_disciplines = [value.strip() for value in payload.profession_disciplines if value.strip()]
		if job_title is None:
			raise HTTPException(status_code=400, detail="Job title / role is required.")
		if not profession_disciplines:
			raise HTTPException(status_code=400, detail="Profession / discipline is required.")
		if organization_name is None:
			raise HTTPException(status_code=400, detail="Organization name is required.")
		if manager_profile.is_primary and phone_number is None:
			raise HTTPException(status_code=400, detail="Phone number is required for the primary manager.")
		if user.account is not None and manager_profile.is_primary:
			user.account.name = organization_name
		elif user.account is not None and organization_name != user.account.name:
			raise HTTPException(status_code=400, detail="Secondary managers cannot change the organization name.")

		manager_profile.full_name = clean_name
		manager_profile.position = job_title
		manager_profile.profession_disciplines = profession_disciplines
		manager_profile.organization = organization_name
		manager_profile.phone = phone_number
		manager_profile.email = user.email
		user.profile_completed = True
		user.profile_completed_at = datetime.now(timezone.utc)
	else:
		user.profile_completed = True
		user.profile_completed_at = datetime.now(timezone.utc)
	await session.commit()
	result = await session.execute(select(User).options(selectinload(User.account)).where(User.id == user.id))
	user = result.scalar_one()

	return SessionResponse(user=await _serialize_auth_user(session, user))


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
	if user is not None:
		_reject_protected_demo_user_mutation(
			email=user.email,
			detail="Protected demo accounts cannot be repurposed through auditor invites.",
		)

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
		user=await _serialize_auth_user(session, user),
	)


@router.get("/manager-invites/{token}", response_model=ManagerInvitePreviewResponse)
async def get_manager_invite_preview(
	token: str,
	session: AsyncSession = Depends(get_auth_session),
) -> ManagerInvitePreviewResponse:
	"""Validate a manager invite token and return display-safe invite context.

	Returns the invitee email, organisation name, inviting manager's display
	name, and expiry so the acceptance page can show context without the user
	needing to fill in the form first.
	"""
	invite = await _get_valid_manager_invite(session, token)

	account = await session.get(Account, invite.account_id)
	organization_name = account.name if account is not None else None

	invited_by_name: str | None = None
	inviter_profile_result = await session.execute(
		select(ManagerProfile).where(ManagerProfile.user_id == invite.invited_by_user_id)
	)
	inviter_profile = inviter_profile_result.scalar_one_or_none()
	if inviter_profile is not None:
		invited_by_name = inviter_profile.full_name

	return ManagerInvitePreviewResponse(
		email=invite.email,
		organization=organization_name,
		invited_by_name=invited_by_name,
		expires_at=invite.expires_at,
		accepted=False,
	)


@router.post("/manager-invites/{token}/accept", response_model=AuthResponse)
async def accept_manager_invite(
	token: str,
	payload: AcceptManagerInviteRequest,
	session: AsyncSession = Depends(get_auth_session),
) -> AuthResponse:
	"""Accept a manager invite and return an authenticated manager session."""

	invite = await _get_valid_manager_invite(session, token)
	email = _normalize_email(invite.email)
	_reject_protected_demo_user_mutation(
		email=email,
		detail="Protected demo accounts cannot be repurposed through manager invites.",
	)
	clean_name = _clean_name(payload.name)
	if clean_name is None:
		raise HTTPException(status_code=400, detail="Name is required.")

	user_result = await session.execute(select(User).options(selectinload(User.account)).where(User.email == email))
	user = user_result.scalar_one_or_none()
	if user is not None:
		_reject_protected_demo_user_mutation(
			email=user.email,
			detail="Protected demo accounts cannot be repurposed through manager invites.",
		)
	if user is not None and user.account_type != AccountType.MANAGER:
		raise HTTPException(status_code=409, detail="This email is already used by a non-manager account.")
	if user is not None and user.account_id == invite.account_id:
		raise HTTPException(status_code=409, detail="This manager already has account access.")
	if user is not None and user.account_id not in {None, invite.account_id}:
		raise HTTPException(status_code=409, detail="This email is already linked to another manager account.")

	now = datetime.now(timezone.utc)
	if user is None:
		user = User(
			email=email,
			password_hash=hash_password(payload.password),
			account_id=invite.account_id,
			account_type=AccountType.MANAGER,
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
		user.account_type = AccountType.MANAGER
		user.name = clean_name
		user.email_verified = True
		user.email_verified_at = now
		user.approved = True
		user.approved_at = now
		user.profile_completed = False
		user.profile_completed_at = None

	await _ensure_manager_profile_for_user(
		session=session,
		user=user,
		email=email,
		clean_name=clean_name,
		prefer_primary=False,
		position=payload.position or None,
	)
	invite.accepted_at = now
	invite.accepted_by_user_id = user.id
	token_value, expires_at = generate_access_token(str(user.id))
	user.last_login_at = now
	await session.commit()
	result = await session.execute(select(User).options(selectinload(User.account)).where(User.id == user.id))
	user = result.scalar_one()

	return AuthResponse(
		access_token=token_value,
		token_type="bearer",
		expires_at=expires_at,
		user=await _serialize_auth_user(session, user),
	)

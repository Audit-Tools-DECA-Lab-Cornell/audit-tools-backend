"""Authentication and token security helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
from datetime import datetime, timedelta, timezone

PBKDF2_ITERATIONS = 390_000


def hash_password(password: str) -> str:
    """Hash a password using PBKDF2-HMAC-SHA256."""

    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        PBKDF2_ITERATIONS,
    )
    salt_b64 = base64.urlsafe_b64encode(salt).decode("utf-8")
    digest_b64 = base64.urlsafe_b64encode(digest).decode("utf-8")
    return f"pbkdf2_sha256${PBKDF2_ITERATIONS}${salt_b64}${digest_b64}"


def verify_password(password: str, stored_hash: str) -> bool:
    """Verify a password against a stored PBKDF2 hash."""

    try:
        algorithm, iterations_raw, salt_b64, digest_b64 = stored_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(iterations_raw)
        salt = base64.urlsafe_b64decode(salt_b64.encode("utf-8"))
        expected_digest = base64.urlsafe_b64decode(digest_b64.encode("utf-8"))
    except Exception:
        return False

    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        iterations,
    )
    return hmac.compare_digest(actual_digest, expected_digest)


def generate_email_verification_token() -> str:
    """Generate a random token suitable for email verification links."""

    return secrets.token_urlsafe(32)


def hash_verification_token(token: str) -> str:
    """Hash a verification token before persisting to DB."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def generate_access_token(user_id: str) -> tuple[str, datetime]:
    """Generate a signed bearer token and expiration timestamp."""

    expires_at = datetime.now(timezone.utc) + timedelta(days=get_access_token_ttl_days())
    payload_json = json.dumps(
        {"sub": user_id, "exp": int(expires_at.timestamp())},
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    payload_b64 = base64.urlsafe_b64encode(payload_json).decode("utf-8").rstrip("=")
    signature = _sign_token_payload(payload_b64)
    token = f"session.{payload_b64}.{signature}"
    return token, expires_at


def get_verification_ttl_hours() -> int:
    """Read verification token TTL (hours) from env with safe fallback."""

    raw = os.getenv("AUTH_EMAIL_VERIFY_TTL_HOURS", "24").strip()
    try:
        ttl = int(raw)
        return ttl if ttl > 0 else 24
    except ValueError:
        return 24


def get_access_token_ttl_days() -> int:
    """Read access token TTL (days) from env with safe fallback."""

    raw = os.getenv("AUTH_ACCESS_TOKEN_TTL_DAYS", "7").strip()
    try:
        ttl = int(raw)
        return ttl if ttl > 0 else 7
    except ValueError:
        return 7


def verify_access_token(token: str) -> str | None:
    """Return the user id encoded in a token if the signature and expiry are valid."""

    parts = token.strip().split(".")
    if len(parts) != 3 or parts[0] != "session":
        return None

    _, payload_b64, provided_signature = parts
    expected_signature = _sign_token_payload(payload_b64)
    if not hmac.compare_digest(expected_signature, provided_signature):
        return None

    try:
        payload_json = _urlsafe_b64decode(payload_b64)
        payload = json.loads(payload_json)
        user_id = str(payload["sub"])
        expires_at = datetime.fromtimestamp(int(payload["exp"]), tz=timezone.utc)
    except Exception:
        return None

    if datetime.now(timezone.utc) > expires_at:
        return None

    return user_id


def _get_access_token_secret() -> bytes:
    secret = os.getenv("AUTH_TOKEN_SECRET_KEY", "").strip()
    if not secret:
        secret = "dev-insecure-auth-secret-change-me"
    return secret.encode("utf-8")


def _sign_token_payload(payload_b64: str) -> str:
    digest = hmac.new(
        _get_access_token_secret(),
        payload_b64.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")


def _urlsafe_b64decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode((value + padding).encode("utf-8"))

"""Authentication and token security helpers."""

from __future__ import annotations

import base64
import hashlib
import hmac
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
    """Generate a simple bearer token and expiration timestamp."""

    expires_at = datetime.now(timezone.utc) + timedelta(days=7)
    random_part = secrets.token_urlsafe(24)
    token = f"session-{user_id}-{random_part}"
    return token, expires_at


def get_verification_ttl_hours() -> int:
    """Read verification token TTL (hours) from env with safe fallback."""

    raw = os.getenv("AUTH_EMAIL_VERIFY_TTL_HOURS", "24").strip()
    try:
        ttl = int(raw)
        return ttl if ttl > 0 else 24
    except ValueError:
        return 24

"""Shared helpers for stable seeded YEE demo-account protection."""

from __future__ import annotations

import os

DEFAULT_YEE_DEMO_EMAILS: tuple[str, ...] = (
	"manager-demo@yee.local",
	"admin-demo@yee.local",
	"auditor-demo-1@yee.local",
	"auditor-demo-2@yee.local",
	"auditor-demo-3@yee.local",
	"farah.khan@example.org",
	"jordan.alvarez@example.org",
)


def get_protected_yee_demo_emails() -> tuple[str, ...]:
	"""Return the protected seeded YEE demo emails.

	Operators can override the list with ``PROTECTED_YEE_DEMO_EMAILS`` as a
	comma-separated env var. When unset, the deterministic seeded email set is
	protected by default so shared demo credentials remain stable.
	"""

	configured = os.getenv("PROTECTED_YEE_DEMO_EMAILS", "").strip()
	if not configured:
		return DEFAULT_YEE_DEMO_EMAILS
	emails = tuple(dict.fromkeys(part.strip().lower() for part in configured.split(",") if part.strip()))
	return emails or DEFAULT_YEE_DEMO_EMAILS


def is_protected_yee_demo_email(email: str | None) -> bool:
	"""Return True when the email belongs to a protected YEE demo identity."""

	if email is None:
		return False
	return email.strip().lower() in set(get_protected_yee_demo_emails())

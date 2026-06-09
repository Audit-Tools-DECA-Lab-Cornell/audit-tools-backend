"""Tests for transactional email delivery payload construction."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

import app.email_service.send_email as email_module


class _FakeBrevoResponse:
	"""Minimal successful Brevo response stub."""

	def raise_for_status(self) -> None:
		return None

	def json(self) -> dict[str, str]:
		return {"messageId": "test-message-id"}


@pytest.mark.parametrize(
	("send_func", "kwargs"),
	[
		(
			email_module.send_verification_email,
			{"to_email": "user@example.org", "verify_url": "https://example.org/verify"},
		),
		(
			email_module.send_password_reset_email,
			{"to_email": "reset@example.org", "reset_url": "https://example.org/reset"},
		),
		(
			email_module.send_auditor_invite_email,
			{"to_email": "auditor@example.org", "invite_url": "https://example.org/invite/auditor"},
		),
		(
			email_module.send_manager_invite_email,
			{
				"to_email": "manager@example.org",
				"invite_url": "https://example.org/invite/manager",
				"organization_name": "Example Org",
				"invited_by_name": "Admin User",
			},
		),
		(
			email_module.send_auditor_credentials_email,
			{
				"to_email": "credentials@example.org",
				"full_name": "Credential User",
				"auditor_code": "AUD-123",
				"temporary_password": "Temporary123!",
				"platform": "Playspace Audit Tools",
			},
		),
		(
			email_module.send_audit_submit_failure_email,
			{
				"to_email": "failure@example.org",
				"auditor_name": "Audit User",
				"place_name": "Central Park",
				"audit_code": "AUDIT-123",
				"project_name": "Park Reviews",
			},
		),
	],
)
def test_transactional_emails_bcc_admin_notification_email(
	monkeypatch: pytest.MonkeyPatch,
	send_func: Callable[..., bool],
	kwargs: dict[str, Any],
) -> None:
	"""Every public transactional email copies the configured admin notification address."""

	captured_payloads: list[dict[str, Any]] = []

	def _post(url: str, json: dict[str, Any], headers: dict[str, str], timeout: int) -> _FakeBrevoResponse:
		captured_payloads.append(json)
		return _FakeBrevoResponse()

	monkeypatch.setenv("BREVO_API_KEY", "test-api-key")
	monkeypatch.setenv("BREVO_SENDER_EMAIL", "sender@example.org")
	monkeypatch.setenv("ADMIN_NOTIFICATION_EMAIL", "admin@example.org")
	monkeypatch.setattr(email_module._SESSION, "post", _post)

	assert send_func(**kwargs) is True
	assert captured_payloads[-1]["bcc"] == [{"email": "admin@example.org"}]


def test_admin_notification_bcc_deduplicates_recipients(monkeypatch: pytest.MonkeyPatch) -> None:
	"""Admin BCC merging keeps explicit recipients and skips duplicate addresses."""

	monkeypatch.setenv("ADMIN_NOTIFICATION_EMAIL", "Admin@Example.org")

	assert email_module._with_admin_notification_bcc(
		to_email="user@example.org",
		bcc=["copy@example.org", "admin@example.org", "copy@example.org"],
	) == ["copy@example.org", "admin@example.org"]
	assert email_module._with_admin_notification_bcc(to_email="admin@example.org") is None

"""Email delivery helpers for auth workflows — powered by Brevo Transactional API."""

from __future__ import annotations

import logging
import os

import requests

from dotenv import find_dotenv, load_dotenv
from app.email_service.templates import credentials_html, invite_html, verification_html


load_dotenv(find_dotenv())
logger = logging.getLogger(__name__)

_BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"


def _send_email(
	*,
	to_email: str,
	bcc: list[str] | None = None,
	subject: str,
	body: str,
	html_body: str | None = None,
	log_label: str,
	fallback_url: str,
) -> bool:
	api_key = os.getenv("BREVO_API_KEY", "").strip()
	sender_email = os.getenv("BREVO_SENDER_EMAIL", "").strip()
	sender_name = os.getenv("BREVO_SENDER_NAME", "Audit Tools").strip()

	if not api_key or not sender_email:
		logger.warning("Brevo not configured. %s for %s: %s", log_label, to_email, fallback_url)
		return False

	payload: dict = {
		"sender": {"name": sender_name, "email": sender_email},
		"to": [{"email": to_email}],
		"bcc": [{"email": email} for email in bcc] if bcc else None,
		"subject": subject,
		"textContent": body,
	}
	if html_body:
		payload["htmlContent"] = html_body

	headers = {
		"accept": "application/json",
		"content-type": "application/json",
		"api-key": api_key,
	}

	try:
		response = requests.post(_BREVO_API_URL, json=payload, headers=headers, timeout=15)
		response.raise_for_status()
		logger.info("Email sent via Brevo to %s (messageId=%s)", to_email, response.json().get("messageId"))
		return True
	except requests.HTTPError as e:
		logger.error("Brevo API error sending to %s: %s — %s", to_email, e, e.response.text)
		return False
	except Exception:
		logger.exception("Failed to send email to %s", to_email)
		return False


def send_verification_email(*, to_email: str, verify_url: str) -> bool:
	"""Send email verification link."""
	return _send_email(
		to_email=to_email,
		subject="Verify your Audit Tools account",
		body=(
			"Welcome to Audit Tools!\n\n"
			"Please verify your email address by clicking the link below:\n"
			f"{verify_url}\n\n"
			"If you did not request this account, you can ignore this email."
		),
		html_body=verification_html(verify_url),
		log_label="Verification link",
		fallback_url=verify_url,
	)


def send_auditor_invite_email(*, to_email: str, invite_url: str) -> bool:
	"""Send an auditor invite email."""
	return _send_email(
		to_email=to_email,
		subject="You have been invited to Audit Tools",
		body=(
			"You have been invited to join Audit Tools as an auditor.\n\n"
			"Open the invite link below to create your account and continue setup:\n"
			f"{invite_url}\n\n"
			"If you were not expecting this invite, you can ignore this email."
		),
		html_body=invite_html(invite_url, "auditor"),
		log_label="Auditor invite link",
		fallback_url=invite_url,
	)


def send_manager_invite_email(*, to_email: str, invite_url: str) -> bool:
	"""Send a manager invite email."""
	return _send_email(
		to_email=to_email,
		subject="You have been invited to manage an Audit Tools workspace",
		body=(
			"You have been invited to join an Audit Tools workspace as a manager.\n\n"
			"Open the invite link below to set your password and continue setup:\n"
			f"{invite_url}\n\n"
			"If you were not expecting this invite, you can ignore this email."
		),
		html_body=invite_html(invite_url, "manager"),
		log_label="Manager invite link",
		fallback_url=invite_url,
	)


def send_auditor_credentials_email(
	*,
	to_email: str,
	full_name: str,
	auditor_code: str,
	temporary_password: str,
	platform: str,
) -> bool:
	"""Email a newly created auditor their login credentials.

	Delivers to ``to_email`` (the auditor) and, if configured, a second copy
	to ``ADMIN_NOTIFICATION_EMAIL``. Returns True only if every send succeeds.
	"""
	body = (
		f"Hello {full_name},\n\n"
		"Your Playspace auditor account has been created.\n\n"
		f"  Email:              {to_email}\n"
		f"  Auditor code:       {auditor_code}\n"
		f"  Temporary password: {temporary_password}\n\n"
		"Please sign in and change your password as soon as possible.\n\n"
		"If you were not expecting this account, contact your administrator."
	)

	product = platform.split(" ")[0]
	admin_notification_email = os.getenv("ADMIN_NOTIFICATION_EMAIL", "").strip()
	sent = _send_email(
		to_email=to_email,
		bcc=[admin_notification_email] if admin_notification_email else None,
		subject=f"Your {product} Auditor Account Credentials",
		body=body,
		html_body=credentials_html(full_name, to_email, auditor_code, temporary_password, platform, product),
		log_label="Auditor credentials",
		fallback_url="",
	)

	return sent

"""Email delivery helpers for auth workflows — powered by Brevo Transactional API."""

from __future__ import annotations

import hashlib
import logging
import os
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from dotenv import find_dotenv, load_dotenv
from app.email_service.templates import credentials_html, invite_html, verification_html


load_dotenv(find_dotenv())
logger = logging.getLogger(__name__)

_BREVO_API_URL = "https://api.brevo.com/v3/smtp/email"

# Retries on connection failures, 429 rate-limit, and transient 5xx server errors.
# Brevo's Retry-After header (present on 429) is respected automatically.
# Backoff schedule: 1 s, 2 s, 4 s (3 total attempts).
_RETRY_STRATEGY: Retry = Retry(
	total=3,
	backoff_factor=1.0,
	status_forcelist={429, 500, 502, 503, 504},
	allowed_methods={"POST"},
	respect_retry_after_header=True,
	raise_on_status=False,
)
# A persistent session reuses the underlying TCP connection and applies the retry
# adapter to every HTTPS request made by this module.
_SESSION = requests.Session()
_SESSION.mount("https://", HTTPAdapter(max_retries=_RETRY_STRATEGY))


def _stable_entity_id(email_type: str, to_email: str) -> str:
	"""Return a deterministic per-type/per-recipient hex digest for X-Entity-Ref-ID.

	Gmail uses this header to prevent identical transactional emails (e.g. repeated
	verification requests to the same address) from threading into one conversation or
	being reclassified as promotional bulk mail.
	"""
	raw = f"{email_type}:{to_email.lower()}".encode()
	return hashlib.sha256(raw).hexdigest()[:32]


def _send_email(
	*,
	to_email: str,
	bcc: list[str] | None = None,
	subject: str,
	body: str,
	html_body: str | None = None,
	log_label: str,
	fallback_url: str,
	email_type: str,
	tags: list[str],
	track_clicks: bool = True,
	track_opens: bool = True,
) -> bool:
	api_key = os.getenv("BREVO_API_KEY", "").strip()
	sender_email = os.getenv("BREVO_SENDER_EMAIL", "").strip()
	sender_name = os.getenv("BREVO_SENDER_NAME", "Audit Tools").strip()
	# Falls back to the sender address when no separate reply-to is configured,
	# but set BREVO_REPLY_TO_EMAIL to a monitored inbox in production.
	reply_to_email = os.getenv("BREVO_REPLY_TO_EMAIL", sender_email).strip()

	if not api_key or not sender_email:
		logger.warning("Brevo not configured. %s for %s: %s", log_label, to_email, fallback_url)
		return False

	payload: dict = {
		"sender": {"name": sender_name, "email": sender_email},
		"to": [{"email": to_email}],
		"subject": subject,
		"textContent": body,
		# Brevo per-message tracking flags: 1 = enabled, 0 = disabled, 2 = account default.
		# Setting these explicitly ensures tracking behaviour is consistent regardless
		# of what the account-level default is set to.
		"trackOpens": 1 if track_opens else 0,
		"trackClicks": 1 if track_clicks else 0,
		# Tags appear in Brevo's transactional analytics dashboard and can be used to
		# filter event logs, build per-email-type delivery/open/click reports, and
		# trigger webhook routing rules.
		"tags": tags,
		# Routes manual replies to a monitored inbox rather than a no-reply bounce address.
		"replyTo": {"email": reply_to_email},
		"headers": {
			# Prevents Gmail from folding repeated same-type transactional emails (e.g.
			# multiple verification or invite sends) into the same conversation thread and
			# reduces the risk of promotional-tab misclassification by breaking the visual
			# pattern of identical sender+subject combinations.
			"X-Entity-Ref-ID": _stable_entity_id(email_type, to_email),
			# Identifies the originating application in raw message headers for support
			# debugging and Brevo event log correlation.
			"X-Mailer": "AuditTools/1.0 Brevo",
		},
	}

	if html_body:
		payload["htmlContent"] = html_body
	# Only include the BCC key when there are actual addresses — sending bcc: null
	# is valid JSON but results in a Brevo 400 on some API versions.
	if bcc:
		payload["bcc"] = [{"email": addr} for addr in bcc]

	headers = {
		"accept": "application/json",
		"content-type": "application/json",
		"api-key": api_key,
	}

	try:
		t0 = time.monotonic()
		response = _SESSION.post(_BREVO_API_URL, json=payload, headers=headers, timeout=15)
		elapsed_ms = round((time.monotonic() - t0) * 1000)
		response.raise_for_status()
		message_id = response.json().get("messageId", "")
		logger.info(
			"Email sent via Brevo: type=%s to=%s messageId=%s elapsed_ms=%d tags=%s",
			email_type,
			to_email,
			message_id,
			elapsed_ms,
			tags,
		)
		return True
	except requests.HTTPError as exc:
		status_code = exc.response.status_code if exc.response is not None else 0
		body_snippet = exc.response.text[:500] if exc.response is not None else ""
		logger.error(
			"Brevo API error: type=%s to=%s status=%d body=%s",
			email_type,
			to_email,
			status_code,
			body_snippet,
		)
		return False
	except Exception:
		logger.exception("Failed to send email: type=%s to=%s", email_type, to_email)
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
		email_type="email_verification",
		tags=["auth", "verification"],
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
		email_type="auditor_invite",
		tags=["auth", "invite", "auditor"],
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
		email_type="manager_invite",
		tags=["auth", "invite", "manager"],
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
	return _send_email(
		to_email=to_email,
		bcc=[admin_notification_email] if admin_notification_email else None,
		subject=f"Your {product} Auditor Account Credentials",
		body=body,
		html_body=credentials_html(full_name, to_email, auditor_code, temporary_password, platform, product),
		log_label="Auditor credentials",
		fallback_url="",
		email_type="auditor_credentials",
		tags=["onboarding", "credentials", "auditor"],
	)

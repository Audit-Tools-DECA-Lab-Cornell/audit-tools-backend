"""Email delivery helpers — Brevo Transactional API with Gmail SMTP fallback."""

from __future__ import annotations

import hashlib
import logging
import os
import smtplib
import time
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from dotenv import find_dotenv, load_dotenv
from app.email_service.templates import credentials_html, invite_html, submit_failure_html, verification_html


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


def _send_email_via_smtp(
	*,
	to_email: str,
	bcc: list[str] | None = None,
	subject: str,
	body: str,
	html_body: str | None = None,
	log_label: str,
	fallback_url: str,
	email_type: str,
) -> bool:
	"""Send an email via a generic SMTP relay (e.g. Gmail App Password).

	Reads configuration from the following environment variables:
	SMTP_HOST        — SMTP server hostname (default: smtp.gmail.com)
	SMTP_PORT        — SMTP port as a string (default: 587)
	SMTP_USERNAME    — SMTP login username
	SMTP_PASSWORD    — SMTP login password / App Password
	SMTP_FROM_EMAIL  — Envelope and From-header address
	SMTP_USE_TLS     — "true" to use STARTTLS (default: true)

	Returns True on success; logs a warning and returns False when
	credentials are absent or sending fails.
	"""
	smtp_host = os.getenv("SMTP_HOST", "smtp.gmail.com").strip()
	smtp_port_raw = os.getenv("SMTP_PORT", "587").strip()
	smtp_user = os.getenv("SMTP_USERNAME", "").strip()
	smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
	smtp_from = os.getenv("SMTP_FROM_EMAIL", "").strip()
	use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() not in {"false", "0", "no"}

	if not smtp_user or not smtp_password or not smtp_from:
		logger.warning(
			"SMTP not configured (missing SMTP_USERNAME / SMTP_PASSWORD / SMTP_FROM_EMAIL). %s for %s: %s",
			log_label,
			to_email,
			fallback_url,
		)
		return False

	try:
		smtp_port = int(smtp_port_raw)
	except ValueError:
		logger.error("SMTP_PORT is not a valid integer: %r", smtp_port_raw)
		return False

	# Build a multipart/alternative message so clients can display either
	# the plain-text or HTML version according to their capabilities.
	recipients = [to_email] + (bcc or [])
	message = MIMEMultipart("alternative")
	message["Subject"] = subject
	message["From"] = smtp_from
	message["To"] = to_email
	# BCC recipients are intentionally omitted from the headers; SMTP RCPT TO
	# handles delivery to them without exposing their addresses.
	message.attach(MIMEText(body, "plain", "utf-8"))
	if html_body:
		message.attach(MIMEText(html_body, "html", "utf-8"))

	try:
		t0 = time.monotonic()
		with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
			smtp.ehlo()
			if use_tls:
				smtp.starttls()
			smtp.login(smtp_user, smtp_password)
			smtp.sendmail(smtp_from, recipients, message.as_string())
		elapsed_ms = round((time.monotonic() - t0) * 1000)
		logger.info(
			"Email sent via SMTP: type=%s to=%s host=%s elapsed_ms=%d",
			email_type,
			to_email,
			smtp_host,
			elapsed_ms,
		)
		return True
	except smtplib.SMTPAuthenticationError:
		logger.error(
			"SMTP authentication failed for %s@%s — check SMTP_PASSWORD.",
			smtp_user,
			smtp_host,
		)
		return False
	except Exception:
		logger.exception("SMTP send failed: type=%s to=%s host=%s", email_type, to_email, smtp_host)
		return False


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
		# Brevo not configured — attempt SMTP fallback before giving up.
		return _send_email_via_smtp(
			to_email=to_email,
			bcc=bcc,
			subject=subject,
			body=body,
			html_body=html_body,
			log_label=log_label,
			fallback_url=fallback_url,
			email_type=email_type,
		)

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


def send_manager_invite_email(
	*,
	to_email: str,
	invite_url: str,
	organization_name: str | None = None,
	invited_by_name: str | None = None,
) -> bool:
	"""Send a manager invite email.

	Optionally include the workspace organisation name and the inviting
	manager's display name so the recipient can immediately identify who and
	what org sent the invitation.
	"""
	org_line = f" from {organization_name}" if organization_name else ""
	body_parts = [
		f"You have been invited to join an Audit Tools workspace{org_line} as a manager.",
		"",
		"Open the invite link below to set your password and continue setup:",
		invite_url,
		"",
	]
	if invited_by_name:
		body_parts.append(f"Invited by: {invited_by_name}")
		body_parts.append("")
	body_parts.append("If you were not expecting this invite, you can ignore this email.")

	return _send_email(
		to_email=to_email,
		subject="You have been invited to manage an Audit Tools workspace",
		body="\n".join(body_parts),
		html_body=invite_html(
			invite_url,
			"manager",
			organization_name=organization_name,
			invited_by_name=invited_by_name,
		),
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


def send_audit_submit_failure_email(
	*,
	to_email: str,
	auditor_name: str,
	place_name: str,
	audit_code: str,
	project_name: str,
) -> bool:
	"""Notify an auditor that their offline-queued audit submission failed.

	Fired by the backend when the mobile app's background sync could not
	submit the audit automatically. The auditor is directed to resubmit
	manually from within the Playspace app.
	"""
	body = (
		f"Hello {auditor_name},\n\n"
		"We were unable to automatically submit your audit after it was queued "
		"while your device was offline. Your audit data is safely saved on your "
		"device.\n\n"
		f"  Place:      {place_name}\n"
		f"  Project:    {project_name}\n"
		f"  Audit Code: {audit_code}\n\n"
		"Please open the Playspace app and submit the audit manually.\n\n"
		"If the problem persists, contact your manager."
	)
	return _send_email(
		to_email=to_email,
		subject="Action required: Your audit could not be submitted",
		body=body,
		html_body=submit_failure_html(
			auditor_name=auditor_name,
			place_name=place_name,
			audit_code=audit_code,
			project_name=project_name,
		),
		log_label="Audit submit failure",
		fallback_url="",
		email_type="audit_submit_failure",
		tags=["audit", "submit_failure", "auditor"],
		track_clicks=False,
	)

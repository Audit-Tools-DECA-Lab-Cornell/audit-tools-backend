"""SMS delivery helpers via Twilio for credential notifications.

Required env vars (all optional — service silently no-ops when absent):
  TWILIO_ACCOUNT_SID     — Twilio account SID
  TWILIO_AUTH_TOKEN      — Twilio auth token
  TWILIO_FROM_PHONE      — E.164 sending number (e.g. +12125551234)
  ADMIN_NOTIFICATION_PHONE — E.164 destination for admin copies (e.g. +16072794794)
"""

from __future__ import annotations

import logging
import os

from twilio.rest import Client  # type: ignore[import-untyped,import-not-found]

logger = logging.getLogger(__name__)


def send_auditor_credentials_sms(
	*,
	full_name: str,
	to_email: str,
	auditor_code: str,
	temporary_password: str,
) -> bool:
	"""Text new-auditor credentials to ``ADMIN_NOTIFICATION_PHONE`` via Twilio.

	Intended for admin/testing visibility only — the auditor's own credentials
	are delivered via email.  Silently returns ``False`` when any required env
	var is absent so a missing Twilio configuration never blocks account creation.
	"""

	account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
	auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
	from_phone = os.getenv("TWILIO_FROM_PHONE", "").strip()
	to_phone = os.getenv("ADMIN_NOTIFICATION_PHONE", "").strip()

	if not all([account_sid, auth_token, from_phone, to_phone]):
		logger.warning(
			"Twilio not fully configured — SMS not sent for auditor %s.",
			auditor_code,
		)
		return False

	body = (
		f"[Playspace] New auditor created\n"
		f"Name: {full_name}\n"
		f"Email: {to_email}\n"
		f"Code: {auditor_code}\n"
		f"Temp pw: {temporary_password}"
	)

	try:
		client = Client(account_sid, auth_token)
		client.messages.create(body=body, from_=from_phone, to=to_phone)
		logger.info("Credentials SMS sent for auditor %s.", auditor_code)
		return True
	except Exception:
		logger.exception("Failed to send Twilio SMS for auditor %s.", auditor_code)
		return False

"""Email delivery helpers for auth workflows."""

from __future__ import annotations

import logging
import os
import smtplib
from email.message import EmailMessage

logger = logging.getLogger(__name__)


def send_verification_email(*, to_email: str, verify_url: str) -> bool:
    """
    Send verification email using SMTP config.

    Returns True when sent successfully. If SMTP config is missing, logs the
    verification URL and returns False.
    """

    smtp_host = os.getenv("SMTP_HOST", "").strip()
    smtp_port_raw = os.getenv("SMTP_PORT", "587").strip()
    smtp_username = os.getenv("SMTP_USERNAME", "").strip()
    smtp_password = os.getenv("SMTP_PASSWORD", "").strip()
    smtp_from = os.getenv("SMTP_FROM_EMAIL", "").strip()
    smtp_use_tls = os.getenv("SMTP_USE_TLS", "true").strip().lower() != "false"

    if not smtp_host or not smtp_from:
        logger.warning(
            "SMTP not configured. Verification link for %s: %s",
            to_email,
            verify_url,
        )
        return False

    try:
        smtp_port = int(smtp_port_raw)
    except ValueError:
        smtp_port = 587

    message = EmailMessage()
    message["Subject"] = "Verify your Audit Tools account"
    message["From"] = smtp_from
    message["To"] = to_email
    message.set_content(
        "Welcome to Audit Tools!\n\n"
        "Please verify your email address by clicking the link below:\n"
        f"{verify_url}\n\n"
        "If you did not request this account, you can ignore this email."
    )

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as server:
            if smtp_use_tls:
                server.starttls()
            if smtp_username and smtp_password:
                server.login(smtp_username, smtp_password)
            server.send_message(message)
        return True
    except Exception:
        logger.exception("Failed to send verification email to %s", to_email)
        return False

"""Email service package."""

from app.email_service.send_email import (
	send_audit_submit_failure_email,
	send_auditor_invite_email,
	send_auditor_credentials_email,
	send_manager_invite_email,
	send_password_reset_email,
	send_verification_email,
)

from app.email_service.templates import credentials_html, invite_html, password_reset_html, submit_failure_html, verification_html

__all__ = [
	"send_audit_submit_failure_email",
	"send_auditor_credentials_email",
	"send_auditor_invite_email",
	"send_manager_invite_email",
	"send_password_reset_email",
	"send_verification_email",
	"credentials_html",
	"invite_html",
	"password_reset_html",
	"submit_failure_html",
	"verification_html",
]

"""Email service package."""

from app.email_service.send_email import (
	send_auditor_invite_email,
	send_auditor_credentials_email,
	send_manager_invite_email,
	send_verification_email,
)

from app.email_service.templates import (
	credentials_html,
	invite_html,
	verification_html
)

__all__ = [
	"send_auditor_credentials_email",
	"send_auditor_invite_email",
	"send_manager_invite_email",
	"send_verification_email",
	"credentials_html",
	"invite_html",
	"verification_html"
]

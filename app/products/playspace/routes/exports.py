"""
Raw-data export support endpoints for Playspace.

The export ZIP itself is generated in the requester's browser (the PDF/Excel/JSON
generators are client-side). These endpoints provide the server-side pieces that
the browser cannot do for itself - currently, sending a completion email for
large exports via the shared email service.
"""

from __future__ import annotations

import logging
import os

from fastapi import APIRouter, Response
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.actors import CurrentUserContext, require_manager_or_admin_user
from app.email_service import send_export_ready_email
from app.models import User
from app.products.playspace.routes.dependencies import (
	CURRENT_USER_DEPENDENCY,
	SESSION_DEPENDENCY,
)
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

router = APIRouter(tags=["playspace"])


_ENTITY_LABELS = {
	"audits": "Audits",
	"reports": "Reports",
	"places": "Places",
	"projects": "Projects",
}
_FORMAT_LABELS = {"xlsx": "Excel (.xlsx)", "json": "JSON (.json)"}


class ExportReadyNotifyRequest(BaseModel):
	"""Summary of a completed raw-data export, used to compose the email."""

	entity: str = Field(pattern="^(audits|reports|places|projects)$")
	format: str = Field(pattern="^(xlsx|json)$")
	audit_count: int = Field(ge=0)
	combined_report_count: int = Field(default=0, ge=0)
	had_failures: bool = False


def _build_dashboard_url(role: str) -> str:
	"""Resolve the raw-data dashboard URL for the requester's role.

	Uses ``RAW_DATA_DASHBOARD_URL_TEMPLATE`` (a format string with a ``{role}``
	placeholder) when set, otherwise falls back to the local Next.js default.
	"""

	template = os.getenv("RAW_DATA_DASHBOARD_URL_TEMPLATE", "").strip()
	resolved = template or "http://localhost:3000/{role}/raw-data"
	return resolved.format(role=role)


@router.post(
	"/exports/notify-ready",
	status_code=204,
	response_class=Response,
	response_model=None,
)
async def notify_export_ready(
	payload: ExportReadyNotifyRequest,
	current_user: CurrentUserContext = CURRENT_USER_DEPENDENCY,
	session: AsyncSession = SESSION_DEPENDENCY,
) -> None:
	"""Email the requesting manager/admin that their raw-data export finished.

	Best-effort: always returns 204 so a delivery failure never surfaces as an
	export error in the browser. Only managers and admins reach the raw-data
	pages, so auditors are rejected.
	"""

	require_manager_or_admin_user(current_user)

	if current_user.user_id is None:
		return

	result = await session.execute(select(User.email, User.name).where(User.id == current_user.user_id).limit(1))
	row = result.first()
	if row is None or not row.email:
		return

	requester_name = (row.name or "").strip() or row.email.split("@")[0]
	role = "admin" if current_user.role.value == "admin" else "manager"

	try:
		send_export_ready_email(
			to_email=row.email,
			requester_name=requester_name,
			entity_label=_ENTITY_LABELS.get(payload.entity, payload.entity),
			format_label=_FORMAT_LABELS.get(payload.format, payload.format),
			audit_count=payload.audit_count,
			combined_report_count=payload.combined_report_count,
			dashboard_url=_build_dashboard_url(role),
			had_failures=payload.had_failures,
		)
	except Exception:  # noqa: BLE001 - email is best-effort; never block the caller.
		logger.exception("Failed to send export-ready email to %s", row.email)

"""Tests for role parsing and role-aware dashboard privacy payloads."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from app.core.actors import CurrentUserRole, resolve_current_user
from app.products.playspace.schemas.admin import AdminAuditorRowResponse
from app.products.playspace.schemas.dashboard import AuditorSummaryResponse
from app.products.playspace.services.privacy import mask_email
from starlette.requests import Request


def _build_request(headers: list[tuple[bytes, bytes]]) -> Request:
    """Build a minimal Starlette request for actor resolution tests."""

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/",
        "raw_path": b"/",
        "query_string": b"",
        "headers": headers,
        "client": ("127.0.0.1", 12345),
        "server": ("testserver", 80),
    }
    return Request(scope)


def test_mask_email_masks_local_and_domain_sections() -> None:
    """Email masking should keep format while hiding sensitive segments."""

    masked = mask_email("auditor@example.com")
    assert masked is not None
    assert masked.startswith("a")
    assert "@e" in masked
    assert masked.endswith(".com")
    assert masked != "auditor@example.com"


def test_resolve_current_user_parses_admin_role() -> None:
    """Actor resolver should accept explicit admin role headers."""

    account_id = str(uuid.uuid4())
    request = _build_request(
        headers=[
            (b"x-demo-role", b"admin"),
            (b"x-demo-account-id", account_id.encode("utf-8")),
        ]
    )

    actor = resolve_current_user(request)
    assert actor.role is CurrentUserRole.ADMIN
    assert actor.account_id is not None
    assert str(actor.account_id) == account_id


def test_auditor_summary_response_includes_manager_visible_identity() -> None:
    """Manager dashboard payloads should expose full auditor name and email."""

    payload = AuditorSummaryResponse(
        id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        auditor_code="AUD-1001",
        full_name="Alex Rivera",
        email="alex.rivera@example.com",
        age_range=None,
        gender=None,
        country=None,
        role=None,
        assignments_count=2,
        completed_audits=1,
        last_active_at=datetime.now(timezone.utc),
    )
    serialized = payload.model_dump()
    assert serialized["email"] == "alex.rivera@example.com"
    assert serialized["full_name"] == "Alex Rivera"


def test_admin_auditor_row_response_omits_raw_name_and_email() -> None:
    """Admin auditor rows should remain masked and auditor-code-first."""

    payload = AdminAuditorRowResponse(
        auditor_profile_id=uuid.uuid4(),
        account_id=uuid.uuid4(),
        auditor_code="AUD-1001",
        email_masked="a******@e******.com",
        assignments_count=2,
        completed_audits=1,
        last_active_at=datetime.now(timezone.utc),
    )
    serialized = payload.model_dump()
    assert "email_masked" in serialized
    assert "email" not in serialized
    assert "full_name" not in serialized

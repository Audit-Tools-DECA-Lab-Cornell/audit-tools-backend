"""
Integration tests: assignment creation flows and notification side effects.

Validates that successful assignments emit notifications and failed writes do not.
"""

from __future__ import annotations

import asyncio
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Notification
from tests.products.playspace.test_api_endpoints import (
    _bearer_headers,
    _create_place,
    _create_project,
    _login_auditor,
    _login_manager,
    _unique_suffix,
)


async def _count_all_notifications(session: AsyncSession) -> int:
    """Return total notification rows (used for before/after comparisons)."""

    result = await session.execute(select(func.count()).select_from(Notification))
    return int(result.scalar_one())


class TestAssignmentNotificationIntegration:
    """End-to-end checks for assignment creation and notification rows."""

    def test_assignment_creation_creates_notification(
        self,
        playspace_client: TestClient,
    ) -> None:
        """Creating an assignment for a linked auditor user inserts one notification."""

        suffix = _unique_suffix()
        manager_token = _login_manager(playspace_client)
        manager_headers = _bearer_headers(manager_token)
        project = _create_project(playspace_client, manager_token, suffix=suffix)
        place = _create_place(
            playspace_client,
            manager_token,
            project_id=str(project["id"]),
            suffix=suffix,
        )
        auditor_email = f"assign-notif-{suffix}@example.org"
        auditor_full_name = f"Assign Notif {suffix}"
        auditor_code = f"ASN-{suffix.upper()}"

        # Manager creates account + user + AuditorProfile. Do not call signup first:
        # public auditor signup also creates an AuditorProfile for that email, so a
        # second POST here would hit "auditor_code or email is already in use."
        profile_response = playspace_client.post(
            "/playspace/auditor-profiles",
            headers=manager_headers,
            json={
                "email": auditor_email,
                "full_name": auditor_full_name,
                "auditor_code": auditor_code,
                "country": "New Zealand",
                "role": "Tester",
            },
        )
        assert profile_response.status_code == 201
        auditor_profile_id = str(profile_response.json()["id"])

        auditor_token = _login_auditor(playspace_client, auditor_email)
        auditor_headers = _bearer_headers(auditor_token)

        assignment_response = playspace_client.post(
            f"/playspace/auditor-profiles/{auditor_profile_id}/assignments",
            headers=manager_headers,
            json={
                "project_id": project["id"],
                "place_id": place["id"],
            },
        )
        assert assignment_response.status_code == 201
        assignment_payload = assignment_response.json()
        assignment_id = str(assignment_payload["id"])
        place_name = str(assignment_payload["place_name"])

        notifications_response = playspace_client.get(
            "/playspace/api/notifications",
            headers=auditor_headers,
        )
        assert notifications_response.status_code == 200
        items = notifications_response.json()
        assert isinstance(items, list)
        matching = [
            row
            for row in items
            if row.get("notification_type") == "ASSIGNMENT_CREATED"
            and row.get("related_entity_id") == assignment_id
        ]
        assert len(matching) == 1
        assert place_name in matching[0].get("message", "")

    def test_invalid_assignment_does_not_create_notification(
        self,
        playspace_client: TestClient,
        playspace_test_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """A failed assignment request leaves the notification table unchanged."""

        async def _count() -> int:
            async with playspace_test_session_factory() as session:
                return await _count_all_notifications(session)

        before = asyncio.run(_count())

        manager_token = _login_manager(playspace_client)
        manager_headers = _bearer_headers(manager_token)
        fake_profile_id = str(uuid.uuid4())
        response = playspace_client.post(
            f"/playspace/auditor-profiles/{fake_profile_id}/assignments",
            headers=manager_headers,
            json={
                "project_id": str(uuid.uuid4()),
                "place_id": str(uuid.uuid4()),
            },
        )
        assert response.status_code == 404

        after = asyncio.run(_count())
        assert after == before

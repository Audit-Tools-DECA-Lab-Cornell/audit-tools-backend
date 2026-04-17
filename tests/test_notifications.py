"""
Tests for ``NotificationService`` and the Playspace-mounted notifications REST API.

Covers pagination, unread filtering, authorization, rate limiting, and serialization.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from typing import TypeVar

from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.auth_security import generate_access_token, hash_password
from app.limiter import limiter
from app.models import AccountType, Notification, NotificationType, User
from app.notification_service import NotificationService
from tests.products.playspace.test_api_endpoints import (
    _bearer_headers,
    _signup_and_login_auditor,
    _unique_suffix,
)

T = TypeVar("T")


def _run_async(
    factory: async_sessionmaker[AsyncSession],
    coro: Callable[[AsyncSession], Awaitable[T]],
) -> T:
    """Run one async coroutine with a short-lived database session."""

    async def _inner() -> T:
        async with factory() as session:
            return await coro(session)

    return asyncio.run(_inner())


async def _insert_user(
    session: AsyncSession,
    *,
    email_suffix: str,
) -> User:
    """Stage a minimal verified auditor user in the current transaction.

    Callers must ``commit`` (or rely on a later commit) to persist the row.
    Uses ``flush`` instead of committing here so multiple users created in the
    same session stay in one transaction (avoids FK issues when inserting
    notifications that reference an earlier user in the same test).
    """

    user = User(
        id=uuid.uuid4(),
        email=f"notif-{email_suffix}-{uuid.uuid4().hex[:8]}@example.org",
        password_hash=hash_password("TestPass123!"),
        account_type=AccountType.AUDITOR,
        email_verified=True,
        approved=True,
        profile_completed=True,
    )
    session.add(user)
    await session.flush()
    await session.refresh(user)
    return user


class TestNotificationService:
    """Exercise ``NotificationService`` against the Playspace test database."""

    def test_create_assignment_notification(
        self,
        playspace_test_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Creating an assignment notification stores expected fields without committing."""

        async def _exercise(session: AsyncSession) -> None:
            user = await _insert_user(session, email_suffix="create")
            assignment_id = uuid.uuid4()
            notification = await NotificationService.create_assignment_notification(
                db=session,
                user_id=user.id,
                assignment_id=assignment_id,
                place_name="Test Place",
            )
            await session.commit()
            await session.refresh(notification)

            assert notification.user_id == user.id
            assert notification.notification_type == NotificationType.ASSIGNMENT_CREATED
            assert "Test Place" in notification.message
            assert notification.is_read is False
            assert notification.related_entity_id == assignment_id

        _run_async(playspace_test_session_factory, _exercise)

    def test_get_user_notifications_pagination(
        self,
        playspace_test_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Pagination returns disjoint pages ordered newest-first."""

        async def _exercise(session: AsyncSession) -> None:
            user = await _insert_user(session, email_suffix="page")
            for index in range(10):
                await NotificationService.create_assignment_notification(
                    db=session,
                    user_id=user.id,
                    assignment_id=uuid.uuid4(),
                    place_name=f"Place {index}",
                )
            await session.commit()

            page1 = await NotificationService.get_user_notifications(
                db=session,
                user_id=user.id,
                limit=5,
                offset=0,
            )
            page2 = await NotificationService.get_user_notifications(
                db=session,
                user_id=user.id,
                limit=5,
                offset=5,
            )
            assert len(page1) == 5
            assert len(page2) == 5
            assert page1[0].id != page2[0].id

        _run_async(playspace_test_session_factory, _exercise)

    def test_get_user_notifications_unread_only(
        self,
        playspace_test_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Unread filter returns only rows with ``is_read`` false."""

        async def _exercise(session: AsyncSession) -> None:
            user = await _insert_user(session, email_suffix="unread")
            for index in range(10):
                notification = await NotificationService.create_assignment_notification(
                    db=session,
                    user_id=user.id,
                    assignment_id=uuid.uuid4(),
                    place_name=f"Place {index}",
                )
                if index < 5:
                    notification.is_read = True
            await session.commit()

            unread = await NotificationService.get_user_notifications(
                db=session,
                user_id=user.id,
                unread_only=True,
            )
            assert len(unread) == 5
            assert all(not row.is_read for row in unread)

        _run_async(playspace_test_session_factory, _exercise)

    def test_mark_as_read(
        self,
        playspace_test_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Marking a notification read persists and returns the row."""

        async def _exercise(session: AsyncSession) -> None:
            user = await _insert_user(session, email_suffix="read")
            notification = await NotificationService.create_assignment_notification(
                db=session,
                user_id=user.id,
                assignment_id=uuid.uuid4(),
                place_name="Test Place",
            )
            await session.commit()
            assert notification.is_read is False

            result = await NotificationService.mark_as_read(
                db=session,
                notification_id=notification.id,
                user_id=user.id,
            )
            assert result is not None
            assert result.is_read is True

        _run_async(playspace_test_session_factory, _exercise)

    def test_mark_as_read_unauthorized(
        self,
        playspace_test_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Another user cannot mark another user's notification as read."""

        async def _exercise(session: AsyncSession) -> None:
            owner = await _insert_user(session, email_suffix="owner")
            other = await _insert_user(session, email_suffix="other")
            notification = await NotificationService.create_assignment_notification(
                db=session,
                user_id=owner.id,
                assignment_id=uuid.uuid4(),
                place_name="Test Place",
            )
            await session.commit()

            result = await NotificationService.mark_as_read(
                db=session,
                notification_id=notification.id,
                user_id=other.id,
            )
            assert result is None

            await session.refresh(notification)
            assert notification.is_read is False

        _run_async(playspace_test_session_factory, _exercise)

    def test_mark_all_as_read(
        self,
        playspace_test_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Mark-all-read updates every unread row for the user."""

        async def _exercise(session: AsyncSession) -> None:
            user = await _insert_user(session, email_suffix="allread")
            for index in range(5):
                await NotificationService.create_assignment_notification(
                    db=session,
                    user_id=user.id,
                    assignment_id=uuid.uuid4(),
                    place_name=f"Place {index}",
                )
            await session.commit()

            count = await NotificationService.mark_all_as_read(db=session, user_id=user.id)
            assert count == 5

            unread_count = await NotificationService.get_unread_count(db=session, user_id=user.id)
            assert unread_count == 0

        _run_async(playspace_test_session_factory, _exercise)

    def test_get_unread_count(
        self,
        playspace_test_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Unread count matches rows with ``is_read`` false."""

        async def _exercise(session: AsyncSession) -> None:
            user = await _insert_user(session, email_suffix="count")
            for index in range(5):
                notification = await NotificationService.create_assignment_notification(
                    db=session,
                    user_id=user.id,
                    assignment_id=uuid.uuid4(),
                    place_name=f"Place {index}",
                )
                if index < 2:
                    notification.is_read = True
            await session.commit()

            count = await NotificationService.get_unread_count(db=session, user_id=user.id)
            assert count == 3

        _run_async(playspace_test_session_factory, _exercise)

    def test_delete_old_notifications(
        self,
        playspace_test_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Deletion removes rows older than the cutoff while keeping recent rows."""

        async def _exercise(session: AsyncSession) -> None:
            user = await _insert_user(session, email_suffix="old")
            old_notification = Notification(
                user_id=user.id,
                message="Old notification",
                notification_type=NotificationType.ASSIGNMENT_CREATED,
                related_entity_type="assignment",
                related_entity_id=uuid.uuid4(),
                created_at=datetime.now(timezone.utc) - timedelta(days=100),
            )
            session.add(old_notification)

            recent = await NotificationService.create_assignment_notification(
                db=session,
                user_id=user.id,
                assignment_id=uuid.uuid4(),
                place_name="Recent Place",
            )
            await session.commit()
            await session.refresh(recent)

            deleted = await NotificationService.delete_old_notifications(db=session, days=90)
            assert deleted >= 1

            remaining = await NotificationService.get_user_notifications(
                db=session,
                user_id=user.id,
            )
            assert len(remaining) == 1
            assert remaining[0].id == recent.id

        _run_async(playspace_test_session_factory, _exercise)

    def test_user_delete_cascades_notifications(
        self,
        playspace_test_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Deleting a user removes their notifications (CASCADE)."""

        async def _exercise(session: AsyncSession) -> None:
            user = await _insert_user(session, email_suffix="cascade")
            await NotificationService.create_assignment_notification(
                db=session,
                user_id=user.id,
                assignment_id=uuid.uuid4(),
                place_name="Cascade Place",
            )
            await session.commit()

            result = await session.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == user.id)
            )
            assert int(result.scalar_one()) > 0

            await session.delete(user)
            await session.commit()

            result_after = await session.execute(
                select(func.count())
                .select_from(Notification)
                .where(Notification.user_id == user.id)
            )
            assert int(result_after.scalar_one()) == 0

        _run_async(playspace_test_session_factory, _exercise)


class TestNotificationAPI:
    """HTTP integration tests for ``/playspace/api/notifications`` routes."""

    def test_get_notifications_endpoint(
        self,
        playspace_client: TestClient,
        playspace_test_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """GET notifications returns a JSON list for an authenticated user."""

        async def _seed() -> str:
            async with playspace_test_session_factory() as session:
                user = await _insert_user(session, email_suffix="api-list")
                await NotificationService.create_assignment_notification(
                    db=session,
                    user_id=user.id,
                    assignment_id=uuid.uuid4(),
                    place_name="API Place",
                )
                await session.commit()
                return generate_access_token(str(user.id))[0]

        token = asyncio.run(_seed())
        response = playspace_client.get(
            "/playspace/api/notifications",
            headers=_bearer_headers(token),
        )
        assert response.status_code == 200
        body = response.json()
        assert isinstance(body, list)
        assert len(body) >= 1
        assert "message" in body[0]

    def test_get_unread_count_endpoint(
        self,
        playspace_client: TestClient,
        playspace_test_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """GET unread count returns an integer ``count`` field."""

        async def _seed() -> str:
            async with playspace_test_session_factory() as session:
                user = await _insert_user(session, email_suffix="api-count")
                await NotificationService.create_assignment_notification(
                    db=session,
                    user_id=user.id,
                    assignment_id=uuid.uuid4(),
                    place_name="Count Place",
                )
                await session.commit()
                return generate_access_token(str(user.id))[0]

        token = asyncio.run(_seed())

        response = playspace_client.get(
            "/playspace/api/notifications/unread/count",
            headers=_bearer_headers(token),
        )
        assert response.status_code == 200
        payload = response.json()
        assert "count" in payload
        assert isinstance(payload["count"], int)
        assert payload["count"] >= 1

    def test_mark_as_read_endpoint(
        self,
        playspace_client: TestClient,
        playspace_test_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """POST mark-read returns success for owned notifications."""

        async def _seed() -> tuple[str, uuid.UUID]:
            async with playspace_test_session_factory() as session:
                user = await _insert_user(session, email_suffix="api-read")
                notification = await NotificationService.create_assignment_notification(
                    db=session,
                    user_id=user.id,
                    assignment_id=uuid.uuid4(),
                    place_name="Read Place",
                )
                await session.commit()
                await session.refresh(notification)
                return generate_access_token(str(user.id))[0], notification.id

        token, notification_id = asyncio.run(_seed())

        response = playspace_client.post(
            f"/playspace/api/notifications/{notification_id}/read",
            headers=_bearer_headers(token),
        )
        assert response.status_code == 200
        assert response.json()["success"] is True

    def test_mark_as_read_unauthorized_returns_404(
        self,
        playspace_client: TestClient,
        playspace_test_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """Marking another user's notification returns 404."""

        async def _seed() -> uuid.UUID:
            async with playspace_test_session_factory() as session:
                owner = await _insert_user(session, email_suffix="api-own")
                notification = await NotificationService.create_assignment_notification(
                    db=session,
                    user_id=owner.id,
                    assignment_id=uuid.uuid4(),
                    place_name="Foreign Place",
                )
                await session.commit()
                await session.refresh(notification)
                return notification.id

        notification_id = asyncio.run(_seed())

        other_token = _signup_and_login_auditor(
            playspace_client,
            email=f"notif-intruder-{_unique_suffix()}@example.org",
            full_name="Intruder User",
            auditor_code=f"INT-{uuid.uuid4().hex[:6].upper()}",
        )

        response = playspace_client.post(
            f"/playspace/api/notifications/{notification_id}/read",
            headers=_bearer_headers(other_token),
        )
        assert response.status_code == 404

    def test_mark_all_as_read_endpoint(
        self,
        playspace_client: TestClient,
        playspace_test_session_factory: async_sessionmaker[AsyncSession],
    ) -> None:
        """POST read-all marks every notification and returns a count."""

        async def _seed() -> str:
            async with playspace_test_session_factory() as session:
                user = await _insert_user(session, email_suffix="api-all")
                for _ in range(3):
                    await NotificationService.create_assignment_notification(
                        db=session,
                        user_id=user.id,
                        assignment_id=uuid.uuid4(),
                        place_name="Bulk Place",
                    )
                await session.commit()
                return generate_access_token(str(user.id))[0]

        token = asyncio.run(_seed())

        response = playspace_client.post(
            "/playspace/api/notifications/read-all",
            headers=_bearer_headers(token),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["success"] is True
        assert body["count"] == 3

    def test_rate_limiting_on_list_notifications(self, playspace_client: TestClient) -> None:
        """Bursting the list endpoint eventually yields HTTP 429.

        Use unauthenticated requests on purpose: with a valid bearer, each call
        waits on the Playspace DB (session + user + notifications). Slow enough
        requests can span multiple one-minute fixed windows and never exceed
        ``30/minute``. Unauthenticated GETs fail fast with 401 while still
        exercising the same SlowAPI wrapper on this route.
        """

        try:
            limiter.reset()
        except (AttributeError, NotImplementedError):
            pass

        responses: list[int] = []
        for _ in range(35):
            response = playspace_client.get("/playspace/api/notifications")
            responses.append(response.status_code)

        assert 401 in responses
        assert 429 in responses

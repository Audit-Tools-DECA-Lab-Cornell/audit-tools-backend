"""
In-app notification helpers for platform users.

Transaction policy:
- create_assignment_notification only adds to the session; the caller commits.
- Methods that mutate persisted state (mark as read, delete old) commit on success.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Notification, NotificationType

logger = logging.getLogger(__name__)


class NotificationService:
    """Service for creating and querying user-scoped notifications."""

    @staticmethod
    async def create_assignment_notification(
        db: AsyncSession,
        user_id: UUID,
        assignment_id: UUID,
        place_name: str,
    ) -> Notification:
        """
        Create a notification when an assignment is created.

        Does not commit; the caller must commit the surrounding transaction.
        """

        try:
            message = f"New assignment: Audit {place_name}"
            notification = Notification(
                user_id=user_id,
                message=message,
                notification_type=NotificationType.ASSIGNMENT_CREATED,
                related_entity_type="assignment",
                related_entity_id=assignment_id,
                is_read=False,
            )
            db.add(notification)
            logger.info(
                "Created notification",
                extra={
                    "user_id": str(user_id),
                    "notification_type": NotificationType.ASSIGNMENT_CREATED.value,
                    "assignment_id": str(assignment_id),
                },
            )
            return notification
        except Exception as exc:
            logger.error(
                "Failed to create notification",
                extra={"user_id": str(user_id), "error": str(exc)},
            )
            raise

    @staticmethod
    async def get_user_notifications(
        db: AsyncSession,
        user_id: UUID,
        limit: int = 50,
        offset: int = 0,
        unread_only: bool = False,
    ) -> list[Notification]:
        """Return notifications for a user, newest first, with pagination."""

        try:
            capped_limit = min(max(limit, 1), 100)
            stmt = (
                select(Notification)
                .where(Notification.user_id == user_id)
                .order_by(Notification.created_at.desc())
                .limit(capped_limit)
                .offset(offset)
            )
            if unread_only:
                stmt = stmt.where(Notification.is_read.is_(False))

            result = await db.execute(stmt)
            notifications = list(result.scalars().all())
            logger.info(
                "Fetched notifications",
                extra={
                    "user_id": str(user_id),
                    "count": len(notifications),
                    "unread_only": unread_only,
                },
            )
            return notifications
        except Exception as exc:
            logger.error(
                "Failed to fetch notifications",
                extra={"user_id": str(user_id), "error": str(exc)},
            )
            raise

    @staticmethod
    async def mark_as_read(
        db: AsyncSession,
        notification_id: UUID,
        user_id: UUID,
    ) -> Notification | None:
        """Mark one notification as read. Returns None if missing or not owned."""

        try:
            stmt = select(Notification).where(
                Notification.id == notification_id,
                Notification.user_id == user_id,
            )
            result = await db.execute(stmt)
            notification = result.scalar_one_or_none()

            if notification is None:
                logger.warning(
                    "Notification not found or unauthorized",
                    extra={
                        "notification_id": str(notification_id),
                        "user_id": str(user_id),
                    },
                )
                return None

            notification.is_read = True
            await db.commit()
            logger.info(
                "Marked notification as read",
                extra={
                    "notification_id": str(notification_id),
                    "user_id": str(user_id),
                },
            )
            return notification
        except Exception as exc:
            await db.rollback()
            logger.error(
                "Failed to mark notification as read",
                extra={
                    "notification_id": str(notification_id),
                    "error": str(exc),
                },
            )
            raise

    @staticmethod
    async def mark_all_as_read(db: AsyncSession, user_id: UUID) -> int:
        """Mark all unread notifications for a user as read. Returns rows updated."""

        try:
            stmt = (
                update(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.is_read.is_(False),
                )
                .values(is_read=True)
            )
            result = await db.execute(stmt)
            await db.commit()
            count = result.rowcount if result.rowcount is not None else 0
            logger.info(
                "Marked all notifications as read",
                extra={"user_id": str(user_id), "count": count},
            )
            return int(count)
        except Exception as exc:
            await db.rollback()
            logger.error(
                "Failed to mark all notifications as read",
                extra={"user_id": str(user_id), "error": str(exc)},
            )
            raise

    @staticmethod
    async def get_unread_count(db: AsyncSession, user_id: UUID) -> int:
        """Return the number of unread notifications for a user."""

        try:
            stmt = (
                select(func.count())
                .select_from(Notification)
                .where(
                    Notification.user_id == user_id,
                    Notification.is_read.is_(False),
                )
            )
            result = await db.execute(stmt)
            count = result.scalar_one()
            logger.info(
                "Fetched unread count",
                extra={"user_id": str(user_id), "count": count},
            )
            return int(count)
        except Exception as exc:
            logger.error(
                "Failed to fetch unread count",
                extra={"user_id": str(user_id), "error": str(exc)},
            )
            raise

    @staticmethod
    async def delete_old_notifications(db: AsyncSession, days: int = 90) -> int:
        """Delete notifications older than ``days``. Returns rows deleted."""

        try:
            cutoff = datetime.now(timezone.utc) - timedelta(days=days)
            stmt = delete(Notification).where(Notification.created_at < cutoff)
            result = await db.execute(stmt)
            await db.commit()
            count = result.rowcount if result.rowcount is not None else 0
            logger.info(
                "Deleted old notifications",
                extra={"days": days, "count": count},
            )
            return int(count)
        except Exception as exc:
            await db.rollback()
            logger.error(
                "Failed to delete old notifications",
                extra={"days": days, "error": str(exc)},
            )
            raise

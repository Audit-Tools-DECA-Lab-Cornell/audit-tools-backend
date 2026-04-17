"""
REST API for in-app notifications (Playspace database, authenticated users).

Routes are mounted under the ``/playspace`` prefix in ``main.py`` so paths match
``get_async_session_playspace`` and bearer auth for the Playspace product.
"""

from __future__ import annotations

import logging
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import bearer_scheme, get_current_user
from app.database import get_async_session_playspace
from app.limiter import limiter
from app.models import Notification, NotificationType
from app.notification_service import NotificationService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationItemResponse(BaseModel):
    """One notification row as returned to API clients."""

    id: str
    message: str
    notification_type: str
    is_read: bool
    related_entity_type: str | None
    related_entity_id: str | None
    created_at: str


class UnreadCountResponse(BaseModel):
    """Unread notification count payload."""

    count: int


class MarkReadResponse(BaseModel):
    """Acknowledgement after marking one notification read."""

    success: bool = Field(default=True)
    message: str


class MarkAllReadResponse(BaseModel):
    """Acknowledgement after marking all notifications read."""

    success: bool = Field(default=True)
    count: int
    message: str


def _serialize_notification(n: Notification) -> NotificationItemResponse:
    """Map an ORM ``Notification`` to the public JSON shape."""

    raw_type = n.notification_type
    if isinstance(raw_type, NotificationType):
        type_str = raw_type.value
    else:
        type_str = str(raw_type)

    return NotificationItemResponse(
        id=str(n.id),
        message=n.message,
        notification_type=type_str,
        is_read=n.is_read,
        related_entity_type=n.related_entity_type,
        related_entity_id=(str(n.related_entity_id) if n.related_entity_id is not None else None),
        created_at=n.created_at.isoformat(),
    )


@router.get("", response_model=list[NotificationItemResponse])
@limiter.limit("30/minute")
async def get_notifications(
    request: Request,
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    unread_only: bool = Query(False),
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_async_session_playspace),
) -> list[NotificationItemResponse]:
    """Return notifications for the current user with pagination (newest first)."""

    current_user = await get_current_user(credentials=credentials, session=session)
    try:
        notifications = await NotificationService.get_user_notifications(
            db=session,
            user_id=current_user.id,
            limit=limit,
            offset=offset,
            unread_only=unread_only,
        )
        return [_serialize_notification(n) for n in notifications]
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch notifications: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch notifications") from exc


@router.get("/unread/count", response_model=UnreadCountResponse)
@limiter.limit("60/minute")
async def get_unread_count(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_async_session_playspace),
) -> UnreadCountResponse:
    """Return the number of unread notifications for the current user."""

    current_user = await get_current_user(credentials=credentials, session=session)
    try:
        count = await NotificationService.get_unread_count(db=session, user_id=current_user.id)
        return UnreadCountResponse(count=count)
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to fetch unread count: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to fetch unread count") from exc


@router.post("/read-all", response_model=MarkAllReadResponse)
@limiter.limit("10/minute")
async def mark_all_notifications_as_read(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_async_session_playspace),
) -> MarkAllReadResponse:
    """Mark all notifications for the current user as read."""

    current_user = await get_current_user(credentials=credentials, session=session)
    try:
        count = await NotificationService.mark_all_as_read(db=session, user_id=current_user.id)
        return MarkAllReadResponse(
            success=True,
            count=count,
            message=f"Marked {count} notifications as read",
        )
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to mark all notifications as read: %s", exc)
        raise HTTPException(
            status_code=500,
            detail="Failed to mark all notifications as read",
        ) from exc


@router.post("/{notification_id}/read", response_model=MarkReadResponse)
@limiter.limit("60/minute")
async def mark_notification_as_read(
    request: Request,
    notification_id: UUID,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    session: AsyncSession = Depends(get_async_session_playspace),
) -> MarkReadResponse:
    """Mark one notification as read (404 if missing or not owned by the user)."""

    current_user = await get_current_user(credentials=credentials, session=session)
    try:
        notification = await NotificationService.mark_as_read(
            db=session,
            notification_id=notification_id,
            user_id=current_user.id,
        )
        if notification is None:
            raise HTTPException(status_code=404, detail="Notification not found")
        return MarkReadResponse(success=True, message="Notification marked as read")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Failed to mark notification as read: %s", exc)
        raise HTTPException(status_code=500, detail="Failed to mark notification as read") from exc

"""Add notifications table for in-app user notifications.

Revision ID: 20260416_0007
Revises: 20260416_0016
Create Date: 2026-04-16 17:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "20260416_0007"
down_revision: str | None = "20260416_0016"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

NOTIFICATION_TYPE_ENUM = postgresql.ENUM(
    "ASSIGNMENT_CREATED",
    "ASSIGNMENT_UPDATED",
    "AUDIT_COMPLETED",
    name="notification_type_enum",
    create_type=False,
)


def upgrade() -> None:
    """Create notification_type_enum, notifications table, and supporting indexes."""

    op.execute(
        sa.text(
            "CREATE TYPE notification_type_enum AS ENUM "
            "('ASSIGNMENT_CREATED', 'ASSIGNMENT_UPDATED', 'AUDIT_COMPLETED')"
        )
    )

    op.create_table(
        "notifications",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("message", sa.String(length=500), nullable=False),
        sa.Column("notification_type", NOTIFICATION_TYPE_ENUM, nullable=False),
        sa.Column(
            "is_read",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("related_entity_type", sa.String(length=50), nullable=True),
        sa.Column("related_entity_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )

    op.create_index("ix_notifications_user_id", "notifications", ["user_id"], unique=False)
    op.create_index("ix_notifications_is_read", "notifications", ["is_read"], unique=False)
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"], unique=False)
    op.create_index(
        "ix_notifications_user_unread",
        "notifications",
        ["user_id", "is_read"],
        unique=False,
    )


def downgrade() -> None:
    """Remove notifications indexes, table, and notification_type_enum."""

    op.drop_index("ix_notifications_user_unread", table_name="notifications")
    op.drop_index("ix_notifications_created_at", table_name="notifications")
    op.drop_index("ix_notifications_is_read", table_name="notifications")
    op.drop_index("ix_notifications_user_id", table_name="notifications")

    op.drop_table("notifications")

    op.execute(sa.text("DROP TYPE notification_type_enum"))

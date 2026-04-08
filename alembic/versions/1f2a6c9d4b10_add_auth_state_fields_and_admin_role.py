"""add auth state fields and admin role

Revision ID: 1f2a6c9d4b10
Revises: c4e9bd11c2a7
Create Date: 2026-03-26 11:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "1f2a6c9d4b10"
down_revision = "c4e9bd11c2a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TYPE account_type ADD VALUE IF NOT EXISTS 'ADMIN'")

    op.add_column(
        "users",
        sa.Column("approved", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column("users", sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(
        "users",
        sa.Column("profile_completed", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.add_column(
        "users",
        sa.Column("profile_completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    op.execute(
        """
        UPDATE users
        SET approved = true,
            approved_at = COALESCE(approved_at, NOW())
        WHERE account_type = 'MANAGER'
        """
    )


def downgrade() -> None:
    op.drop_column("users", "profile_completed_at")
    op.drop_column("users", "profile_completed")
    op.drop_column("users", "approved_at")
    op.drop_column("users", "approved")

    # PostgreSQL enum values cannot be safely removed in place.

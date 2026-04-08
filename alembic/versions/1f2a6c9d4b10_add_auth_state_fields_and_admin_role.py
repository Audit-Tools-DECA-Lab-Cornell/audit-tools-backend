"""add auth state fields and admin role

Revision ID: 1f2a6c9d4b10
Revises: c4e9bd11c2a7
Create Date: 2026-03-26 11:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision = "1f2a6c9d4b10"
down_revision = "c4e9bd11c2a7"
branch_labels = None
depends_on = None


def _is_target_product(product_key: str) -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("product", "yee").strip().lower() == product_key


def upgrade() -> None:
    if not _is_target_product("yee"):
        return
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
    if not _is_target_product("yee"):
        return
    op.drop_column("users", "profile_completed_at")
    op.drop_column("users", "profile_completed")
    op.drop_column("users", "approved_at")
    op.drop_column("users", "approved")

    # PostgreSQL enum values cannot be safely removed in place.

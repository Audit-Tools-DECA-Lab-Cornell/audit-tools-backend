"""Restore YEE auth fields after the shared-core baseline.

Revision ID: 20260408_0004
Revises: 20260408_0003
Create Date: 2026-04-08 16:35:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "20260408_0004"
down_revision = "20260408_0003"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    if context.is_offline_mode():
        raise RuntimeError("Schema inspection is unavailable in offline migration mode.")
    return sa.inspect(op.get_bind())


def _is_target_product(product_key: str) -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("product", "yee").strip().lower() == product_key


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return column_name in {column["name"] for column in _inspector().get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(index["name"] == index_name for index in _inspector().get_indexes(table_name))


def upgrade() -> None:
    if not _is_target_product("yee"):
        return
    if context.is_offline_mode():
        return
    if _has_table("users"):
        if not _has_column("users", "account_id"):
            op.add_column(
                "users",
                sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
            )
            op.create_foreign_key(
                "fk_users_account_id_accounts",
                "users",
                "accounts",
                ["account_id"],
                ["id"],
                ondelete="SET NULL",
            )
            op.create_index("ix_users_account_id", "users", ["account_id"], unique=False)

        for column_name, column in [
            (
                "email_verified",
                sa.Column(
                    "email_verified",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
            ),
            (
                "email_verification_token_hash",
                sa.Column(
                    "email_verification_token_hash",
                    sa.String(length=255),
                    nullable=True,
                ),
            ),
            (
                "email_verification_sent_at",
                sa.Column(
                    "email_verification_sent_at",
                    sa.DateTime(timezone=True),
                    nullable=True,
                ),
            ),
            (
                "email_verified_at",
                sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
            ),
            (
                "failed_login_attempts",
                sa.Column(
                    "failed_login_attempts",
                    sa.Integer(),
                    nullable=False,
                    server_default=sa.text("0"),
                ),
            ),
            (
                "approved",
                sa.Column(
                    "approved",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
            ),
            (
                "approved_at",
                sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
            ),
            (
                "profile_completed",
                sa.Column(
                    "profile_completed",
                    sa.Boolean(),
                    nullable=False,
                    server_default=sa.text("false"),
                ),
            ),
            (
                "profile_completed_at",
                sa.Column("profile_completed_at", sa.DateTime(timezone=True), nullable=True),
            ),
            (
                "last_login_at",
                sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
            ),
        ]:
            if not _has_column("users", column_name):
                op.add_column("users", column)

        op.execute(
            """
            UPDATE users
            SET
                email_verified = COALESCE(email_verified, false),
                failed_login_attempts = COALESCE(failed_login_attempts, 0),
                approved = COALESCE(approved, false),
                profile_completed = COALESCE(profile_completed, false)
            """
        )

    if _has_table("auditor_profiles") and not _has_column("auditor_profiles", "user_id"):
        op.add_column(
            "auditor_profiles",
            sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        )
        op.create_foreign_key(
            "fk_auditor_profiles_user_id_users",
            "auditor_profiles",
            "users",
            ["user_id"],
            ["id"],
            ondelete="SET NULL",
        )
        if not _has_index("auditor_profiles", "ix_auditor_profiles_user_id"):
            op.create_index(
                "ix_auditor_profiles_user_id",
                "auditor_profiles",
                ["user_id"],
                unique=True,
            )


def downgrade() -> None:
    if not _is_target_product("yee"):
        return
    if context.is_offline_mode():
        return
    raise NotImplementedError("This corrective migration is intentionally one-way.")

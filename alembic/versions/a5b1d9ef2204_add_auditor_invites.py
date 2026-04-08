"""add auditor invites

Revision ID: a5b1d9ef2204
Revises: 82b7e4d33a21
Create Date: 2026-03-26 14:05:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "a5b1d9ef2204"
down_revision = "82b7e4d33a21"
branch_labels = None
depends_on = None


def _is_target_product(product_key: str) -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("product", "yee").strip().lower() == product_key


def upgrade() -> None:
    if not _is_target_product("yee"):
        return
    op.create_table(
        "auditor_invites",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("invited_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auditor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["accounts.id"], name=op.f("fk_auditor_invites_account_id_accounts"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["invited_by_user_id"], ["users.id"], name=op.f("fk_auditor_invites_invited_by_user_id_users"), ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["auditor_id"], ["auditors.id"], name=op.f("fk_auditor_invites_auditor_id_auditors"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auditor_invites")),
        sa.UniqueConstraint("token_hash", name=op.f("uq_auditor_invites_token_hash")),
    )
    op.create_index(op.f("ix_auditor_invites_account_id"), "auditor_invites", ["account_id"], unique=False)
    op.create_index(op.f("ix_auditor_invites_invited_by_user_id"), "auditor_invites", ["invited_by_user_id"], unique=False)
    op.create_index(op.f("ix_auditor_invites_auditor_id"), "auditor_invites", ["auditor_id"], unique=False)
    op.create_index(op.f("ix_auditor_invites_email"), "auditor_invites", ["email"], unique=False)


def downgrade() -> None:
    if not _is_target_product("yee"):
        return
    op.drop_index(op.f("ix_auditor_invites_email"), table_name="auditor_invites")
    op.drop_index(op.f("ix_auditor_invites_auditor_id"), table_name="auditor_invites")
    op.drop_index(op.f("ix_auditor_invites_invited_by_user_id"), table_name="auditor_invites")
    op.drop_index(op.f("ix_auditor_invites_account_id"), table_name="auditor_invites")
    op.drop_table("auditor_invites")

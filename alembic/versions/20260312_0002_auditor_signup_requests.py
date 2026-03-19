"""Add auditor signup request workflow tables.

Revision ID: 20260312_0002
Revises: 20260310_0001
Create Date: 2026-03-12 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260312_0002"
down_revision: str | None = "20260310_0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

AUDITOR_SIGNUP_REQUEST_STATUS_ENUM = postgresql.ENUM(
    "PENDING",
    "APPROVED",
    "DECLINED",
    name="shared_auditor_signup_request_status",
    create_type=False,
)


def upgrade() -> None:
    """Create the shared table used for auditor access-request approvals."""

    bind = op.get_bind()
    AUDITOR_SIGNUP_REQUEST_STATUS_ENUM.create(bind, checkfirst=True)

    op.create_table(
        "auditor_signup_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("manager_email", sa.String(length=320), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("status", AUDITOR_SIGNUP_REQUEST_STATUS_ENUM, nullable=False),
        sa.Column("approved_auditor_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assigned_project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("assigned_place_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "assigned_project_id IS NULL OR assigned_place_id IS NULL",
            name="ck_auditor_signup_requests_max_one_assignment",
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_auditor_signup_requests_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["approved_auditor_profile_id"],
            ["auditor_profiles.id"],
            name="fk_signup_req_approved_auditor",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_project_id"],
            ["projects.id"],
            name="fk_auditor_signup_requests_assigned_project_id_projects",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["assigned_place_id"],
            ["places.id"],
            name="fk_auditor_signup_requests_assigned_place_id_places",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auditor_signup_requests"),
    )
    op.create_index(
        "ix_auditor_signup_requests_account_id",
        "auditor_signup_requests",
        ["account_id"],
        unique=False,
    )
    op.create_index(
        "ix_auditor_signup_requests_manager_email",
        "auditor_signup_requests",
        ["manager_email"],
        unique=False,
    )
    op.create_index(
        "ix_auditor_signup_requests_email",
        "auditor_signup_requests",
        ["email"],
        unique=False,
    )
    op.create_index(
        "ix_auditor_signup_requests_status",
        "auditor_signup_requests",
        ["status"],
        unique=False,
    )
    op.create_index(
        "ix_auditor_signup_requests_approved_auditor_profile_id",
        "auditor_signup_requests",
        ["approved_auditor_profile_id"],
        unique=False,
    )
    op.create_index(
        "ix_auditor_signup_requests_assigned_project_id",
        "auditor_signup_requests",
        ["assigned_project_id"],
        unique=False,
    )
    op.create_index(
        "ix_auditor_signup_requests_assigned_place_id",
        "auditor_signup_requests",
        ["assigned_place_id"],
        unique=False,
    )


def downgrade() -> None:
    """Drop the shared auditor access-request workflow table."""

    op.drop_index(
        "ix_auditor_signup_requests_assigned_place_id",
        table_name="auditor_signup_requests",
    )
    op.drop_index(
        "ix_auditor_signup_requests_assigned_project_id",
        table_name="auditor_signup_requests",
    )
    op.drop_index(
        "ix_auditor_signup_requests_approved_auditor_profile_id",
        table_name="auditor_signup_requests",
    )
    op.drop_index("ix_auditor_signup_requests_manager_email", table_name="auditor_signup_requests")
    op.drop_index("ix_auditor_signup_requests_status", table_name="auditor_signup_requests")
    op.drop_index("ix_auditor_signup_requests_email", table_name="auditor_signup_requests")
    op.drop_index("ix_auditor_signup_requests_account_id", table_name="auditor_signup_requests")
    op.drop_table("auditor_signup_requests")

    bind = op.get_bind()
    AUDITOR_SIGNUP_REQUEST_STATUS_ENUM.drop(bind, checkfirst=True)

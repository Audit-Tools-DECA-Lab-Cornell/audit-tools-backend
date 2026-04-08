"""Create the shared core dashboard schema.

Revision ID: 20260310_0001
Revises:
Create Date: 2026-03-10 00:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "20260310_0001"
down_revision: str | None = None
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

ACCOUNT_TYPE_ENUM = postgresql.ENUM(
    "MANAGER",
    "AUDITOR",
    "ADMIN",
    name="shared_account_type",
    create_type=False,
)
AUDIT_STATUS_ENUM = postgresql.ENUM(
    "IN_PROGRESS",
    "PAUSED",
    "SUBMITTED",
    name="shared_audit_status",
    create_type=False,
)


def _table_exists(table_name: str) -> bool:
    """Check whether a table exists before attempting to drop it."""

    if context.is_offline_mode():
        return False
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return inspector.has_table(table_name)


def _is_target_product(product_key: str) -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("product", "yee").strip().lower() == product_key


def _drop_table_if_exists(table_name: str) -> None:
    """Drop a table with cascade when it exists in the current target database."""

    if _table_exists(table_name):
        op.execute(sa.text(f'DROP TABLE IF EXISTS "{table_name}" CASCADE'))


def upgrade() -> None:
    """Create the shared dashboard hierarchy used by both products."""

    if not _is_target_product("playspace"):
        return

    bind = op.get_bind()
    ACCOUNT_TYPE_ENUM.create(bind, checkfirst=True)
    AUDIT_STATUS_ENUM.create(bind, checkfirst=True)

    for table_name in [
        "audits",
        "auditor_assignments",
        "assignments",
        "auditor_profiles",
        "auditors",
        "places",
        "projects",
        "manager_profiles",
        "instruments",
        "accounts",
        "users",
    ]:
        _drop_table_if_exists(table_name)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("account_type", ACCOUNT_TYPE_ENUM, nullable=False),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=False)

    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("account_type", ACCOUNT_TYPE_ENUM, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name="pk_accounts"),
        sa.UniqueConstraint("email", name="uq_accounts_email"),
    )
    op.create_index("ix_accounts_email", "accounts", ["email"], unique=False)

    op.create_table(
        "manager_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("phone", sa.String(length=50), nullable=True),
        sa.Column("position", sa.String(length=200), nullable=True),
        sa.Column("organization", sa.String(length=200), nullable=True),
        sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_manager_profiles_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_manager_profiles"),
        sa.UniqueConstraint("email", name="uq_manager_profiles_email"),
    )
    op.create_index(
        "ix_manager_profiles_account_id", "manager_profiles", ["account_id"], unique=False
    )
    op.create_index("ix_manager_profiles_email", "manager_profiles", ["email"], unique=False)

    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("overview", sa.Text(), nullable=True),
        sa.Column(
            "place_types",
            postgresql.ARRAY(sa.String(length=100)),
            nullable=False,
            server_default=sa.text("'{}'::varchar[]"),
        ),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("est_places", sa.Integer(), nullable=True),
        sa.Column("est_auditors", sa.Integer(), nullable=True),
        sa.Column("auditor_description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_projects_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_projects"),
    )
    op.create_index("ix_projects_account_id", "projects", ["account_id"], unique=False)

    op.create_table(
        "places",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("city", sa.String(length=120), nullable=True),
        sa.Column("province", sa.String(length=120), nullable=True),
        sa.Column("country", sa.String(length=120), nullable=True),
        sa.Column("place_type", sa.String(length=120), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("start_date", sa.Date(), nullable=True),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("est_auditors", sa.Integer(), nullable=True),
        sa.Column("auditor_description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_places_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_places"),
    )
    op.create_index("ix_places_project_id", "places", ["project_id"], unique=False)

    op.create_table(
        "auditor_profiles",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auditor_code", sa.String(length=50), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("full_name", sa.String(length=200), nullable=False),
        sa.Column("age_range", sa.String(length=80), nullable=True),
        sa.Column("gender", sa.String(length=80), nullable=True),
        sa.Column("country", sa.String(length=120), nullable=True),
        sa.Column("role", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name="fk_auditor_profiles_account_id_accounts",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auditor_profiles"),
        sa.UniqueConstraint("account_id", name="uq_auditor_profiles_account_id"),
        sa.UniqueConstraint("auditor_code", name="uq_auditor_profiles_auditor_code"),
        sa.UniqueConstraint("email", name="uq_auditor_profiles_email"),
    )
    op.create_index(
        "ix_auditor_profiles_account_id", "auditor_profiles", ["account_id"], unique=False
    )
    op.create_index(
        "ix_auditor_profiles_auditor_code", "auditor_profiles", ["auditor_code"], unique=False
    )
    op.create_index("ix_auditor_profiles_email", "auditor_profiles", ["email"], unique=False)

    op.create_table(
        "auditor_assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auditor_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("place_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "assigned_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "(project_id IS NOT NULL) <> (place_id IS NOT NULL)",
            name="ck_auditor_assignments_single_scope",
        ),
        sa.ForeignKeyConstraint(
            ["auditor_profile_id"],
            ["auditor_profiles.id"],
            name="fk_auditor_assignments_auditor_profile_id_auditor_profiles",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name="fk_auditor_assignments_project_id_projects",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["places.id"],
            name="fk_auditor_assignments_place_id_places",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_auditor_assignments"),
        sa.UniqueConstraint(
            "auditor_profile_id",
            "project_id",
            name="uq_auditor_assignments_auditor_project",
        ),
        sa.UniqueConstraint(
            "auditor_profile_id",
            "place_id",
            name="uq_auditor_assignments_auditor_place",
        ),
    )
    op.create_index(
        "ix_auditor_assignments_auditor_profile_id",
        "auditor_assignments",
        ["auditor_profile_id"],
        unique=False,
    )
    op.create_index(
        "ix_auditor_assignments_project_id", "auditor_assignments", ["project_id"], unique=False
    )
    op.create_index(
        "ix_auditor_assignments_place_id", "auditor_assignments", ["place_id"], unique=False
    )

    op.create_table(
        "audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("place_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auditor_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("audit_code", sa.String(length=120), nullable=False),
        sa.Column("instrument_key", sa.String(length=80), nullable=True),
        sa.Column("instrument_version", sa.String(length=40), nullable=True),
        sa.Column("status", AUDIT_STATUS_ENUM, nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("total_minutes", sa.Integer(), nullable=True),
        sa.Column("summary_score", sa.Float(), nullable=True),
        sa.Column(
            "responses_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "scores_json",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["places.id"],
            name="fk_audits_place_id_places",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["auditor_profile_id"],
            ["auditor_profiles.id"],
            name="fk_audits_auditor_profile_id_auditor_profiles",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_audits"),
        sa.UniqueConstraint("audit_code", name="uq_audits_audit_code"),
    )
    op.create_index("ix_audits_place_id", "audits", ["place_id"], unique=False)
    op.create_index("ix_audits_auditor_profile_id", "audits", ["auditor_profile_id"], unique=False)
    op.create_index("ix_audits_audit_code", "audits", ["audit_code"], unique=False)


def downgrade() -> None:
    if not _is_target_product("playspace"):
        return
    """Drop the shared core dashboard schema."""

    bind = op.get_bind()
    for table_name in [
        "audits",
        "auditor_assignments",
        "auditor_profiles",
        "places",
        "projects",
        "manager_profiles",
        "accounts",
        "users",
    ]:
        _drop_table_if_exists(table_name)

    AUDIT_STATUS_ENUM.drop(bind, checkfirst=True)
    ACCOUNT_TYPE_ENUM.drop(bind, checkfirst=True)

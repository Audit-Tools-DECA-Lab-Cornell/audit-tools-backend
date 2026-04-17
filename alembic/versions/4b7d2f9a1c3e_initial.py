"""Initial schema

Revision ID: 4b7d2f9a1c3e
Revises:
Create Date: 2026-03-06 16:20:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "4b7d2f9a1c3e"
down_revision = None
branch_labels = None
depends_on = None


def _is_target_product(product_key: str) -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("product", "yee").strip().lower() == product_key


def upgrade() -> None:
    if not _is_target_product("yee"):
        return
    op.create_table(
        "accounts",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
    )

    op.create_table(
        "instruments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("version", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_instruments")),
        sa.UniqueConstraint("key", "version", name="uq_instrument_key_version"),
    )

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column(
            "account_type",
            sa.Enum("MANAGER", "AUDITOR", name="account_type"),
            nullable=False,
        ),
        sa.Column("name", sa.String(length=200), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("email", name=op.f("uq_users_email")),
    )
    op.create_index(op.f("ix_users_email"), "users", ["email"], unique=True)

    op.create_table(
        "projects",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("start_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("end_date", sa.DateTime(timezone=True), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_projects_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
    )
    op.create_index(op.f("ix_projects_account_id"), "projects", ["account_id"], unique=False)

    op.create_table(
        "auditors",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auditor_code", sa.String(length=50), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["account_id"],
            ["accounts.id"],
            name=op.f("fk_auditors_account_id_accounts"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_auditors_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_auditors")),
        sa.UniqueConstraint("user_id", name=op.f("uq_auditors_user_id")),
        sa.UniqueConstraint("auditor_code", name=op.f("uq_auditors_auditor_code")),
    )
    op.create_index(op.f("ix_auditors_account_id"), "auditors", ["account_id"], unique=False)
    op.create_index(op.f("ix_auditors_auditor_code"), "auditors", ["auditor_code"], unique=True)

    op.create_table(
        "places",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("address", sa.String(length=500), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["project_id"],
            ["projects.id"],
            name=op.f("fk_places_project_id_projects"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_places")),
    )
    op.create_index(op.f("ix_places_project_id"), "places", ["project_id"], unique=False)

    op.create_table(
        "assignments",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auditor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("place_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["auditor_id"],
            ["auditors.id"],
            name=op.f("fk_assignments_auditor_id_auditors"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["places.id"],
            name=op.f("fk_assignments_place_id_places"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_assignments")),
        sa.UniqueConstraint("auditor_id", "place_id", name="uq_assignment_auditor_place"),
    )
    op.create_index(op.f("ix_assignments_auditor_id"), "assignments", ["auditor_id"], unique=False)
    op.create_index(op.f("ix_assignments_place_id"), "assignments", ["place_id"], unique=False)

    op.create_table(
        "audits",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("instrument_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("place_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("auditor_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "status",
            sa.Enum("IN_PROGRESS", "SUBMITTED", name="audit_status"),
            nullable=False,
        ),
        sa.Column("responses_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("scores_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.ForeignKeyConstraint(
            ["auditor_id"],
            ["auditors.id"],
            name=op.f("fk_audits_auditor_id_auditors"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["instrument_id"],
            ["instruments.id"],
            name=op.f("fk_audits_instrument_id_instruments"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["places.id"],
            name=op.f("fk_audits_place_id_places"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audits")),
    )
    op.create_index(op.f("ix_audits_auditor_id"), "audits", ["auditor_id"], unique=False)
    op.create_index(op.f("ix_audits_instrument_id"), "audits", ["instrument_id"], unique=False)
    op.create_index(op.f("ix_audits_place_id"), "audits", ["place_id"], unique=False)


def downgrade() -> None:
    if not _is_target_product("yee"):
        return
    op.drop_index(op.f("ix_audits_place_id"), table_name="audits")
    op.drop_index(op.f("ix_audits_instrument_id"), table_name="audits")
    op.drop_index(op.f("ix_audits_auditor_id"), table_name="audits")
    op.drop_table("audits")

    op.drop_index(op.f("ix_assignments_place_id"), table_name="assignments")
    op.drop_index(op.f("ix_assignments_auditor_id"), table_name="assignments")
    op.drop_table("assignments")

    op.drop_index(op.f("ix_places_project_id"), table_name="places")
    op.drop_table("places")

    op.drop_index(op.f("ix_auditors_auditor_code"), table_name="auditors")
    op.drop_index(op.f("ix_auditors_account_id"), table_name="auditors")
    op.drop_table("auditors")

    op.drop_index(op.f("ix_projects_account_id"), table_name="projects")
    op.drop_table("projects")

    op.drop_index(op.f("ix_users_email"), table_name="users")
    op.drop_table("users")

    op.drop_table("instruments")
    op.drop_table("accounts")

    op.execute("DROP TYPE IF EXISTS audit_status")
    op.execute("DROP TYPE IF EXISTS account_type")

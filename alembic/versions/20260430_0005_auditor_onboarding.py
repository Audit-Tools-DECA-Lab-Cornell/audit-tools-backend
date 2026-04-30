"""Add auditor onboarding fields and access-request table.

Adds phone, city, province, terms_accepted_at columns to auditor_profiles
and creates the auditor_access_requests table for Scenario A self-signup.

Revision ID: 20260430_0005
Revises: 20260428_0004
Create Date: 2026-04-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260430_0005"
down_revision = "20260428_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
	op.add_column("auditor_profiles", sa.Column("phone", sa.String(50), nullable=True))
	op.add_column("auditor_profiles", sa.Column("city", sa.String(120), nullable=True))
	op.add_column("auditor_profiles", sa.Column("province", sa.String(120), nullable=True))
	op.add_column(
		"auditor_profiles",
		sa.Column("terms_accepted_at", sa.DateTime(timezone=True), nullable=True),
	)

	op.create_table(
		"auditor_access_requests",
		sa.Column("id", sa.UUID(as_uuid=True), primary_key=True),
		sa.Column("name", sa.String(200), nullable=False),
		sa.Column("email", sa.String(320), nullable=False),
		sa.Column("manager_email", sa.String(320), nullable=False),
		sa.Column(
			"status",
			sa.String(20),
			nullable=False,
			server_default="pending",
		),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			nullable=False,
			server_default=sa.func.now(),
		),
	)
	op.create_index("ix_auditor_access_requests_email", "auditor_access_requests", ["email"])


def downgrade() -> None:
	op.drop_index("ix_auditor_access_requests_email", table_name="auditor_access_requests")
	op.drop_table("auditor_access_requests")
	op.drop_column("auditor_profiles", "terms_accepted_at")
	op.drop_column("auditor_profiles", "province")
	op.drop_column("auditor_profiles", "city")
	op.drop_column("auditor_profiles", "phone")

"""Add project creator ownership for manager scoping.

Revision ID: 20260420_0013
Revises: 20260418_0012
Create Date: 2026-04-20 13:30:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260420_0013"
down_revision = "20260418_0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
	op.add_column(
		"projects",
		sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
	)
	op.create_index(
		op.f("ix_projects_created_by_user_id"),
		"projects",
		["created_by_user_id"],
		unique=False,
	)
	op.create_foreign_key(
		op.f("fk_projects_created_by_user_id_users"),
		"projects",
		"users",
		["created_by_user_id"],
		["id"],
		ondelete="RESTRICT",
	)

	op.execute(
		"""
        UPDATE projects
        SET created_by_user_id = (
            SELECT users.id
            FROM users
            WHERE users.account_id = projects.account_id
              AND users.account_type = 'MANAGER'
            ORDER BY users.created_at ASC, users.id ASC
            LIMIT 1
        )
        WHERE projects.created_by_user_id IS NULL
        """
	)

	op.alter_column("projects", "created_by_user_id", nullable=False)


def downgrade() -> None:
	op.drop_constraint(op.f("fk_projects_created_by_user_id_users"), "projects", type_="foreignkey")
	op.drop_index(op.f("ix_projects_created_by_user_id"), table_name="projects")
	op.drop_column("projects", "created_by_user_id")

"""Add playspace audit role to auditor assignments.

Revision ID: 20260312_0002
Revises: 20260310_0001
Create Date: 2026-03-19 00:02:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "20260312_0002"
down_revision: str | None = "20260310_0001"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

AUDIT_PARTICIPATION_ROLE_ENUM = postgresql.ENUM(
	"AUDITOR",
	"PLACE_ADMIN",
	"BOTH",
	name="playspace_audit_participation_role",
	create_type=False,
)


def _is_target_product(product_key: str) -> bool:
	x_args = context.get_x_argument(as_dictionary=True)
	return x_args.get("product", "yee").strip().lower() == product_key


def upgrade() -> None:
	"""Add the per-assignment Playspace form role column."""

	if not _is_target_product("playspace"):
		return
	bind = op.get_bind()
	AUDIT_PARTICIPATION_ROLE_ENUM.create(bind, checkfirst=True)
	op.add_column(
		"auditor_assignments",
		sa.Column(
			"audit_role",
			AUDIT_PARTICIPATION_ROLE_ENUM,
			nullable=False,
			server_default=sa.text("'AUDITOR'"),
		),
	)
	op.alter_column("auditor_assignments", "audit_role", server_default=None)


def downgrade() -> None:
	"""Remove the per-assignment Playspace form role column."""

	if not _is_target_product("playspace"):
		return
	bind = op.get_bind()
	op.drop_column("auditor_assignments", "audit_role")
	AUDIT_PARTICIPATION_ROLE_ENUM.drop(bind, checkfirst=True)

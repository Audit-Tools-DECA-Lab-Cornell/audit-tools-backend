"""Add instruments table when missing.

This repairs YEE environments that were upgraded through the live-schema
alignment path but never received the shared ``instruments`` table.

Revision ID: 20260525_0005
Revises: 20260428_0004
Create Date: 2026-05-25
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260525_0005"
down_revision = "20260428_0004"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
	conn = op.get_bind()
	insp = sa.inspect(conn)
	return name in insp.get_table_names()


def upgrade() -> None:
	if _table_exists("instruments"):
		return

	op.create_table(
		"instruments",
		sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
		sa.Column("instrument_key", sa.String(length=255), nullable=False),
		sa.Column("instrument_version", sa.String(length=50), nullable=False),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
		sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_instruments")),
	)


def downgrade() -> None:
	if _table_exists("instruments"):
		op.drop_table("instruments")

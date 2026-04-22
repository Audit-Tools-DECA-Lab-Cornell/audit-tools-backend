"""Add instruments table for centralized instrument management.

Revision ID: 20260414_0012
Revises: 20260408_0011
Create Date: 2026-04-14 23:00:00
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260414_0012"
down_revision: str = "20260408_0011"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
	op.create_table(
		"instruments",
		sa.Column(
			"id",
			postgresql.UUID(as_uuid=True),
			server_default=sa.text("gen_random_uuid()"),
			nullable=False,
		),
		sa.Column("instrument_key", sa.String(255), nullable=False),
		sa.Column("instrument_version", sa.String(50), nullable=False),
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
		sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
		sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_instruments")),
	)
	op.create_index(
		op.f("ix_instruments_instrument_key"),
		"instruments",
		["instrument_key"],
		unique=False,
	)


def downgrade() -> None:
	op.drop_index(op.f("ix_instruments_instrument_key"), table_name="instruments")
	op.drop_table("instruments")

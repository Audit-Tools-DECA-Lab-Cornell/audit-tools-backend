"""Add saved_place_reports JSONB column to places table.

Stores an array of saved place report combinations (audit+survey pairs or
single full-assessment IDs) as lightweight JSON objects.

Revision ID: 20260507_0007
Revises: 20260502_0006
Create Date: 2026-05-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "20260507_0007"
down_revision = "20260502_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
	op.add_column(
		"places",
		sa.Column("saved_place_reports", JSONB, nullable=False, server_default="[]"),
	)


def downgrade() -> None:
	op.drop_column("places", "saved_place_reports")

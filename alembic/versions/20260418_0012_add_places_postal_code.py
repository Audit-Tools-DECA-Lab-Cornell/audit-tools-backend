"""Add postal code field to places.

Revision ID: 20260418_0012
Revises: 20260408_0011
Create Date: 2026-04-18 00:00:00.000000
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


revision = "20260418_0012"
down_revision = "20260408_0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
	op.add_column("places", sa.Column("postal_code", sa.String(length=32), nullable=True))


def downgrade() -> None:
	op.drop_column("places", "postal_code")

"""Add address column to places table

Revision ID: 20260423_0002
Revises: 20260423_0001
Create Date: 2026-04-23

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260423_0002"
down_revision = "20260423_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
	op.add_column("places", sa.Column("address", sa.Text(), nullable=True))


def downgrade() -> None:
	op.drop_column("places", "address")

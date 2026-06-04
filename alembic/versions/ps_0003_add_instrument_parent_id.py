"""Add parent links for instrument draft branches.

Revision ID: ps_0003
Revises: ps_0002
Create Date: 2026-06-03

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "ps_0003"
down_revision = "ps_0002"
branch_labels = None
depends_on = None

TABLE_NAME = "instruments"
COLUMN_NAME = "parent_instrument_id"
FK_NAME = "fk_instruments_parent_instrument_id_instruments"
IX_NAME = "ix_instruments_parent_instrument_id"


def _has_column(column_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	return any(column["name"] == column_name for column in inspector.get_columns(TABLE_NAME))


def upgrade() -> None:
	if not _has_column(COLUMN_NAME):
		op.add_column(TABLE_NAME, sa.Column(COLUMN_NAME, sa.UUID(), nullable=True))
		op.create_foreign_key(FK_NAME, TABLE_NAME, TABLE_NAME, [COLUMN_NAME], ["id"], ondelete="SET NULL")
		op.create_index(IX_NAME, TABLE_NAME, [COLUMN_NAME], unique=False)


def downgrade() -> None:
	if _has_column(COLUMN_NAME):
		op.drop_index(IX_NAME, table_name=TABLE_NAME)
		op.drop_constraint(FK_NAME, TABLE_NAME, type_="foreignkey")
		op.drop_column(TABLE_NAME, COLUMN_NAME)

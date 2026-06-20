"""Add shared manager-profile fields used by the updated org rules.

Revision ID: ps_0006
Revises: ps_0005
Create Date: 2026-06-19
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "ps_0006"
down_revision = "ps_0005"
branch_labels = None
depends_on = None

_TABLE = "manager_profiles"
_COLUMN = "profession_disciplines"


def _has_table(table_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	if table_name not in inspector.get_table_names():
		return False
	return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
	if not _has_table(_TABLE) or _has_column(_TABLE, _COLUMN):
		return
	op.add_column(
		_TABLE,
		sa.Column(
			_COLUMN,
			postgresql.ARRAY(sa.String(length=120)),
			nullable=False,
			server_default=sa.text("'{}'"),
		),
	)
	op.execute("UPDATE manager_profiles SET profession_disciplines = '{}' WHERE profession_disciplines IS NULL")
	op.alter_column(_TABLE, _COLUMN, server_default=None)


def downgrade() -> None:
	if not _has_table(_TABLE) or not _has_column(_TABLE, _COLUMN):
		return
	op.drop_column(_TABLE, _COLUMN)

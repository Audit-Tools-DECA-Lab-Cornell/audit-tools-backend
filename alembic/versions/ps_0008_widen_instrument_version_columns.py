"""Widen instrument_version stamp columns to String(50) (Playspace database).

Mirror of ``yee_0007`` for the Playspace side. ``instruments.instrument_version``
is ``String(50)`` but the stamp columns were ``String(40)``, so a 41-50 char
version label truncates when copied onto an audit/submission. Widen the shared
``audits`` table (this runs against the Playspace DB) and the Playspace-only
``playspace_submissions`` table. Metadata-only change in Postgres.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "ps_0008"
down_revision = "ps_0007"
branch_labels = None
depends_on = None

_COLUMN = "instrument_version"
_TABLES = ("audits", "playspace_submissions")


def _has_column(table_name: str, column_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	if table_name not in inspector.get_table_names():
		return False
	return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
	for table_name in _TABLES:
		if _has_column(table_name, _COLUMN):
			op.alter_column(
				table_name,
				_COLUMN,
				existing_type=sa.String(length=40),
				type_=sa.String(length=50),
				existing_nullable=True,
			)


def downgrade() -> None:
	for table_name in reversed(_TABLES):
		if _has_column(table_name, _COLUMN):
			op.alter_column(
				table_name,
				_COLUMN,
				existing_type=sa.String(length=50),
				type_=sa.String(length=40),
				existing_nullable=True,
			)

"""Widen instrument_version stamp columns to String(50) (YEE database).

``instruments.instrument_version`` is ``String(50)``, but the audit-side stamp
columns were ``String(40)``. A version label of 41-50 characters fits the
instrument row yet truncates when copied onto an audit/submission, raising a
Postgres ``varchar(40)`` error at insert time. Widen the stamp columns so any
version an instrument can hold can also be stamped.

``audits`` is a shared table, so the same widening runs on the Playspace branch
(``ps_0008``); ``yee_audit_submissions`` is YEE-only and is widened here.
Widening a varchar is a metadata-only change in Postgres (no table rewrite).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "yee_0007"
down_revision = "yee_0006"
branch_labels = None
depends_on = None

_COLUMN = "instrument_version"
_TABLES = ("audits", "yee_audit_submissions")


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

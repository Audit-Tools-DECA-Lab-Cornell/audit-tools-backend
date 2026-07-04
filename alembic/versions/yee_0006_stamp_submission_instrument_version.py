from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "yee_0006"
down_revision = "yee_0005"
branch_labels = None
depends_on = None

_TABLE = "yee_audit_submissions"
_KEY_COLUMN = "instrument_key"
_VERSION_COLUMN = "instrument_version"
_INSTRUMENTS_TABLE = "instruments"


def _has_table(table_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	if table_name not in inspector.get_table_names():
		return False
	return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def upgrade() -> None:
	if not _has_table(_TABLE):
		return

	if not _has_column(_TABLE, _KEY_COLUMN):
		op.add_column(_TABLE, sa.Column(_KEY_COLUMN, sa.String(length=80), nullable=True))

	if not _has_column(_TABLE, _VERSION_COLUMN):
		op.add_column(_TABLE, sa.Column(_VERSION_COLUMN, sa.String(length=40), nullable=True))

	# Backfill existing submissions with the currently-active YEE instrument
	# version so the usage delete-guard protects them and historical reports can
	# resolve their version. COALESCE falls back to "1" (the bootstrap version)
	# when no active instrument row exists yet. Idempotent: only touches rows not
	# already stamped.
	if _has_table(_INSTRUMENTS_TABLE):
		op.execute(
			"UPDATE yee_audit_submissions "
			"SET instrument_key = 'yee', "
			"instrument_version = COALESCE("
			"(SELECT instrument_version FROM instruments "
			"WHERE instrument_key = 'yee' AND is_active = true "
			"ORDER BY created_at DESC LIMIT 1), '1') "
			"WHERE instrument_version IS NULL"
		)


def downgrade() -> None:
	if not _has_table(_TABLE):
		return

	if _has_column(_TABLE, _VERSION_COLUMN):
		op.drop_column(_TABLE, _VERSION_COLUMN)

	if _has_column(_TABLE, _KEY_COLUMN):
		op.drop_column(_TABLE, _KEY_COLUMN)

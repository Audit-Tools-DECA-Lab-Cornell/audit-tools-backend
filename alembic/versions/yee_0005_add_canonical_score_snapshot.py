from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "yee_0005"
down_revision = "yee_0004"
branch_labels = None
depends_on = None

_TABLE = "yee_audit_submissions"
_SCORES_COLUMN = "scores_json"
_VERSION_COLUMN = "scoring_version"


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

	if not _has_column(_TABLE, _SCORES_COLUMN):
		op.add_column(
			_TABLE,
			sa.Column(
				_SCORES_COLUMN,
				postgresql.JSONB(astext_type=sa.Text()),
				server_default=sa.text("'{}'::jsonb"),
				nullable=False,
			),
		)

	if not _has_column(_TABLE, _VERSION_COLUMN):
		op.add_column(
			_TABLE,
			sa.Column(
				_VERSION_COLUMN,
				sa.String(length=32),
				server_default=sa.text("'yee_v2'"),
				nullable=False,
			),
		)


def downgrade() -> None:
	if not _has_table(_TABLE):
		return

	if _has_column(_TABLE, _VERSION_COLUMN):
		op.drop_column(_TABLE, _VERSION_COLUMN)

	if _has_column(_TABLE, _SCORES_COLUMN):
		op.drop_column(_TABLE, _SCORES_COLUMN)

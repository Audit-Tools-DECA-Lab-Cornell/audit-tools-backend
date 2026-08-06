"""Add structured multi-select values to Playspace scale answers.

Scalar answers continue to use ``option_key``. Multi-select answers use the
nullable ``selected_option_keys`` JSONB array on the same logical
``(question_response_id, scale_key)`` row, so no relationship or uniqueness
change is required.

The downgrade is safe only before multi-select writes exist. It refuses to
restore ``option_key`` to NOT NULL when any row has a NULL scalar value;
production rollback after new writes requires an explicit data-preserving
conversion or backup instead of this downgrade.

Revision ID: ps_0010
Revises: ps_0009
Create Date: 2026-08-06
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "ps_0010"
down_revision = "ps_0009"
branch_labels = None
depends_on = None

_VALUE_CONSTRAINT_NAME = "ck_playspace_scale_answers_exactly_one_value"


def _has_table(table_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	return table_name in inspector.get_table_names()


def _column_metadata(table_name: str, column_name: str) -> dict[str, object] | None:
	inspector = sa.inspect(op.get_bind())
	return next((column for column in inspector.get_columns(table_name) if column["name"] == column_name), None)


def _has_check_constraint(table_name: str, constraint_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	return any(constraint["name"] == constraint_name for constraint in inspector.get_check_constraints(table_name))


def upgrade() -> None:
	if not _has_table("playspace_scale_answers"):
		return

	if _column_metadata("playspace_scale_answers", "selected_option_keys") is None:
		op.add_column(
			"playspace_scale_answers",
			sa.Column(
				"selected_option_keys",
				postgresql.JSONB(astext_type=sa.Text()),
				nullable=True,
			),
		)

	option_key = _column_metadata("playspace_scale_answers", "option_key")
	if option_key is not None and option_key.get("nullable") is False:
		op.alter_column(
			"playspace_scale_answers",
			"option_key",
			existing_type=sa.String(length=255),
			nullable=True,
		)

	if not _has_check_constraint("playspace_scale_answers", _VALUE_CONSTRAINT_NAME):
		op.create_check_constraint(
			_VALUE_CONSTRAINT_NAME,
			"playspace_scale_answers",
			"(option_key IS NOT NULL) <> (selected_option_keys IS NOT NULL)",
		)


def downgrade() -> None:
	if not _has_table("playspace_scale_answers"):
		return

	option_key = _column_metadata("playspace_scale_answers", "option_key")
	if option_key is not None and option_key.get("nullable") is True:
		row_without_scalar = (
			op.get_bind()
			.execute(sa.text("SELECT 1 FROM playspace_scale_answers WHERE option_key IS NULL LIMIT 1"))
			.first()
		)
		if row_without_scalar is not None:
			raise RuntimeError(
				"Refusing to restore option_key NOT NULL while any scale answer option_key is NULL; "
				"perform a data-preserving conversion or restore from backup."
			)

	if _has_check_constraint("playspace_scale_answers", _VALUE_CONSTRAINT_NAME):
		op.drop_constraint(_VALUE_CONSTRAINT_NAME, "playspace_scale_answers", type_="check")

	if option_key is not None and option_key.get("nullable") is True:
		op.alter_column(
			"playspace_scale_answers",
			"option_key",
			existing_type=sa.String(length=255),
			nullable=False,
		)

	selected_option_keys = _column_metadata("playspace_scale_answers", "selected_option_keys")
	if selected_option_keys is not None:
		op.drop_column("playspace_scale_answers", "selected_option_keys")

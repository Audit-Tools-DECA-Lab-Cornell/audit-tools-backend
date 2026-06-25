"""Harden the YEE final-submit boundary for offline-safe submission.

YEE drafts stay on the device; the backend only has to be durable at final
submit. Two additive guards make that boundary safe under retries and races:

* ``submit_idempotency_key`` - a nullable key stored on a successful submit so a
  replayed submit (after an ambiguous network failure) returns the existing
  submission instead of a 409 conflict.
* ``uq_yee_audit_submissions_auditor_place`` - a unique constraint enforcing one
  submission per ``(auditor_id, place_id)`` at the database level, behind the
  route's duplicate check.

Both changes touch only ``yee_audit_submissions``, which exists in the YEE
database. The migration is safe on fresh, partially-migrated, and rerun states.

Revision ID: yee_0004
Revises: yee_0003
Create Date: 2026-06-24

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "yee_0004"
down_revision = "yee_0003"
branch_labels = None
depends_on = None

_TABLE = "yee_audit_submissions"
_COLUMN = "submit_idempotency_key"
_UNIQUE_CONSTRAINT = "uq_yee_audit_submissions_auditor_place"


def _has_table(table_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	if table_name not in inspector.get_table_names():
		return False
	return column_name in {column["name"] for column in inspector.get_columns(table_name)}


def _has_unique_constraint(table_name: str, constraint_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	if table_name not in inspector.get_table_names():
		return False
	return constraint_name in {constraint["name"] for constraint in inspector.get_unique_constraints(table_name)}


def upgrade() -> None:
	if not _has_table(_TABLE):
		return

	if not _has_column(_TABLE, _COLUMN):
		op.add_column(_TABLE, sa.Column(_COLUMN, sa.String(length=64), nullable=True))

	if not _has_unique_constraint(_TABLE, _UNIQUE_CONSTRAINT):
		op.create_unique_constraint(_UNIQUE_CONSTRAINT, _TABLE, ["auditor_id", "place_id"])


def downgrade() -> None:
	if not _has_table(_TABLE):
		return

	if _has_unique_constraint(_TABLE, _UNIQUE_CONSTRAINT):
		op.drop_constraint(_UNIQUE_CONSTRAINT, _TABLE, type_="unique")

	if _has_column(_TABLE, _COLUMN):
		op.drop_column(_TABLE, _COLUMN)

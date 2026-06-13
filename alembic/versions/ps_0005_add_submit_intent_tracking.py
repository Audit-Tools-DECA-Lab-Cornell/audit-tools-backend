"""Add submit-intent and idempotency tracking columns to Playspace submissions.

The offline durability program needs the server to know when an auditor
*intended* to submit, independently of whether the submit request itself
arrived. Four nullable columns are added to ``playspace_submissions``:

* ``submit_intended_at`` - server time the first submit-intent beacon was
  recorded for an in-progress audit. Drives the never-arrived detector job.
* ``submit_intent_client_at`` - device-reported time the auditor tapped submit,
  kept for diagnostics (clock skew, offline duration).
* ``submit_stall_notified_at`` - last time the auditor was emailed that their
  intended submission had not completed, so the detector does not re-notify on
  every run.
* ``submit_idempotency_key`` - key stored on a successful submit so a replayed
  submit (after an ambiguous network failure) returns the submitted session
  instead of a 409 conflict.

All columns are nullable and additive, so the migration is safe on fresh,
partially-migrated, and rerun states.

Revision ID: ps_0005
Revises: ps_0004
Create Date: 2026-06-12

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "ps_0005"
down_revision = "ps_0004"
branch_labels = None
depends_on = None

_TABLE = "playspace_submissions"


def _new_columns() -> list[sa.Column]:
	"""Build fresh Column objects each call (a Column can be added only once)."""

	return [
		sa.Column("submit_intended_at", sa.DateTime(timezone=True), nullable=True),
		sa.Column("submit_intent_client_at", sa.DateTime(timezone=True), nullable=True),
		sa.Column("submit_stall_notified_at", sa.DateTime(timezone=True), nullable=True),
		sa.Column("submit_idempotency_key", sa.String(length=64), nullable=True),
	]


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
	for column in _new_columns():
		if not _has_column(_TABLE, column.name):
			op.add_column(_TABLE, column)


def downgrade() -> None:
	if not _has_table(_TABLE):
		return
	for column in reversed(_new_columns()):
		if _has_column(_TABLE, column.name):
			op.drop_column(_TABLE, column.name)

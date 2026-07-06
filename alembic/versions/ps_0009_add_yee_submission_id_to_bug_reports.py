"""Add yee_submission_id column to the Playspace bug_reports table.

``bug_reports`` became a shared-core table and the shared ``BugReport`` model
gained a ``yee_submission_id`` column. Every ORM insert/select now emits that
column against BOTH product databases, so the existing Playspace table (created
by ``ps_0006`` without it) must gain the column too or writes fail.

The column is added WITHOUT a foreign key: its referenced table
``yee_audit_submissions`` is YEE-only and does not exist in the Playspace
database. Playspace reports never populate it; it stays NULL. (The FK is created
only in the YEE branch, in ``yee_0008``.)

Created on the ``playspace`` branch:

    alembic -x product=playspace upgrade playspace@head

Hand-written and idempotent (guards on column existence).

Revision ID: ps_0009
Revises: ps_0008
Create Date: 2026-07-06

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "ps_0009"
down_revision = "ps_0008"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	return table_name in inspector.get_table_names()


def _has_column(table_name: str, column_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
	if _has_table("bug_reports") and not _has_column("bug_reports", "yee_submission_id"):
		op.add_column("bug_reports", sa.Column("yee_submission_id", sa.UUID(), nullable=True))


def downgrade() -> None:
	if _has_table("bug_reports") and _has_column("bug_reports", "yee_submission_id"):
		op.drop_column("bug_reports", "yee_submission_id")

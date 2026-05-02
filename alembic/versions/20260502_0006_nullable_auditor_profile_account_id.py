"""Make auditor_profiles.account_id nullable to support soft unlink.

When a manager removes an auditor from their account the profile row is kept
(preserving all historical submissions/audits) by setting account_id to NULL
rather than deleting the row.  The FK and its CASCADE are retained so that
deleting an Account still cascades correctly.

Revision ID: 20260502_0006
Revises: 20260430_0005
Create Date: 2026-05-02
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "20260502_0006"
down_revision = "20260430_0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
	# Drop the existing non-nullable FK constraint, alter the column, then
	# re-add as nullable.  PostgreSQL requires the column alteration and
	# constraint recreation to happen in sequence.

	with op.batch_alter_table("auditor_profiles", schema=None) as batch_op:
		batch_op.alter_column(
			"account_id",
			existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
			nullable=True,
		)


def downgrade() -> None:
	# Before reverting, rows with account_id = NULL must be deleted or
	# re-assigned; we do a best-effort deletion here to avoid constraint
	# violations on downgrade.
	op.execute(
		"""
		DELETE FROM auditor_profiles
		WHERE account_id IS NULL
		"""
	)

	with op.batch_alter_table("auditor_profiles", schema=None) as batch_op:
		batch_op.alter_column(
			"account_id",
			existing_type=sa.dialects.postgresql.UUID(as_uuid=True),
			nullable=False,
		)

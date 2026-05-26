"""Add final comments to Playspace submission contexts.

Revision ID: 0002
Revises: 0001
Create Date: 2026-05-26

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
	op.add_column(
		"playspace_submission_contexts",
		sa.Column("final_comments", sa.Text(), nullable=True),
	)


def downgrade() -> None:
	op.drop_column("playspace_submission_contexts", "final_comments")

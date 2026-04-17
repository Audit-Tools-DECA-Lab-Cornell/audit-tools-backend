"""Merge the shared-core and YEE compatibility migration heads.

Revision ID: 20260408_0003
Revises: 20260310_0001, 20260408_0002
Create Date: 2026-04-08 16:10:00
"""

from __future__ import annotations

# revision identifiers, used by Alembic.
revision = "20260408_0003"
down_revision = ("20260310_0001", "20260408_0002")
branch_labels = None
depends_on = None


def upgrade() -> None:
	"""Merge migration branches without applying additional schema changes."""


def downgrade() -> None:
	"""Split the merged migration history without reverting schema changes."""

"""Merge notifications and YEE dashboard migration heads.

Revision ID: 20260422_0015
Revises: 20260416_0007, 20260420_0014
Create Date: 2026-04-22 14:50:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "20260422_0015"
down_revision: Sequence[str] | None = ("20260416_0007", "20260420_0014")
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
	"""Merge the divergent Alembic heads without applying schema changes."""


def downgrade() -> None:
	"""Split the merged Alembic heads."""

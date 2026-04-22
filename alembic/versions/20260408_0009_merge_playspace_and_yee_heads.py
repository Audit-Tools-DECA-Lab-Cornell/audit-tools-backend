"""Merge latest Playspace and YEE migration heads.

Revision ID: 20260408_0009
Revises: 20260323_0005, 20260408_0008
Create Date: 2026-04-08 00:09:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "20260408_0009"
down_revision: tuple[str, str] = ("20260323_0005", "20260408_0008")
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
	pass


def downgrade() -> None:
	pass

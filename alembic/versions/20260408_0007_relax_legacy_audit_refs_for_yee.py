"""Relax legacy audit reference requirements for YEE compatibility.

Revision ID: 20260408_0007
Revises: 20260408_0006
Create Date: 2026-04-08 00:07:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "20260408_0007"
down_revision: str | None = "20260408_0006"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _is_target_product(product_key: str) -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("product", "yee").strip().lower() == product_key


def _has_column(table_name: str, column_name: str) -> bool:
    if context.is_offline_mode():
        return False
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def upgrade() -> None:
    if not _is_target_product("yee"):
        return
    if _has_column("audits", "instrument_id"):
        op.execute("ALTER TABLE audits ALTER COLUMN instrument_id DROP NOT NULL")
    if _has_column("audits", "auditor_id"):
        op.execute("ALTER TABLE audits ALTER COLUMN auditor_id DROP NOT NULL")


def downgrade() -> None:
    if not _is_target_product("yee"):
        return
    if _has_column("audits", "instrument_id"):
        op.execute("ALTER TABLE audits ALTER COLUMN instrument_id SET NOT NULL")
    if _has_column("audits", "auditor_id"):
        op.execute("ALTER TABLE audits ALTER COLUMN auditor_id SET NOT NULL")

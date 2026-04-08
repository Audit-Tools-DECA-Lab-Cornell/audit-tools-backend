"""Allow multiple auditor profiles per account.

Revision ID: 20260408_0005
Revises: 20260408_0004
Create Date: 2026-04-08 16:50:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision = "20260408_0005"
down_revision = "20260408_0004"
branch_labels = None
depends_on = None


def _inspector() -> sa.Inspector:
    if context.is_offline_mode():
        raise RuntimeError("Schema inspection is unavailable in offline migration mode.")
    return sa.inspect(op.get_bind())


def _is_target_product(product_key: str) -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("product", "yee").strip().lower() == product_key


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def _has_constraint(table_name: str, constraint_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(
        constraint["name"] == constraint_name
        for constraint in _inspector().get_unique_constraints(table_name)
    )


def upgrade() -> None:
    if not _is_target_product("yee"):
        return
    if context.is_offline_mode():
        return
    if _has_constraint("auditor_profiles", "uq_auditor_profiles_account_id"):
        op.drop_constraint("uq_auditor_profiles_account_id", "auditor_profiles", type_="unique")


def downgrade() -> None:
    if not _is_target_product("yee"):
        return
    if context.is_offline_mode():
        return
    op.create_unique_constraint(
        "uq_auditor_profiles_account_id",
        "auditor_profiles",
        ["account_id"],
    )

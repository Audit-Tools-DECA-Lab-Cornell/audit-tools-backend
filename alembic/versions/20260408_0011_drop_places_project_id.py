"""Drop the legacy project_id column from places.

The 20260323_0005 migration moved this relationship into project_places
but is skipped on databases that already had that table.  This corrective
step ensures the column is dropped so seed data and the current ORM model
work correctly.

Revision ID: 20260408_0011
Revises: 20260408_0010
Create Date: 2026-04-08 22:00:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op

# revision identifiers, used by Alembic.
revision = "20260408_0011"
down_revision = "20260408_0010"
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


def _has_column(table_name: str, column_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return column_name in {column["name"] for column in _inspector().get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(index["name"] == index_name for index in _inspector().get_indexes(table_name))


def _has_foreign_key(table_name: str, fk_name: str) -> bool:
    if not _has_table(table_name):
        return False
    return any(fk["name"] == fk_name for fk in _inspector().get_foreign_keys(table_name))


def upgrade() -> None:
    if not _is_target_product("playspace"):
        return
    if context.is_offline_mode():
        return

    if not _has_table("places"):
        return

    if _has_column("places", "project_id"):
        if _has_foreign_key("places", "fk_places_project_id_projects"):
            op.drop_constraint(
                "fk_places_project_id_projects",
                "places",
                type_="foreignkey",
            )
        if _has_index("places", "ix_places_project_id"):
            op.drop_index("ix_places_project_id", table_name="places")
        op.drop_column("places", "project_id")

    # The 20260323_0005 migration also drops this column but was skipped.
    if _has_column("auditor_assignments", "audit_roles"):
        op.drop_column("auditor_assignments", "audit_roles")


def downgrade() -> None:
    if not _is_target_product("playspace"):
        return
    if context.is_offline_mode():
        return
    raise NotImplementedError("This corrective migration is intentionally one-way.")

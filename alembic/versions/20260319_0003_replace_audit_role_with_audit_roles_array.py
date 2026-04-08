"""Replace audit_role enum with audit_roles string array.

Revision ID: 20260319_0003
Revises: 20260312_0002
Create Date: 2026-03-19 18:10:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "20260319_0003"
down_revision: str | None = "20260312_0002"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

AUDIT_PARTICIPATION_ROLE_ENUM = postgresql.ENUM(
    "AUDITOR",
    "PLACE_ADMIN",
    "BOTH",
    name="playspace_audit_participation_role",
    create_type=False,
)


def _is_target_product(product_key: str) -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("product", "yee").strip().lower() == product_key


def _has_column(table_name: str, column_name: str) -> bool:
    """Return whether a column currently exists on a table."""

    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = inspector.get_columns(table_name)
    return any(column["name"] == column_name for column in columns)


def upgrade() -> None:
    """Migrate assignment participation storage from enum to role arrays."""

    if not _is_target_product("playspace"):
        return
    has_audit_role = _has_column("auditor_assignments", "audit_role")
    has_audit_roles = _has_column("auditor_assignments", "audit_roles")

    if not has_audit_roles:
        op.add_column(
            "auditor_assignments",
            sa.Column(
                "audit_roles",
                postgresql.ARRAY(sa.String(length=40)),
                nullable=False,
                server_default=sa.text("ARRAY['auditor']::varchar[]"),
            ),
        )

    if has_audit_role:
        op.execute(
            sa.text(
                """
                UPDATE auditor_assignments
                SET audit_roles = CASE audit_role
                    WHEN 'AUDITOR' THEN ARRAY['auditor']::varchar[]
                    WHEN 'PLACE_ADMIN' THEN ARRAY['place_admin']::varchar[]
                    WHEN 'BOTH' THEN ARRAY['auditor', 'place_admin']::varchar[]
                    ELSE ARRAY['auditor']::varchar[]
                END
                """
            )
        )
        op.drop_column("auditor_assignments", "audit_role")
    else:
        op.execute(
            sa.text(
                """
                UPDATE auditor_assignments
                SET audit_roles = ARRAY['auditor']::varchar[]
                WHERE audit_roles IS NULL OR cardinality(audit_roles) = 0
                """
            )
        )

    op.alter_column("auditor_assignments", "audit_roles", server_default=None)
    op.execute(sa.text("DROP TYPE IF EXISTS playspace_audit_participation_role"))


def downgrade() -> None:
    """Recreate enum-based assignment participation storage."""

    if not _is_target_product("playspace"):
        return
    has_audit_role = _has_column("auditor_assignments", "audit_role")
    has_audit_roles = _has_column("auditor_assignments", "audit_roles")

    bind = op.get_bind()
    if not has_audit_role:
        AUDIT_PARTICIPATION_ROLE_ENUM.create(bind, checkfirst=True)
        op.add_column(
            "auditor_assignments",
            sa.Column(
                "audit_role",
                AUDIT_PARTICIPATION_ROLE_ENUM,
                nullable=False,
                server_default=sa.text("'AUDITOR'"),
            ),
        )

    if has_audit_roles:
        op.execute(
            sa.text(
                """
                UPDATE auditor_assignments
                SET audit_role = CASE
                    WHEN audit_roles @> ARRAY['auditor', 'place_admin']::varchar[]
                        THEN 'BOTH'::playspace_audit_participation_role
                    WHEN audit_roles @> ARRAY['place_admin']::varchar[]
                        THEN 'PLACE_ADMIN'::playspace_audit_participation_role
                    ELSE 'AUDITOR'::playspace_audit_participation_role
                END
                """
            )
        )
        op.drop_column("auditor_assignments", "audit_roles")

    op.alter_column("auditor_assignments", "audit_role", server_default=None)

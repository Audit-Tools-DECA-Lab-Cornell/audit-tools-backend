"""link yee submissions to auditor and place

Revision ID: f2d41c7aa991
Revises: a5b1d9ef2204
Create Date: 2026-03-26 15:10:00
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "f2d41c7aa991"
down_revision = "a5b1d9ef2204"
branch_labels = None
depends_on = None


def _is_target_product(product_key: str) -> bool:
    x_args = context.get_x_argument(as_dictionary=True)
    return x_args.get("product", "yee").strip().lower() == product_key


def upgrade() -> None:
    if not _is_target_product("yee"):
        return
    op.add_column(
        "yee_audit_submissions",
        sa.Column("auditor_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "yee_audit_submissions",
        sa.Column("place_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        op.f("ix_yee_audit_submissions_auditor_id"),
        "yee_audit_submissions",
        ["auditor_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_yee_audit_submissions_place_id"),
        "yee_audit_submissions",
        ["place_id"],
        unique=False,
    )
    op.create_foreign_key(
        op.f("fk_yee_audit_submissions_auditor_id_auditors"),
        "yee_audit_submissions",
        "auditors",
        ["auditor_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_foreign_key(
        op.f("fk_yee_audit_submissions_place_id_places"),
        "yee_audit_submissions",
        "places",
        ["place_id"],
        ["id"],
        ondelete="CASCADE",
    )


def downgrade() -> None:
    if not _is_target_product("yee"):
        return
    op.drop_constraint(
        op.f("fk_yee_audit_submissions_place_id_places"),
        "yee_audit_submissions",
        type_="foreignkey",
    )
    op.drop_constraint(
        op.f("fk_yee_audit_submissions_auditor_id_auditors"),
        "yee_audit_submissions",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_yee_audit_submissions_place_id"), table_name="yee_audit_submissions")
    op.drop_index(op.f("ix_yee_audit_submissions_auditor_id"), table_name="yee_audit_submissions")
    op.drop_column("yee_audit_submissions", "place_id")
    op.drop_column("yee_audit_submissions", "auditor_id")

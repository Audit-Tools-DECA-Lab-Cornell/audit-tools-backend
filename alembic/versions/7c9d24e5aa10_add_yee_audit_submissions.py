"""add yee audit submissions

Revision ID: 7c9d24e5aa10
Revises: 4b7d2f9a1c3e
Create Date: 2026-03-06 16:35:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

# revision identifiers, used by Alembic.
revision = "7c9d24e5aa10"
down_revision = "4b7d2f9a1c3e"
branch_labels = None
depends_on = None


def _is_target_product(product_key: str) -> bool:
	x_args = context.get_x_argument(as_dictionary=True)
	return x_args.get("product", "yee").strip().lower() == product_key


def upgrade() -> None:
	if not _is_target_product("yee"):
		return
	op.create_table(
		"yee_audit_submissions",
		sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
		sa.Column(
			"submitted_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.Column(
			"participant_info_json",
			postgresql.JSONB(astext_type=sa.Text()),
			nullable=False,
		),
		sa.Column("responses_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
		sa.Column(
			"section_scores_json",
			postgresql.JSONB(astext_type=sa.Text()),
			nullable=False,
		),
		sa.Column("total_score", sa.Integer(), nullable=False),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_yee_audit_submissions")),
	)


def downgrade() -> None:
	if not _is_target_product("yee"):
		return
	op.drop_table("yee_audit_submissions")

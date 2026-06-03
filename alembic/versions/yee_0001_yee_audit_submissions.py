"""YEE branch base: YEE-only audit submission table.

Root of the ``yee`` migration branch. This table exists ONLY in the YEE
database and is never created in the Playspace database.

    alembic -x product=yee upgrade yee@head

Revision ID: yee_0001
Revises: 0001
Create Date: 2026-06-03

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision = "yee_0001"
down_revision = "0001"
branch_labels = ("yee",)
depends_on = None


def upgrade() -> None:
	# ── yee_audit_submissions ─────────────────────────────────────────────────
	op.create_table(
		"yee_audit_submissions",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("auditor_id", sa.UUID(), nullable=False),
		sa.Column("place_id", sa.UUID(), nullable=False),
		sa.Column("submitted_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.Column("participant_info_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
		sa.Column("responses_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
		sa.Column("section_scores_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
		sa.Column("total_score", sa.Integer(), nullable=False),
		sa.ForeignKeyConstraint(
			["auditor_id"],
			["auditor_profiles.id"],
			name=op.f("fk_yee_audit_submissions_auditor_id_auditor_profiles"),
			ondelete="RESTRICT",
		),
		sa.ForeignKeyConstraint(
			["place_id"],
			["places.id"],
			name=op.f("fk_yee_audit_submissions_place_id_places"),
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_yee_audit_submissions")),
	)
	op.create_index(
		op.f("ix_yee_audit_submissions_yee_audit_submissions_auditor_id"),
		"yee_audit_submissions",
		["auditor_id"],
		unique=False,
	)
	op.create_index(
		op.f("ix_yee_audit_submissions_yee_audit_submissions_place_id"),
		"yee_audit_submissions",
		["place_id"],
		unique=False,
	)


def downgrade() -> None:
	op.drop_table("yee_audit_submissions")

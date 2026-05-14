"""add playspace checklist answers

Revision ID: 20260514_0010
Revises: 20260512_0009
Create Date: 2026-05-14

Adds normalized JSONB storage for checklist-style Playspace question
responses so selected option arrays and optional "other" details round-trip
without being coerced into scale-answer strings.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision = "20260514_0010"
down_revision = "20260512_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
	op.create_table(
		"playspace_checklist_answers",
		sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
		sa.Column("question_response_id", postgresql.UUID(as_uuid=True), nullable=False),
		sa.Column(
			"selected_option_keys",
			postgresql.JSONB(astext_type=sa.Text()),
			server_default=sa.text("'[]'::jsonb"),
			nullable=False,
		),
		sa.Column(
			"other_details",
			postgresql.JSONB(astext_type=sa.Text()),
			server_default=sa.text("'{}'::jsonb"),
			nullable=False,
		),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
		sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
		sa.ForeignKeyConstraint(
			["question_response_id"],
			["playspace_question_responses.id"],
			name="fk_ps_checklist_answer_question_response",
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id"),
		sa.UniqueConstraint("question_response_id", name="uq_playspace_checklist_answers_question_response"),
	)
	op.create_index(
		op.f("ix_playspace_checklist_answers_question_response_id"),
		"playspace_checklist_answers",
		["question_response_id"],
		unique=False,
	)


def downgrade() -> None:
	op.drop_index(
		op.f("ix_playspace_checklist_answers_question_response_id"),
		table_name="playspace_checklist_answers",
	)
	op.drop_table("playspace_checklist_answers")

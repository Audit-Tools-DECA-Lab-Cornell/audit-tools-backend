"""Increase playspace key column lengths to 255 characters.

Revision ID: 20260512_0009
Revises: 20260507_0008
Create Date: 2026-05-12

Expands several key columns from their previous VARCHAR limits to
VARCHAR(255) to avoid StringDataRightTruncation errors when longer
instrument, section, question, or option keys are inserted.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "20260512_0009"
down_revision = "20260507_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
	op.alter_column(
		"playspace_scale_answers",
		"scale_key",
		existing_type=sa.VARCHAR(length=80),
		type_=sa.VARCHAR(length=255),
		existing_nullable=False,
	)

	op.alter_column(
		"playspace_scale_answers",
		"option_key",
		existing_type=sa.VARCHAR(length=80),
		type_=sa.VARCHAR(length=255),
		existing_nullable=False,
	)

	op.alter_column(
		"playspace_question_responses",
		"question_key",
		existing_type=sa.VARCHAR(length=120),
		type_=sa.VARCHAR(length=255),
		existing_nullable=False,
	)

	op.alter_column(
		"playspace_submission_sections",
		"section_key",
		existing_type=sa.VARCHAR(length=80),
		type_=sa.VARCHAR(length=255),
		existing_nullable=False,
	)


def downgrade() -> None:
	op.alter_column(
		"playspace_submission_sections",
		"section_key",
		existing_type=sa.VARCHAR(length=255),
		type_=sa.VARCHAR(length=80),
		existing_nullable=False,
	)

	op.alter_column(
		"playspace_question_responses",
		"question_key",
		existing_type=sa.VARCHAR(length=255),
		type_=sa.VARCHAR(length=120),
		existing_nullable=False,
	)

	op.alter_column(
		"playspace_scale_answers",
		"option_key",
		existing_type=sa.VARCHAR(length=255),
		type_=sa.VARCHAR(length=80),
		existing_nullable=False,
	)

	op.alter_column(
		"playspace_scale_answers",
		"scale_key",
		existing_type=sa.VARCHAR(length=255),
		type_=sa.VARCHAR(length=80),
		existing_nullable=False,
	)

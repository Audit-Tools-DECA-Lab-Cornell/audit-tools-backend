"""add note to playspace_question_responses

Revision ID: 20260507_0008
Revises: 20260507_0007
Create Date: 2026-05-07

Adds an optional free-text ``note`` column to
``playspace_question_responses`` so auditors can leave per-question
comments when the instrument defines a ``notes_prompt`` for that question.
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260507_0008"
down_revision = "20260507_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
	op.add_column(
		"playspace_question_responses",
		sa.Column("note", sa.Text(), nullable=True),
	)


def downgrade() -> None:
	op.drop_column("playspace_question_responses", "note")

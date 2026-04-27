"""Rename playspace_audit_* tables to playspace_submission_*.

The normalized audit tables were renamed in the ORM models and initial migration
after the database was first provisioned. This migration brings the live schema
in sync by renaming the three affected tables and all of their associated
constraints and indexes.

Affected tables:
  playspace_audit_contexts     → playspace_submission_contexts
  playspace_pre_audit_answers  → playspace_pre_submission_answers
  playspace_audit_sections     → playspace_submission_sections

Revision ID: 0002
Revises: 0001
Create Date: 2026-04-27

"""

from __future__ import annotations

from alembic import op

# revision identifiers, used by Alembic.
revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
	# ── Rename tables ────────────────────────────────────────────────────────────

	op.rename_table("playspace_audit_contexts", "playspace_submission_contexts")
	op.rename_table("playspace_pre_audit_answers", "playspace_pre_submission_answers")
	op.rename_table("playspace_audit_sections", "playspace_submission_sections")

	# ── playspace_submission_contexts ────────────────────────────────────────────
	# Rename PK constraint (also renames its underlying index in PostgreSQL).
	op.execute(
		"ALTER TABLE playspace_submission_contexts "
		"RENAME CONSTRAINT pk_playspace_audit_contexts "
		"TO pk_playspace_submission_contexts"
	)

	# ── playspace_pre_submission_answers ─────────────────────────────────────────
	# Rename PK constraint (also renames its underlying index).
	op.execute(
		"ALTER TABLE playspace_pre_submission_answers "
		"RENAME CONSTRAINT pk_playspace_pre_audit_answers "
		"TO pk_playspace_pre_submission_answers"
	)

	# Rename FK constraint.
	op.execute(
		"ALTER TABLE playspace_pre_submission_answers "
		"RENAME CONSTRAINT fk_ps_pre_audit_answer_submission "
		"TO fk_ps_pre_submission_answer_submission"
	)

	# Rename UNIQUE constraint (also renames its underlying index).
	op.execute(
		"ALTER TABLE playspace_pre_submission_answers "
		"RENAME CONSTRAINT uq_playspace_pre_audit_answers_submission_field_value "
		"TO uq_playspace_pre_submission_answers_submission_field_value"
	)

	# Rename the explicit submission_id index (not tied to a constraint).
	op.execute(
		"ALTER INDEX ix_playspace_pre_audit_answers_playspace_pre_audit_answ_a5a7 "
		"RENAME TO ix_playspace_pre_submission_answers_playspace_pre_submission_answers_submission_id"
	)

	# ── playspace_submission_sections ────────────────────────────────────────────
	# Rename PK constraint (also renames its underlying index).
	op.execute(
		"ALTER TABLE playspace_submission_sections "
		"RENAME CONSTRAINT pk_playspace_audit_sections "
		"TO pk_playspace_submission_sections"
	)

	# Rename FK constraint.
	op.execute(
		"ALTER TABLE playspace_submission_sections "
		"RENAME CONSTRAINT fk_ps_audit_section_submission "
		"TO fk_ps_submission_section_submission"
	)

	# Rename UNIQUE constraint (also renames its underlying index).
	op.execute(
		"ALTER TABLE playspace_submission_sections "
		"RENAME CONSTRAINT uq_playspace_audit_sections_submission_section "
		"TO uq_playspace_submission_sections_submission_section"
	)

	# Rename the explicit submission_id index (not tied to a constraint).
	op.execute(
		"ALTER INDEX ix_playspace_audit_sections_playspace_audit_sections_su_821a "
		"RENAME TO ix_playspace_submission_sections_playspace_submission_sections_submission_id"
	)


def downgrade() -> None:
	# ── playspace_submission_sections ────────────────────────────────────────────

	op.execute(
		"ALTER INDEX ix_playspace_submission_sections_playspace_submission_sections_submission_id "
		"RENAME TO ix_playspace_audit_sections_playspace_audit_sections_su_821a"
	)
	op.execute(
		"ALTER TABLE playspace_submission_sections "
		"RENAME CONSTRAINT uq_playspace_submission_sections_submission_section "
		"TO uq_playspace_audit_sections_submission_section"
	)
	op.execute(
		"ALTER TABLE playspace_submission_sections "
		"RENAME CONSTRAINT fk_ps_submission_section_submission "
		"TO fk_ps_audit_section_submission"
	)
	op.execute(
		"ALTER TABLE playspace_submission_sections "
		"RENAME CONSTRAINT pk_playspace_submission_sections "
		"TO pk_playspace_audit_sections"
	)

	# ── playspace_pre_submission_answers ─────────────────────────────────────────

	op.execute(
		"ALTER INDEX ix_playspace_pre_submission_answers_playspace_pre_submission_answers_submission_id "
		"RENAME TO ix_playspace_pre_audit_answers_playspace_pre_audit_answ_a5a7"
	)
	op.execute(
		"ALTER TABLE playspace_pre_submission_answers "
		"RENAME CONSTRAINT uq_playspace_pre_submission_answers_submission_field_value "
		"TO uq_playspace_pre_audit_answers_submission_field_value"
	)
	op.execute(
		"ALTER TABLE playspace_pre_submission_answers "
		"RENAME CONSTRAINT fk_ps_pre_submission_answer_submission "
		"TO fk_ps_pre_audit_answer_submission"
	)
	op.execute(
		"ALTER TABLE playspace_pre_submission_answers "
		"RENAME CONSTRAINT pk_playspace_pre_submission_answers "
		"TO pk_playspace_pre_audit_answers"
	)

	# ── playspace_submission_contexts ────────────────────────────────────────────

	op.execute(
		"ALTER TABLE playspace_submission_contexts "
		"RENAME CONSTRAINT pk_playspace_submission_contexts "
		"TO pk_playspace_audit_contexts"
	)

	# ── Rename tables back ───────────────────────────────────────────────────────

	op.rename_table("playspace_submission_sections", "playspace_audit_sections")
	op.rename_table("playspace_pre_submission_answers", "playspace_pre_audit_answers")
	op.rename_table("playspace_submission_contexts", "playspace_audit_contexts")

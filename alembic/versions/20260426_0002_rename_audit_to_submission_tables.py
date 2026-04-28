"""Rename playspace_audit_* tables to playspace_submission_*.

The normalized audit tables were renamed in the ORM models and initial migration
after the database was first provisioned. This migration brings the live schema
in sync by renaming the three affected tables and all of their associated
constraints and indexes.

Affected tables:
  playspace_audit_contexts     → playspace_submission_contexts
  playspace_pre_audit_answers  → playspace_pre_submission_answers
  playspace_audit_sections     → playspace_submission_sections

Revision ID: 20260426_0002
Revises: 0001
Create Date: 2026-04-26

"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = "20260426_0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def _table_exists(name: str) -> bool:
	return name in sa.inspect(op.get_bind()).get_table_names()


def _constraint_exists(table_name: str, constraint_name: str) -> bool:
	row = op.get_bind().execute(
		sa.text(
			"SELECT 1 "
			"FROM pg_constraint c "
			"JOIN pg_class t ON t.oid = c.conrelid "
			"JOIN pg_namespace n ON n.oid = t.relnamespace "
			"WHERE n.nspname = current_schema() "
			"AND t.relname = :table_name "
			"AND c.conname = :constraint_name"
		),
		{"table_name": table_name, "constraint_name": constraint_name},
	).first()
	return row is not None


def _index_exists(index_name: str) -> bool:
	row = op.get_bind().execute(
		sa.text(
			"SELECT 1 "
			"FROM pg_class i "
			"JOIN pg_namespace n ON n.oid = i.relnamespace "
			"WHERE n.nspname = current_schema() "
			"AND i.relkind = 'i' "
			"AND i.relname = :index_name"
		),
		{"index_name": index_name},
	).first()
	return row is not None


def _rename_table_if_needed(old_name: str, new_name: str) -> None:
	if _table_exists(old_name) and not _table_exists(new_name):
		op.rename_table(old_name, new_name)


def _rename_constraint_if_needed(table_name: str, old_name: str, new_name: str) -> None:
	if _table_exists(table_name) and _constraint_exists(table_name, old_name) and not _constraint_exists(table_name, new_name):
		op.execute(
			sa.text(f'ALTER TABLE "{table_name}" RENAME CONSTRAINT "{old_name}" TO "{new_name}"')
		)


def _rename_index_if_needed(old_name: str, new_name: str) -> None:
	if _index_exists(old_name) and not _index_exists(new_name):
		op.execute(sa.text(f'ALTER INDEX "{old_name}" RENAME TO "{new_name}"'))


def upgrade() -> None:
	# ── Rename tables ────────────────────────────────────────────────────────────
	# 0001 is now a squashed baseline that already creates the submission_* names.
	# Keep this migration safe for both fresh databases and older databases that
	# still have the legacy audit_* tables.

	_rename_table_if_needed("playspace_audit_contexts", "playspace_submission_contexts")
	_rename_table_if_needed("playspace_pre_audit_answers", "playspace_pre_submission_answers")
	_rename_table_if_needed("playspace_audit_sections", "playspace_submission_sections")

	# ── playspace_submission_contexts ────────────────────────────────────────────
	_rename_constraint_if_needed(
		"playspace_submission_contexts",
		"pk_playspace_audit_contexts",
		"pk_playspace_submission_contexts",
	)

	# ── playspace_pre_submission_answers ─────────────────────────────────────────
	_rename_constraint_if_needed(
		"playspace_pre_submission_answers",
		"pk_playspace_pre_audit_answers",
		"pk_playspace_pre_submission_answers",
	)
	_rename_constraint_if_needed(
		"playspace_pre_submission_answers",
		"fk_ps_pre_audit_answer_submission",
		"fk_ps_pre_submission_answer_submission",
	)
	_rename_constraint_if_needed(
		"playspace_pre_submission_answers",
		"uq_playspace_pre_audit_answers_submission_field_value",
		"uq_playspace_pre_submission_answers_submission_field_value",
	)
	_rename_index_if_needed(
		"ix_playspace_pre_audit_answers_playspace_pre_audit_answ_a5a7",
		"ix_playspace_pre_submission_answers_playspace_pre_submission_answers_submission_id",
	)

	# ── playspace_submission_sections ────────────────────────────────────────────
	_rename_constraint_if_needed(
		"playspace_submission_sections",
		"pk_playspace_audit_sections",
		"pk_playspace_submission_sections",
	)
	_rename_constraint_if_needed(
		"playspace_submission_sections",
		"fk_ps_audit_section_submission",
		"fk_ps_submission_section_submission",
	)
	_rename_constraint_if_needed(
		"playspace_submission_sections",
		"uq_playspace_audit_sections_submission_section",
		"uq_playspace_submission_sections_submission_section",
	)
	_rename_index_if_needed(
		"ix_playspace_audit_sections_playspace_audit_sections_su_821a",
		"ix_playspace_submission_sections_playspace_submission_sections_submission_id",
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

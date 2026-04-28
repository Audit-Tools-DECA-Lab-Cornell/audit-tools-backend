"""Backfill playspace_submissions from audits table.

Migration 0003 modified the playspace_submissions table but did not copy
submitted audits that only existed in the shared ``audits`` table.  This
migration inserts the missing rows so the Playspace dashboard surfaces them
again.

Only audits with no corresponding playspace_submissions row (matched by the
``(project_id, place_id, auditor_profile_id)`` triple) are copied.  The JSONB
payload columns (``responses_json``, ``scores_json``) carry across verbatim,
preserving the full submitted snapshot.

Revision ID: 20260428_0004
Revises: 20260426_0003
Create Date: 2026-04-28
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "20260428_0004"
down_revision = "20260426_0003"
branch_labels = None
depends_on = None


def _table_exists(table_name: str) -> bool:
	bind = op.get_bind()
	inspector = sa.inspect(bind)
	return table_name in inspector.get_table_names()


def upgrade() -> None:
	context_join = ""
	execution_mode_expression = (
		"COALESCE(a.responses_json #>> '{meta,execution_mode}', a.scores_json ->> 'execution_mode')"
	)
	if _table_exists("playspace_audit_contexts"):
		context_join = "LEFT JOIN playspace_audit_contexts pac ON pac.audit_id = a.id "
		execution_mode_expression = (
			"COALESCE(pac.execution_mode, a.responses_json #>> '{meta,execution_mode}', "
			"a.scores_json ->> 'execution_mode')"
		)

	overall_play_value = "(a.scores_json -> 'overall' ->> 'play_value_total')::double precision"
	overall_usability = "(a.scores_json -> 'overall' ->> 'usability_total')::double precision"
	audit_play_value = (
		"CASE WHEN "
		f"{execution_mode_expression} IN ('audit', 'both') "
		"AND jsonb_typeof(a.scores_json -> 'overall' -> 'play_value_total') = 'number' "
		f"THEN {overall_play_value} ELSE NULL END"
	)
	audit_usability = (
		"CASE WHEN "
		f"{execution_mode_expression} IN ('audit', 'both') "
		"AND jsonb_typeof(a.scores_json -> 'overall' -> 'usability_total') = 'number' "
		f"THEN {overall_usability} ELSE NULL END"
	)
	survey_play_value = (
		"CASE WHEN "
		f"{execution_mode_expression} IN ('survey', 'both') "
		"AND jsonb_typeof(a.scores_json -> 'overall' -> 'play_value_total') = 'number' "
		f"THEN {overall_play_value} ELSE NULL END"
	)
	survey_usability = (
		"CASE WHEN "
		f"{execution_mode_expression} IN ('survey', 'both') "
		"AND jsonb_typeof(a.scores_json -> 'overall' -> 'usability_total') = 'number' "
		f"THEN {overall_usability} ELSE NULL END"
	)

	op.execute(
		"INSERT INTO playspace_submissions "
		"(id, project_id, place_id, auditor_profile_id, audit_code, "
		" instrument_key, instrument_version, execution_mode, status, started_at, "
		" submitted_at, total_minutes, summary_score, "
		" audit_play_value_score, audit_usability_score, "
		" survey_play_value_score, survey_usability_score, "
		" responses_json, scores_json, created_at, updated_at) "
		"SELECT "
		" a.id, a.project_id, a.place_id, a.auditor_profile_id, a.audit_code, "
		" a.instrument_key, a.instrument_version, "
		f" {execution_mode_expression}, "
		" a.status, a.started_at, a.submitted_at, a.total_minutes, "
		" a.summary_score, "
		f" {audit_play_value}, {audit_usability}, "
		f" {survey_play_value}, {survey_usability}, "
		" a.responses_json, a.scores_json, "
		" a.created_at, a.updated_at "
		"FROM audits a "
		f"{context_join}"
		"WHERE NOT EXISTS ("
		" SELECT 1 FROM playspace_submissions ps "
		" WHERE ps.project_id = a.project_id "
		"   AND ps.place_id = a.place_id "
		"   AND ps.auditor_profile_id = a.auditor_profile_id"
		")"
	)


def downgrade() -> None:
	op.execute("DELETE FROM playspace_submissions ps USING audits a WHERE ps.id = a.id")

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

revision = "20260428_0004"
down_revision = "20260426_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
	op.execute(
		"INSERT INTO playspace_submissions "
		"(id, project_id, place_id, auditor_profile_id, audit_code, "
		" instrument_key, instrument_version, status, started_at, "
		" submitted_at, total_minutes, summary_score, "
		" responses_json, scores_json, created_at, updated_at) "
		"SELECT "
		" a.id, a.project_id, a.place_id, a.auditor_profile_id, a.audit_code, "
		" a.instrument_key, a.instrument_version, "
		" a.status, a.started_at, a.submitted_at, a.total_minutes, "
		" a.summary_score, a.responses_json, a.scores_json, "
		" a.created_at, a.updated_at "
		"FROM audits a "
		"WHERE NOT EXISTS ("
		" SELECT 1 FROM playspace_submissions ps "
		" WHERE ps.project_id = a.project_id "
		"   AND ps.place_id = a.place_id "
		"   AND ps.auditor_profile_id = a.auditor_profile_id"
		")"
	)


def downgrade() -> None:
	op.execute("DELETE FROM playspace_submissions ps USING audits a WHERE ps.id = a.id")

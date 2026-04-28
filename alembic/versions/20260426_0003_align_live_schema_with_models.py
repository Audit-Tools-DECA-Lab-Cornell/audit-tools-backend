"""Align live database schema with current ORM models.

This migration brings the existing production database (as of the schema.sql
dump) in line with the current SQLAlchemy model definitions.  Before running,
the database must be stamped at revision 0002:

    alembic -x product=playspace stamp 20260426_0002
    alembic -x product=playspace upgrade 20260426_0003

Changes applied
───────────────
Phase 1 — Modify existing tables
  1a. accounts: drop legacy ``password_hash`` column
  1b. playspace_submissions: add ``execution_mode`` + ``draft_progress_percent``,
      migrate data from ``submission_kind``, drop ``submission_kind``
  1c. playspace_submission_contexts: add ``schema_version`` and ``revision``
      columns; rename FK to match model

Phase 2 — Create new tables (conditional — safe for both live and clean paths)
  2a. playspace_submission_sections
  2b. playspace_pre_submission_answers
  2c. audits (if not exists — covers gap in migration 0001)
  2d. yee_audit_submissions

Phase 3 — Data migration (old audit-linked → new submission-linked tables)
  3a. playspace_audit_sections       → playspace_submission_sections
  3b. playspace_pre_audit_answers    → playspace_pre_submission_answers
  3c. playspace_audit_contexts       → playspace_submission_contexts (merge)

Phase 4 — Redirect FK on playspace_question_responses
  4a. Drop FK to playspace_audit_sections
  4b. Delete orphaned question_responses (cascades to scale_answers)
  4c. Add FK to playspace_submission_sections

Phase 5 — Drop old tables
  playspace_pre_audit_answers, playspace_audit_sections, playspace_audit_contexts

Revision ID: 20260426_0003
Revises: 20260426_0002
Create Date: 2026-04-26
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models import AccountType, AuditStatus

revision = "20260426_0003"
down_revision = "20260426_0002"
branch_labels = None
depends_on = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _table_exists(name: str) -> bool:
	"""Return True when *name* already exists in the current database."""
	conn = op.get_bind()
	insp = sa.inspect(conn)
	return name in insp.get_table_names()


# ---------------------------------------------------------------------------
# upgrade
# ---------------------------------------------------------------------------


def upgrade() -> None:
	# ── Phase 1: Modify existing tables ──────────────────────────────────

	# 1a. accounts — drop legacy password_hash
	if _table_exists("accounts"):
		op.drop_column("accounts", "password_hash")

	# 1b. playspace_submissions — submission_kind → execution_mode + new column
	if _table_exists("playspace_submissions"):
		op.add_column(
			"playspace_submissions",
			sa.Column("execution_mode", sa.String(length=20), nullable=True),
		)
		op.add_column(
			"playspace_submissions",
			sa.Column("draft_progress_percent", sa.Float(), nullable=True),
		)
		op.execute(
			"UPDATE playspace_submissions "
			"SET execution_mode = LEFT(submission_kind, 20) "
			"WHERE submission_kind IS NOT NULL"
		)
		op.drop_column("playspace_submissions", "submission_kind")

	# 1c. playspace_submission_contexts — add schema_version + revision, rename FK
	if _table_exists("playspace_submission_contexts"):
		op.add_column(
			"playspace_submission_contexts",
			sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
		)
		op.add_column(
			"playspace_submission_contexts",
			sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
		)
		op.drop_constraint(
			"fk_ps_submission_context_submission",
			"playspace_submission_contexts",
			type_="foreignkey",
		)
		op.create_foreign_key(
			"fk_ps_context_submission",
			"playspace_submission_contexts",
			"playspace_submissions",
			["submission_id"],
			["id"],
			ondelete="CASCADE",
		)

	# ── Phase 2: Create new tables (conditionally) ──────────────────────

	# 2a. playspace_submission_sections
	if not _table_exists("playspace_submission_sections"):
		op.create_table(
			"playspace_submission_sections",
			sa.Column("id", sa.UUID(), nullable=False),
			sa.Column("submission_id", sa.UUID(), nullable=False),
			sa.Column("section_key", sa.String(length=120), nullable=False),
			sa.Column("note", sa.Text(), nullable=True),
			sa.Column(
				"created_at",
				sa.DateTime(timezone=True),
				server_default=sa.text("now()"),
				nullable=False,
			),
			sa.Column(
				"updated_at",
				sa.DateTime(timezone=True),
				server_default=sa.text("now()"),
				nullable=False,
			),
			sa.ForeignKeyConstraint(
				["submission_id"],
				["playspace_submissions.id"],
				name="fk_ps_submission_section_submission",
				ondelete="CASCADE",
			),
			sa.PrimaryKeyConstraint("id", name=op.f("pk_playspace_submission_sections")),
			sa.UniqueConstraint(
				"submission_id",
				"section_key",
				name="uq_playspace_submission_sections_submission_section",
			),
		)
		op.create_index(
			op.f("ix_playspace_submission_sections_playspace_submission_sections_submission_id"),
			"playspace_submission_sections",
			["submission_id"],
			unique=False,
		)

	# 2b. playspace_pre_submission_answers
	if not _table_exists("playspace_pre_submission_answers"):
		op.create_table(
			"playspace_pre_submission_answers",
			sa.Column("id", sa.UUID(), nullable=False),
			sa.Column("submission_id", sa.UUID(), nullable=False),
			sa.Column("field_key", sa.String(length=80), nullable=False),
			sa.Column("selected_value", sa.String(length=80), nullable=False),
			sa.Column("sort_order", sa.Integer(), nullable=False),
			sa.Column(
				"created_at",
				sa.DateTime(timezone=True),
				server_default=sa.text("now()"),
				nullable=False,
			),
			sa.ForeignKeyConstraint(
				["submission_id"],
				["playspace_submissions.id"],
				name="fk_ps_pre_submission_answer_submission",
				ondelete="CASCADE",
			),
			sa.PrimaryKeyConstraint("id", name=op.f("pk_playspace_pre_submission_answers")),
			sa.UniqueConstraint(
				"submission_id",
				"field_key",
				"selected_value",
				name="uq_playspace_pre_submission_answers_submission_field_value",
			),
		)
		op.create_index(
			op.f("ix_playspace_pre_submission_answers_playspace_pre_submission_answers_submission_id"),
			"playspace_pre_submission_answers",
			["submission_id"],
			unique=False,
		)

	# 2c. audits (covers gap in 0001 — already present on the live DB)
	if not _table_exists("audits"):
		op.create_table(
			"audits",
			sa.Column("id", sa.UUID(), nullable=False),
			sa.Column("project_id", sa.UUID(), nullable=False),
			sa.Column("place_id", sa.UUID(), nullable=False),
			sa.Column("auditor_profile_id", sa.UUID(), nullable=False),
			sa.Column("audit_code", sa.String(length=120), nullable=False),
			sa.Column("instrument_key", sa.String(length=80), nullable=True),
			sa.Column("instrument_version", sa.String(length=40), nullable=True),
			sa.Column(
				"status",
				postgresql.ENUM(AuditStatus, name="shared_audit_status", create_type=False),
				nullable=False,
			),
			sa.Column(
				"started_at",
				sa.DateTime(timezone=True),
				server_default=sa.text("now()"),
				nullable=False,
			),
			sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
			sa.Column("total_minutes", sa.Integer(), nullable=True),
			sa.Column("summary_score", sa.Float(), nullable=True),
			sa.Column(
				"responses_json",
				postgresql.JSONB(astext_type=sa.Text()),
				nullable=False,
			),
			sa.Column(
				"scores_json",
				postgresql.JSONB(astext_type=sa.Text()),
				nullable=False,
			),
			sa.Column(
				"created_at",
				sa.DateTime(timezone=True),
				server_default=sa.text("now()"),
				nullable=False,
			),
			sa.Column(
				"updated_at",
				sa.DateTime(timezone=True),
				server_default=sa.text("now()"),
				nullable=False,
			),
			sa.ForeignKeyConstraint(
				["auditor_profile_id"],
				["auditor_profiles.id"],
				name=op.f("fk_audits_auditor_profile_id_auditor_profiles"),
				ondelete="CASCADE",
			),
			sa.ForeignKeyConstraint(
				["place_id"],
				["places.id"],
				name=op.f("fk_audits_place_id_places"),
				ondelete="CASCADE",
			),
			sa.ForeignKeyConstraint(
				["project_id"],
				["projects.id"],
				name=op.f("fk_audits_project_id_projects"),
				ondelete="CASCADE",
			),
			sa.ForeignKeyConstraint(
				["project_id", "place_id"],
				["project_places.project_id", "project_places.place_id"],
				name="fk_audits_project_place_pair",
				ondelete="CASCADE",
			),
			sa.PrimaryKeyConstraint("id", name=op.f("pk_audits")),
			sa.UniqueConstraint(
				"project_id",
				"place_id",
				"auditor_profile_id",
				name="uq_audits_project_place_auditor",
			),
		)
		op.create_index(op.f("ix_audits_audits_audit_code"), "audits", ["audit_code"], unique=True)
		op.create_index(
			op.f("ix_audits_audits_auditor_profile_id"),
			"audits",
			["auditor_profile_id"],
			unique=False,
		)
		op.create_index(op.f("ix_audits_audits_place_id"), "audits", ["place_id"], unique=False)
		op.create_index(op.f("ix_audits_audits_project_id"), "audits", ["project_id"], unique=False)

	# 2d. yee_audit_submissions
	if not _table_exists("yee_audit_submissions"):
		op.create_table(
			"yee_audit_submissions",
			sa.Column("id", sa.UUID(), nullable=False),
			sa.Column("auditor_id", sa.UUID(), nullable=False),
			sa.Column("place_id", sa.UUID(), nullable=False),
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
			sa.Column(
				"responses_json",
				postgresql.JSONB(astext_type=sa.Text()),
				nullable=False,
			),
			sa.Column(
				"section_scores_json",
				postgresql.JSONB(astext_type=sa.Text()),
				nullable=False,
			),
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

	# ── Phase 3: Data migration (old audit-linked → new submission-linked) ─

	# 3a. playspace_audit_sections → playspace_submission_sections
	if _table_exists("playspace_audit_sections"):
		op.execute(
			"INSERT INTO playspace_submission_sections"
			"	(id, submission_id, section_key, note, created_at, updated_at) "
			"SELECT"
			"	pas.id, ps.id, pas.section_key, pas.note,"
			"	pas.created_at, pas.updated_at "
			"FROM playspace_audit_sections pas "
			"JOIN audits a ON pas.audit_id = a.id "
			"JOIN playspace_submissions ps"
			"	ON ps.project_id = a.project_id"
			"	AND ps.place_id = a.place_id"
			"	AND ps.auditor_profile_id = a.auditor_profile_id "
			"ON CONFLICT (id) DO NOTHING"
		)

	# 3b. playspace_pre_audit_answers → playspace_pre_submission_answers
	if _table_exists("playspace_pre_audit_answers"):
		op.execute(
			"INSERT INTO playspace_pre_submission_answers"
			"	(id, submission_id, field_key, selected_value, sort_order, created_at) "
			"SELECT"
			"	ppa.id, ps.id, ppa.field_key, ppa.selected_value,"
			"	ppa.sort_order, ppa.created_at "
			"FROM playspace_pre_audit_answers ppa "
			"JOIN audits a ON ppa.audit_id = a.id "
			"JOIN playspace_submissions ps"
			"	ON ps.project_id = a.project_id"
			"	AND ps.place_id = a.place_id"
			"	AND ps.auditor_profile_id = a.auditor_profile_id "
			"ON CONFLICT (id) DO NOTHING"
		)

	# 3c. playspace_audit_contexts → playspace_submission_contexts (merge)
	if _table_exists("playspace_audit_contexts"):
		op.execute(
			"INSERT INTO playspace_submission_contexts"
			"	(submission_id, execution_mode, draft_progress_percent,"
			"	 created_at, updated_at) "
			"SELECT"
			"	ps.id, pac.execution_mode, pac.draft_progress_percent,"
			"	pac.created_at, pac.updated_at "
			"FROM playspace_audit_contexts pac "
			"JOIN audits a ON pac.audit_id = a.id "
			"JOIN playspace_submissions ps"
			"	ON ps.project_id = a.project_id"
			"	AND ps.place_id = a.place_id"
			"	AND ps.auditor_profile_id = a.auditor_profile_id "
			"ON CONFLICT (submission_id) DO NOTHING"
		)

	# ── Phase 4: Redirect FK on playspace_question_responses ────────────

	# 4a. Drop old FK (points to playspace_audit_sections)
	op.drop_constraint(
		"fk_ps_question_response_section",
		"playspace_question_responses",
		type_="foreignkey",
	)

	# 4b. Delete orphaned rows whose section_id has no match in the new table
	op.execute(
		"DELETE FROM playspace_question_responses "
		"WHERE section_id NOT IN (SELECT id FROM playspace_submission_sections)"
	)

	# 4c. Add FK pointing to the new submission-sections table
	op.create_foreign_key(
		"fk_ps_question_response_section",
		"playspace_question_responses",
		"playspace_submission_sections",
		["section_id"],
		["id"],
		ondelete="CASCADE",
	)

	# ── Phase 5: Drop old tables (children first) ───────────────────────

	if _table_exists("playspace_pre_audit_answers"):
		op.drop_table("playspace_pre_audit_answers")

	if _table_exists("playspace_audit_sections"):
		op.drop_table("playspace_audit_sections")

	if _table_exists("playspace_audit_contexts"):
		op.drop_table("playspace_audit_contexts")


# ---------------------------------------------------------------------------
# downgrade  (structural only — data migration is not reversible)
# ---------------------------------------------------------------------------


def downgrade() -> None:
	# ── Reverse Phase 5: Recreate old tables ─────────────────────────────

	op.create_table(
		"playspace_audit_contexts",
		sa.Column("audit_id", sa.UUID(), nullable=False),
		sa.Column("execution_mode", sa.String(length=20), nullable=True),
		sa.Column("draft_progress_percent", sa.Float(), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.Column(
			"updated_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.ForeignKeyConstraint(
			["audit_id"],
			["audits.id"],
			name="fk_ps_context_audit",
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("audit_id", name="pk_playspace_audit_contexts"),
	)

	op.create_table(
		"playspace_audit_sections",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("audit_id", sa.UUID(), nullable=False),
		sa.Column("section_key", sa.String(length=120), nullable=False),
		sa.Column("note", sa.Text(), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.Column(
			"updated_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.ForeignKeyConstraint(
			["audit_id"],
			["audits.id"],
			name="fk_ps_audit_section_audit",
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id", name="pk_playspace_audit_sections"),
		sa.UniqueConstraint(
			"audit_id",
			"section_key",
			name="uq_playspace_audit_sections_audit_section",
		),
	)
	op.create_index(
		"ix_playspace_audit_sections_playspace_audit_sections_audit_id",
		"playspace_audit_sections",
		["audit_id"],
		unique=False,
	)

	op.create_table(
		"playspace_pre_audit_answers",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("audit_id", sa.UUID(), nullable=False),
		sa.Column("field_key", sa.String(length=80), nullable=False),
		sa.Column("selected_value", sa.String(length=80), nullable=False),
		sa.Column("sort_order", sa.Integer(), nullable=False),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.ForeignKeyConstraint(
			["audit_id"],
			["audits.id"],
			name="fk_ps_pre_audit_answer_audit",
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id", name="pk_playspace_pre_audit_answers"),
		sa.UniqueConstraint(
			"audit_id",
			"field_key",
			"selected_value",
			name="uq_playspace_pre_audit_answers_audit_field_value",
		),
	)
	op.create_index(
		"ix_playspace_pre_audit_answers_playspace_pre_audit_answ_d6df",
		"playspace_pre_audit_answers",
		["audit_id"],
		unique=False,
	)

	# ── Reverse Phase 4: Redirect FK back to playspace_audit_sections ───

	op.drop_constraint(
		"fk_ps_question_response_section",
		"playspace_question_responses",
		type_="foreignkey",
	)
	op.create_foreign_key(
		"fk_ps_question_response_section",
		"playspace_question_responses",
		"playspace_audit_sections",
		["section_id"],
		["id"],
		ondelete="CASCADE",
	)

	# ── Reverse Phase 2: Drop new tables ────────────────────────────────

	if _table_exists("yee_audit_submissions"):
		op.drop_table("yee_audit_submissions")

	if _table_exists("playspace_pre_submission_answers"):
		op.drop_table("playspace_pre_submission_answers")

	if _table_exists("playspace_submission_sections"):
		op.drop_table("playspace_submission_sections")

	# ── Reverse Phase 1c: playspace_submission_contexts ──────────────────

	op.drop_constraint(
		"fk_ps_context_submission",
		"playspace_submission_contexts",
		type_="foreignkey",
	)
	op.create_foreign_key(
		"fk_ps_submission_context_submission",
		"playspace_submission_contexts",
		"playspace_submissions",
		["submission_id"],
		["id"],
		ondelete="CASCADE",
	)
	op.drop_column("playspace_submission_contexts", "revision")
	op.drop_column("playspace_submission_contexts", "schema_version")

	# ── Reverse Phase 1b: playspace_submissions ──────────────────────────

	op.add_column(
		"playspace_submissions",
		sa.Column("submission_kind", sa.String(length=40), nullable=True),
	)
	op.execute("UPDATE playspace_submissions SET submission_kind = execution_mode WHERE execution_mode IS NOT NULL")
	op.drop_column("playspace_submissions", "draft_progress_percent")
	op.drop_column("playspace_submissions", "execution_mode")

	# ── Reverse Phase 1a: accounts ───────────────────────────────────────

	op.add_column(
		"accounts",
		sa.Column("password_hash", sa.String(length=255), nullable=True),
	)

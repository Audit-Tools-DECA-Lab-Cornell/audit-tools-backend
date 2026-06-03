"""Playspace branch base: Playspace-only audit tables.

Root of the ``playspace`` migration branch. These tables exist ONLY in the
Playspace database and are never created in the YEE database.

    alembic -x product=playspace upgrade playspace@head

Revision ID: ps_0001
Revises: 0001
Create Date: 2026-04-27

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models import AuditStatus

# revision identifiers, used by Alembic.
revision = "ps_0001"
down_revision = "0001"
branch_labels = ("playspace",)
depends_on = None


def upgrade() -> None:
	# ── playspace_submissions ────────────────────────────────────────────────
	op.create_table(
		"playspace_submissions",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("project_id", sa.UUID(), nullable=False),
		sa.Column("place_id", sa.UUID(), nullable=False),
		sa.Column("auditor_profile_id", sa.UUID(), nullable=False),
		sa.Column("audit_code", sa.String(length=120), nullable=False),
		sa.Column("instrument_key", sa.String(length=80), nullable=True),
		sa.Column("instrument_version", sa.String(length=40), nullable=True),
		sa.Column("execution_mode", sa.String(length=20), nullable=True),
		sa.Column("draft_progress_percent", sa.Float(), nullable=True),
		sa.Column(
			"status",
			postgresql.ENUM(AuditStatus, name="shared_audit_status", create_type=False),
			nullable=False,
		),
		sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
		sa.Column("total_minutes", sa.Integer(), nullable=True),
		sa.Column("summary_score", sa.Float(), nullable=True),
		sa.Column("audit_play_value_score", sa.Float(), nullable=True),
		sa.Column("audit_usability_score", sa.Float(), nullable=True),
		sa.Column("survey_play_value_score", sa.Float(), nullable=True),
		sa.Column("survey_usability_score", sa.Float(), nullable=True),
		sa.Column("responses_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
		sa.Column("scores_json", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.ForeignKeyConstraint(
			["auditor_profile_id"],
			["auditor_profiles.id"],
			name=op.f("fk_playspace_submissions_auditor_profile_id_auditor_profiles"),
			ondelete="CASCADE",
		),
		sa.ForeignKeyConstraint(
			["place_id"],
			["places.id"],
			name=op.f("fk_playspace_submissions_place_id_places"),
			ondelete="CASCADE",
		),
		sa.ForeignKeyConstraint(
			["project_id"],
			["projects.id"],
			name=op.f("fk_playspace_submissions_project_id_projects"),
			ondelete="CASCADE",
		),
		sa.ForeignKeyConstraint(
			["project_id", "place_id"],
			["project_places.project_id", "project_places.place_id"],
			name="fk_playspace_submissions_project_place_pair",
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_playspace_submissions")),
		sa.UniqueConstraint("audit_code", name=op.f("uq_playspace_submissions_audit_code")),
		sa.UniqueConstraint(
			"project_id",
			"place_id",
			"auditor_profile_id",
			name="uq_playspace_submissions_project_place_auditor",
		),
	)
	op.create_index(
		op.f("ix_playspace_submissions_playspace_submissions_auditor_profile_id"),
		"playspace_submissions",
		["auditor_profile_id"],
		unique=False,
	)
	op.create_index(
		op.f("ix_playspace_submissions_playspace_submissions_audit_code"),
		"playspace_submissions",
		["audit_code"],
		unique=True,
	)
	op.create_index(
		op.f("ix_playspace_submissions_playspace_submissions_place_id"),
		"playspace_submissions",
		["place_id"],
		unique=False,
	)
	op.create_index(
		op.f("ix_playspace_submissions_playspace_submissions_project_id"),
		"playspace_submissions",
		["project_id"],
		unique=False,
	)

	# ── playspace_submission_contexts ─────────────────────────────────────────────
	op.create_table(
		"playspace_submission_contexts",
		sa.Column("submission_id", sa.UUID(), nullable=False),
		sa.Column("execution_mode", sa.String(length=20), nullable=True),
		sa.Column("draft_progress_percent", sa.Float(), nullable=True),
		sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
		sa.Column("revision", sa.Integer(), server_default="0", nullable=False),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.ForeignKeyConstraint(
			["submission_id"],
			["playspace_submissions.id"],
			name="fk_ps_context_submission",
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("submission_id"),
	)

	# ── playspace_pre_submission_answers ──────────────────────────────────────────
	op.create_table(
		"playspace_pre_submission_answers",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("submission_id", sa.UUID(), nullable=False),
		sa.Column("field_key", sa.String(length=80), nullable=False),
		sa.Column("selected_value", sa.String(length=80), nullable=False),
		sa.Column("sort_order", sa.Integer(), nullable=False),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.ForeignKeyConstraint(
			["submission_id"],
			["playspace_submissions.id"],
			name="fk_ps_pre_submission_answer_submission",
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id"),
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

	# ── playspace_submission_sections ─────────────────────────────────────────────
	op.create_table(
		"playspace_submission_sections",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("submission_id", sa.UUID(), nullable=False),
		sa.Column("section_key", sa.String(length=255), nullable=False),
		sa.Column("note", sa.Text(), nullable=True),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.ForeignKeyConstraint(
			["submission_id"],
			["playspace_submissions.id"],
			name="fk_ps_submission_section_submission",
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id"),
		sa.UniqueConstraint("submission_id", "section_key", name="uq_playspace_submission_sections_submission_section"),
	)
	op.create_index(
		op.f("ix_playspace_submission_sections_playspace_submission_sections_submission_id"),
		"playspace_submission_sections",
		["submission_id"],
		unique=False,
	)

	# ── playspace_question_responses ─────────────────────────────────────────
	op.create_table(
		"playspace_question_responses",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("section_id", sa.UUID(), nullable=False),
		sa.Column("question_key", sa.String(length=255), nullable=False),
		sa.Column("note", sa.Text(), nullable=True),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.ForeignKeyConstraint(
			["section_id"],
			["playspace_submission_sections.id"],
			name="fk_ps_question_response_section",
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id"),
		sa.UniqueConstraint("section_id", "question_key", name="uq_playspace_question_responses_section_question"),
	)
	op.create_index(
		op.f("ix_playspace_question_responses_playspace_question_responses_section_id"),
		"playspace_question_responses",
		["section_id"],
		unique=False,
	)

	# ── playspace_scale_answers ──────────────────────────────────────────────
	op.create_table(
		"playspace_scale_answers",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("question_response_id", sa.UUID(), nullable=False),
		sa.Column("scale_key", sa.String(length=255), nullable=False),
		sa.Column("option_key", sa.String(length=255), nullable=False),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.ForeignKeyConstraint(
			["question_response_id"],
			["playspace_question_responses.id"],
			name="fk_ps_scale_answer_question_response",
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id"),
		sa.UniqueConstraint("question_response_id", "scale_key", name="uq_playspace_scale_answers_question_scale"),
	)
	op.create_index(
		op.f("ix_playspace_scale_answers_playspace_scale_answers_question_response_id"),
		"playspace_scale_answers",
		["question_response_id"],
		unique=False,
	)

	# ── playspace_checklist_answers ──────────────────────────────────────────
	op.create_table(
		"playspace_checklist_answers",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("question_response_id", sa.UUID(), nullable=False),
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
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
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
	op.drop_table("playspace_checklist_answers")
	op.drop_table("playspace_scale_answers")
	op.drop_table("playspace_question_responses")
	op.drop_table("playspace_submission_sections")
	op.drop_table("playspace_pre_submission_answers")
	op.drop_table("playspace_submission_contexts")
	op.drop_table("playspace_submissions")

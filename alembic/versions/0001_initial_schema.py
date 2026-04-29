"""Initial schema — full current state.

Single migration that creates every table from scratch. Run against a clean
(empty) database. To reset: drop + recreate the public schema, then
``alembic -x product=<yee|playspace> upgrade head``.

Revision ID: 0001
Revises:
Create Date: 2026-04-27

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models import AccountType, AuditStatus, NotificationType

# revision identifiers, used by Alembic.
revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
	bind = op.get_bind()

	# ── PostgreSQL ENUM types ───────────────────────────────────────────────
	postgresql.ENUM(AccountType, name="shared_account_type").create(bind, checkfirst=True)
	postgresql.ENUM(AuditStatus, name="shared_audit_status").create(bind, checkfirst=True)
	postgresql.ENUM(NotificationType, name="notification_type_enum").create(bind, checkfirst=True)

	# ── accounts ────────────────────────────────────────────────────────────
	op.create_table(
		"accounts",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("name", sa.String(length=200), nullable=False),
		sa.Column("email", sa.String(length=320), nullable=False),
		sa.Column(
			"account_type",
			postgresql.ENUM(AccountType, name="shared_account_type", create_type=False),
			nullable=False,
		),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_accounts")),
	)
	op.create_index(op.f("ix_accounts_accounts_email"), "accounts", ["email"], unique=True)

	# ── instruments ─────────────────────────────────────────────────────────
	op.create_table(
		"instruments",
		sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), nullable=False),
		sa.Column("instrument_key", sa.String(length=255), nullable=False),
		sa.Column("instrument_version", sa.String(length=50), nullable=False),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
		sa.Column("content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_instruments")),
	)

	# ── users ───────────────────────────────────────────────────────────────
	op.create_table(
		"users",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("email", sa.String(length=320), nullable=False),
		sa.Column("password_hash", sa.String(length=255), nullable=False),
		sa.Column("account_id", sa.UUID(), nullable=True),
		sa.Column(
			"account_type",
			postgresql.ENUM(AccountType, name="shared_account_type", create_type=False),
			nullable=False,
		),
		sa.Column("name", sa.String(length=200), nullable=True),
		sa.Column("email_verified", sa.Boolean(), server_default="false", nullable=False),
		sa.Column("email_verification_token_hash", sa.String(length=255), nullable=True),
		sa.Column("email_verification_sent_at", sa.DateTime(timezone=True), nullable=True),
		sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
		sa.Column("failed_login_attempts", sa.Integer(), server_default="0", nullable=False),
		sa.Column("approved", sa.Boolean(), server_default="false", nullable=False),
		sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
		sa.Column("profile_completed", sa.Boolean(), server_default="false", nullable=False),
		sa.Column("profile_completed_at", sa.DateTime(timezone=True), nullable=True),
		sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.ForeignKeyConstraint(
			["account_id"], ["accounts.id"], name=op.f("fk_users_account_id_accounts"), ondelete="SET NULL"
		),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
		sa.UniqueConstraint("email", name=op.f("uq_users_email")),
	)
	op.create_index(op.f("ix_users_account_id"), "users", ["account_id"], unique=False)
	op.create_index(op.f("ix_users_users_email"), "users", ["email"], unique=True)

	# ── notifications ───────────────────────────────────────────────────────
	op.create_table(
		"notifications",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("user_id", sa.UUID(), nullable=False),
		sa.Column("message", sa.String(length=500), nullable=False),
		sa.Column(
			"notification_type",
			postgresql.ENUM(NotificationType, name="notification_type_enum", create_type=False),
			nullable=False,
		),
		sa.Column("is_read", sa.Boolean(), server_default="false", nullable=False),
		sa.Column("related_entity_type", sa.String(length=50), nullable=True),
		sa.Column("related_entity_id", sa.UUID(), nullable=True),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.ForeignKeyConstraint(
			["user_id"], ["users.id"], name=op.f("fk_notifications_user_id_users"), ondelete="CASCADE"
		),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
	)
	op.create_index("ix_notifications_user_unread", "notifications", ["user_id", "is_read"], unique=False)
	op.create_index(op.f("ix_notifications_notifications_is_read"), "notifications", ["is_read"], unique=False)
	op.create_index(op.f("ix_notifications_notifications_created_at"), "notifications", ["created_at"], unique=False)
	op.create_index(op.f("ix_notifications_notifications_user_id"), "notifications", ["user_id"], unique=False)

	# ── manager_profiles ────────────────────────────────────────────────────
	op.create_table(
		"manager_profiles",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("account_id", sa.UUID(), nullable=False),
		sa.Column("user_id", sa.UUID(), nullable=True),
		sa.Column("full_name", sa.String(length=200), nullable=False),
		sa.Column("email", sa.String(length=320), nullable=False),
		sa.Column("phone", sa.String(length=50), nullable=True),
		sa.Column("position", sa.String(length=200), nullable=True),
		sa.Column("organization", sa.String(length=200), nullable=True),
		sa.Column("is_primary", sa.Boolean(), nullable=False),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.ForeignKeyConstraint(
			["account_id"],
			["accounts.id"],
			name=op.f("fk_manager_profiles_account_id_accounts"),
			ondelete="CASCADE",
		),
		sa.ForeignKeyConstraint(
			["user_id"], ["users.id"], name=op.f("fk_manager_profiles_user_id_users"), ondelete="SET NULL"
		),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_manager_profiles")),
		sa.UniqueConstraint("user_id", name=op.f("uq_manager_profiles_user_id")),
		sa.UniqueConstraint("email", name=op.f("uq_manager_profiles_email")),
	)
	op.create_index(
		op.f("ix_manager_profiles_manager_profiles_account_id"), "manager_profiles", ["account_id"], unique=False
	)
	op.create_index(op.f("ix_manager_profiles_manager_profiles_email"), "manager_profiles", ["email"], unique=True)
	# Partial unique index: at most one primary profile per account.
	op.create_index(
		"ix_manager_profiles_account_primary_true",
		"manager_profiles",
		["account_id"],
		unique=True,
		postgresql_where=sa.text("is_primary = true"),
	)

	# ── auditor_profiles ────────────────────────────────────────────────────
	op.create_table(
		"auditor_profiles",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("account_id", sa.UUID(), nullable=False),
		sa.Column("user_id", sa.UUID(), nullable=True),
		sa.Column("auditor_code", sa.String(length=50), nullable=False),
		sa.Column("email", sa.String(length=320), nullable=True),
		sa.Column("full_name", sa.String(length=200), nullable=False),
		sa.Column("age_range", sa.String(length=80), nullable=True),
		sa.Column("gender", sa.String(length=80), nullable=True),
		sa.Column("country", sa.String(length=120), nullable=True),
		sa.Column("role", sa.String(length=120), nullable=True),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.ForeignKeyConstraint(
			["account_id"],
			["accounts.id"],
			name=op.f("fk_auditor_profiles_account_id_accounts"),
			ondelete="CASCADE",
		),
		sa.ForeignKeyConstraint(
			["user_id"], ["users.id"], name=op.f("fk_auditor_profiles_user_id_users"), ondelete="SET NULL"
		),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_auditor_profiles")),
		sa.UniqueConstraint("user_id", name=op.f("uq_auditor_profiles_user_id")),
		sa.UniqueConstraint("auditor_code", name=op.f("uq_auditor_profiles_auditor_code")),
		sa.UniqueConstraint("email", name=op.f("uq_auditor_profiles_email")),
	)
	op.create_index(
		op.f("ix_auditor_profiles_auditor_profiles_account_id"),
		"auditor_profiles",
		["account_id"],
		unique=False,
	)
	op.create_index(
		op.f("ix_auditor_profiles_auditor_profiles_auditor_code"),
		"auditor_profiles",
		["auditor_code"],
		unique=True,
	)
	op.create_index(op.f("ix_auditor_profiles_auditor_profiles_email"), "auditor_profiles", ["email"], unique=True)

	# ── auditor_invites ─────────────────────────────────────────────────────
	op.create_table(
		"auditor_invites",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("account_id", sa.UUID(), nullable=False),
		sa.Column("invited_by_user_id", sa.UUID(), nullable=False),
		sa.Column("auditor_id", sa.UUID(), nullable=True),
		sa.Column("email", sa.String(length=320), nullable=False),
		sa.Column("token_hash", sa.String(length=255), nullable=False),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
		sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
		sa.ForeignKeyConstraint(
			["account_id"],
			["accounts.id"],
			name=op.f("fk_auditor_invites_account_id_accounts"),
			ondelete="CASCADE",
		),
		sa.ForeignKeyConstraint(
			["auditor_id"],
			["auditor_profiles.id"],
			name=op.f("fk_auditor_invites_auditor_id_auditor_profiles"),
			ondelete="SET NULL",
		),
		sa.ForeignKeyConstraint(
			["invited_by_user_id"],
			["users.id"],
			name=op.f("fk_auditor_invites_invited_by_user_id_users"),
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_auditor_invites")),
		sa.UniqueConstraint("token_hash", name=op.f("uq_auditor_invites_token_hash")),
	)
	op.create_index(
		op.f("ix_auditor_invites_auditor_invites_account_id"), "auditor_invites", ["account_id"], unique=False
	)
	op.create_index(
		op.f("ix_auditor_invites_auditor_invites_auditor_id"), "auditor_invites", ["auditor_id"], unique=False
	)
	op.create_index(op.f("ix_auditor_invites_auditor_invites_email"), "auditor_invites", ["email"], unique=False)
	op.create_index(
		op.f("ix_auditor_invites_auditor_invites_invited_by_user_id"),
		"auditor_invites",
		["invited_by_user_id"],
		unique=False,
	)

	# ── manager_invites ─────────────────────────────────────────────────────
	op.create_table(
		"manager_invites",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("account_id", sa.UUID(), nullable=False),
		sa.Column("invited_by_user_id", sa.UUID(), nullable=False),
		sa.Column("accepted_by_user_id", sa.UUID(), nullable=True),
		sa.Column("email", sa.String(length=320), nullable=False),
		sa.Column("token_hash", sa.String(length=255), nullable=False),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
		sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
		sa.ForeignKeyConstraint(
			["accepted_by_user_id"],
			["users.id"],
			name=op.f("fk_manager_invites_accepted_by_user_id_users"),
			ondelete="SET NULL",
		),
		sa.ForeignKeyConstraint(
			["account_id"],
			["accounts.id"],
			name=op.f("fk_manager_invites_account_id_accounts"),
			ondelete="CASCADE",
		),
		sa.ForeignKeyConstraint(
			["invited_by_user_id"],
			["users.id"],
			name=op.f("fk_manager_invites_invited_by_user_id_users"),
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_manager_invites")),
		sa.UniqueConstraint("token_hash", name=op.f("uq_manager_invites_token_hash")),
	)
	op.create_index(
		op.f("ix_manager_invites_manager_invites_accepted_by_user_id"),
		"manager_invites",
		["accepted_by_user_id"],
		unique=False,
	)
	op.create_index(
		op.f("ix_manager_invites_manager_invites_account_id"), "manager_invites", ["account_id"], unique=False
	)
	op.create_index(op.f("ix_manager_invites_manager_invites_email"), "manager_invites", ["email"], unique=False)
	op.create_index(
		op.f("ix_manager_invites_manager_invites_invited_by_user_id"),
		"manager_invites",
		["invited_by_user_id"],
		unique=False,
	)

	# ── places ──────────────────────────────────────────────────────────────
	op.create_table(
		"places",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("name", sa.String(length=200), nullable=False),
		sa.Column("city", sa.String(length=120), nullable=True),
		sa.Column("province", sa.String(length=120), nullable=True),
		sa.Column("country", sa.String(length=120), nullable=True),
		sa.Column("postal_code", sa.String(length=32), nullable=True),
		sa.Column("address", sa.Text(), nullable=True),
		sa.Column("place_type", sa.String(length=100), nullable=True),
		sa.Column("lat", sa.Float(), nullable=True),
		sa.Column("lng", sa.Float(), nullable=True),
		sa.Column("start_date", sa.Date(), nullable=True),
		sa.Column("end_date", sa.Date(), nullable=True),
		sa.Column("est_auditors", sa.Integer(), nullable=True),
		sa.Column("auditor_description", sa.Text(), nullable=True),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_places")),
	)

	# ── projects ─────────────────────────────────────────────────────────────
	op.create_table(
		"projects",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("account_id", sa.UUID(), nullable=False),
		sa.Column("created_by_user_id", sa.UUID(), nullable=False),
		sa.Column("name", sa.String(length=200), nullable=False),
		sa.Column("overview", sa.Text(), nullable=True),
		sa.Column("place_types", postgresql.ARRAY(sa.String(length=100)), nullable=False),
		sa.Column("start_date", sa.Date(), nullable=True),
		sa.Column("end_date", sa.Date(), nullable=True),
		sa.Column("est_places", sa.Integer(), nullable=True),
		sa.Column("est_auditors", sa.Integer(), nullable=True),
		sa.Column("auditor_description", sa.Text(), nullable=True),
		sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.ForeignKeyConstraint(
			["account_id"],
			["accounts.id"],
			name=op.f("fk_projects_account_id_accounts"),
			ondelete="CASCADE",
		),
		sa.ForeignKeyConstraint(
			["created_by_user_id"],
			["users.id"],
			name=op.f("fk_projects_created_by_user_id_users"),
			ondelete="RESTRICT",
		),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_projects")),
	)
	op.create_index(op.f("ix_projects_projects_account_id"), "projects", ["account_id"], unique=False)
	op.create_index(op.f("ix_projects_projects_created_by_user_id"), "projects", ["created_by_user_id"], unique=False)

	# ── project_places ───────────────────────────────────────────────────────
	op.create_table(
		"project_places",
		sa.Column("project_id", sa.UUID(), nullable=False),
		sa.Column("place_id", sa.UUID(), nullable=False),
		sa.Column("linked_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.ForeignKeyConstraint(
			["place_id"], ["places.id"], name=op.f("fk_project_places_place_id_places"), ondelete="CASCADE"
		),
		sa.ForeignKeyConstraint(
			["project_id"],
			["projects.id"],
			name=op.f("fk_project_places_project_id_projects"),
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("project_id", "place_id", name=op.f("pk_project_places")),
	)

	# ── auditor_assignments ──────────────────────────────────────────────────
	op.create_table(
		"auditor_assignments",
		sa.Column("id", sa.UUID(), nullable=False),
		sa.Column("auditor_profile_id", sa.UUID(), nullable=False),
		sa.Column("project_id", sa.UUID(), nullable=False),
		sa.Column("place_id", sa.UUID(), nullable=False),
		sa.Column("assigned_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
		sa.ForeignKeyConstraint(
			["auditor_profile_id"],
			["auditor_profiles.id"],
			name=op.f("fk_auditor_assignments_auditor_profile_id_auditor_profiles"),
			ondelete="CASCADE",
		),
		sa.ForeignKeyConstraint(
			["place_id"], ["places.id"], name=op.f("fk_auditor_assignments_place_id_places"), ondelete="CASCADE"
		),
		sa.ForeignKeyConstraint(
			["project_id"],
			["projects.id"],
			name=op.f("fk_auditor_assignments_project_id_projects"),
			ondelete="CASCADE",
		),
		sa.ForeignKeyConstraint(
			["project_id", "place_id"],
			["project_places.project_id", "project_places.place_id"],
			name="fk_auditor_assignments_project_place_pair",
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id", name=op.f("pk_auditor_assignments")),
		sa.UniqueConstraint(
			"auditor_profile_id",
			"project_id",
			"place_id",
			name="uq_auditor_assignments_auditor_project_place",
		),
	)
	op.create_index(
		op.f("ix_auditor_assignments_auditor_assignments_auditor_profile_id"),
		"auditor_assignments",
		["auditor_profile_id"],
		unique=False,
	)
	op.create_index(
		op.f("ix_auditor_assignments_auditor_assignments_place_id"),
		"auditor_assignments",
		["place_id"],
		unique=False,
	)
	op.create_index(
		op.f("ix_auditor_assignments_auditor_assignments_project_id"),
		"auditor_assignments",
		["project_id"],
		unique=False,
	)

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
		sa.Column("section_key", sa.String(length=120), nullable=False),
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
		sa.Column("question_key", sa.String(length=120), nullable=False),
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
		sa.Column("scale_key", sa.String(length=40), nullable=False),
		sa.Column("option_key", sa.String(length=80), nullable=False),
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


def downgrade() -> None:
	op.drop_table("playspace_scale_answers")
	op.drop_table("playspace_question_responses")
	op.drop_table("playspace_submission_sections")
	op.drop_table("playspace_pre_submission_answers")
	op.drop_table("playspace_submission_contexts")
	op.drop_table("playspace_submissions")
	op.drop_table("audits")
	op.drop_table("auditor_assignments")
	op.drop_table("project_places")
	op.drop_table("projects")
	op.drop_table("places")
	op.drop_table("manager_invites")
	op.drop_table("auditor_invites")
	op.drop_table("auditor_profiles")
	op.drop_table("manager_profiles")
	op.drop_table("notifications")
	op.drop_table("users")
	op.drop_table("instruments")
	op.drop_table("accounts")

	bind = op.get_bind()
	postgresql.ENUM(name="notification_type_enum").drop(bind, checkfirst=True)
	postgresql.ENUM(name="shared_audit_status").drop(bind, checkfirst=True)
	postgresql.ENUM(name="shared_account_type").drop(bind, checkfirst=True)

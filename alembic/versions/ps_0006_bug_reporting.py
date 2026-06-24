"""Add Playspace bug-reporting and known-issues tables.

Creates two Playspace-only tables behind the internal bug-reporting workflow:

* ``known_issues`` - a curated, platform-wide library (not account-scoped) of
  known problems and workarounds shown to a reporter before they submit.
* ``bug_reports`` - user-submitted reports private to the reporter's account,
  carrying a privacy-filtered diagnostic ``context`` blob and optional Cloudinary
  screenshot URL. Entity references are nullable FKs verified at write time.

Both tables live ONLY in the Playspace database (registered in
``PLAYSPACE_ONLY_TABLE_NAMES``) and are created on the ``playspace`` branch:

    alembic -x product=playspace upgrade playspace@head

The migration is hand-written and idempotent (guards on table/type existence),
so it is safe on fresh, partially-migrated, and rerun states.

Revision ID: ps_0006
Revises: ps_0005
Create Date: 2026-06-19

"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from app.models import (
	BugReportSeverity,
	BugReportStatus,
	BugReportSurface,
	KnownIssueStatus,
)

# revision identifiers, used by Alembic.
revision = "ps_0006"
down_revision = "ps_0005"
branch_labels = None
depends_on = None


def _has_table(table_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	return table_name in inspector.get_table_names()


def upgrade() -> None:
	bind = op.get_bind()

	# ── enum types ───────────────────────────────────────────────────────────
	postgresql.ENUM(BugReportSurface, name="bug_report_surface").create(bind, checkfirst=True)
	postgresql.ENUM(BugReportSeverity, name="bug_report_severity").create(bind, checkfirst=True)
	postgresql.ENUM(BugReportStatus, name="bug_report_status").create(bind, checkfirst=True)
	postgresql.ENUM(KnownIssueStatus, name="known_issue_status").create(bind, checkfirst=True)

	# ── known_issues (created first: bug_reports references it) ───────────────
	if not _has_table("known_issues"):
		op.create_table(
			"known_issues",
			sa.Column("id", sa.UUID(), nullable=False),
			sa.Column("title", sa.String(length=200), nullable=False),
			sa.Column("symptoms", sa.Text(), nullable=False),
			sa.Column("workaround", sa.Text(), nullable=True),
			sa.Column(
				"status",
				postgresql.ENUM(KnownIssueStatus, name="known_issue_status", create_type=False),
				nullable=False,
			),
			sa.Column("tags", postgresql.ARRAY(sa.String(length=60)), nullable=False),
			sa.Column("surfaces", postgresql.ARRAY(sa.String(length=20)), nullable=False),
			sa.Column("is_published", sa.Boolean(), server_default=sa.text("false"), nullable=False),
			sa.Column("created_by_user_id", sa.UUID(), nullable=True),
			sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
			sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
			sa.ForeignKeyConstraint(
				["created_by_user_id"],
				["users.id"],
				name=op.f("fk_known_issues_created_by_user_id_users"),
				ondelete="SET NULL",
			),
			sa.PrimaryKeyConstraint("id", name=op.f("pk_known_issues")),
		)
		op.create_index(
			op.f("ix_known_issues_is_published"),
			"known_issues",
			["is_published"],
			unique=False,
		)

	# ── bug_reports ──────────────────────────────────────────────────────────
	if not _has_table("bug_reports"):
		op.create_table(
			"bug_reports",
			sa.Column("id", sa.UUID(), nullable=False),
			sa.Column("account_id", sa.UUID(), nullable=True),
			sa.Column("reporter_user_id", sa.UUID(), nullable=True),
			sa.Column("reporter_email", sa.String(length=320), nullable=True),
			sa.Column("reporter_role", sa.String(length=20), nullable=True),
			sa.Column(
				"surface",
				postgresql.ENUM(BugReportSurface, name="bug_report_surface", create_type=False),
				nullable=False,
			),
			sa.Column("title", sa.String(length=200), nullable=False),
			sa.Column("description", sa.Text(), nullable=False),
			sa.Column(
				"severity",
				postgresql.ENUM(BugReportSeverity, name="bug_report_severity", create_type=False),
				nullable=False,
			),
			sa.Column(
				"status",
				postgresql.ENUM(BugReportStatus, name="bug_report_status", create_type=False),
				nullable=False,
			),
			sa.Column("linked_known_issue_id", sa.UUID(), nullable=True),
			sa.Column("project_id", sa.UUID(), nullable=True),
			sa.Column("place_id", sa.UUID(), nullable=True),
			sa.Column("playspace_submission_id", sa.UUID(), nullable=True),
			sa.Column("context", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
			sa.Column("screenshot_url", sa.Text(), nullable=True),
			sa.Column("screenshot_public_id", sa.String(length=255), nullable=True),
			sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
			sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
			sa.ForeignKeyConstraint(
				["account_id"],
				["accounts.id"],
				name=op.f("fk_bug_reports_account_id_accounts"),
				ondelete="SET NULL",
			),
			sa.ForeignKeyConstraint(
				["reporter_user_id"],
				["users.id"],
				name=op.f("fk_bug_reports_reporter_user_id_users"),
				ondelete="SET NULL",
			),
			sa.ForeignKeyConstraint(
				["linked_known_issue_id"],
				["known_issues.id"],
				name=op.f("fk_bug_reports_linked_known_issue_id_known_issues"),
				ondelete="SET NULL",
			),
			sa.ForeignKeyConstraint(
				["project_id"],
				["projects.id"],
				name=op.f("fk_bug_reports_project_id_projects"),
				ondelete="SET NULL",
			),
			sa.ForeignKeyConstraint(
				["place_id"],
				["places.id"],
				name=op.f("fk_bug_reports_place_id_places"),
				ondelete="SET NULL",
			),
			sa.ForeignKeyConstraint(
				["playspace_submission_id"],
				["playspace_submissions.id"],
				name=op.f("fk_bug_reports_playspace_submission_id_playspace_submissions"),
				ondelete="SET NULL",
			),
			sa.PrimaryKeyConstraint("id", name=op.f("pk_bug_reports")),
		)
		op.create_index(op.f("ix_bug_reports_account_id"), "bug_reports", ["account_id"], unique=False)
		op.create_index(op.f("ix_bug_reports_reporter_user_id"), "bug_reports", ["reporter_user_id"], unique=False)
		op.create_index(op.f("ix_bug_reports_status"), "bug_reports", ["status"], unique=False)
		op.create_index(op.f("ix_bug_reports_created_at"), "bug_reports", ["created_at"], unique=False)


def downgrade() -> None:
	bind = op.get_bind()

	if _has_table("bug_reports"):
		op.drop_index(op.f("ix_bug_reports_created_at"), table_name="bug_reports")
		op.drop_index(op.f("ix_bug_reports_status"), table_name="bug_reports")
		op.drop_index(op.f("ix_bug_reports_reporter_user_id"), table_name="bug_reports")
		op.drop_index(op.f("ix_bug_reports_account_id"), table_name="bug_reports")
		op.drop_table("bug_reports")

	if _has_table("known_issues"):
		op.drop_index(op.f("ix_known_issues_is_published"), table_name="known_issues")
		op.drop_table("known_issues")

	postgresql.ENUM(name="bug_report_status").drop(bind, checkfirst=True)
	postgresql.ENUM(name="bug_report_severity").drop(bind, checkfirst=True)
	postgresql.ENUM(name="bug_report_surface").drop(bind, checkfirst=True)
	postgresql.ENUM(name="known_issue_status").drop(bind, checkfirst=True)

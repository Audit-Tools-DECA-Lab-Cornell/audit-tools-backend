"""Add project-place compatibility structures for YEE.

Revision ID: 20260408_0008
Revises: 20260408_0007
Create Date: 2026-04-08 00:08:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

# revision identifiers, used by Alembic.
revision: str = "20260408_0008"
down_revision: str | None = "20260408_0007"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _is_target_product(product_key: str) -> bool:
	x_args = context.get_x_argument(as_dictionary=True)
	return x_args.get("product", "yee").strip().lower() == product_key


def _has_table(table_name: str) -> bool:
	if context.is_offline_mode():
		return False
	bind = op.get_bind()
	inspector = sa.inspect(bind)
	return inspector.has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
	if context.is_offline_mode():
		return False
	bind = op.get_bind()
	inspector = sa.inspect(bind)
	return any(column["name"] == column_name for column in inspector.get_columns(table_name))


def _has_constraint(table_name: str, constraint_name: str) -> bool:
	if context.is_offline_mode():
		return False
	bind = op.get_bind()
	inspector = sa.inspect(bind)
	for fk in inspector.get_foreign_keys(table_name):
		if fk.get("name") == constraint_name:
			return True
	for unique in inspector.get_unique_constraints(table_name):
		if unique.get("name") == constraint_name:
			return True
	return False


def upgrade() -> None:
	if not _is_target_product("yee"):
		return
	if context.is_offline_mode():
		return

	if not _has_table("project_places"):
		op.create_table(
			"project_places",
			sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
			sa.Column("place_id", postgresql.UUID(as_uuid=True), nullable=False),
			sa.Column(
				"linked_at",
				sa.DateTime(timezone=True),
				server_default=sa.text("now()"),
				nullable=False,
			),
			sa.ForeignKeyConstraint(
				["project_id"],
				["projects.id"],
				name="fk_project_places_project_id_projects",
				ondelete="CASCADE",
			),
			sa.ForeignKeyConstraint(
				["place_id"],
				["places.id"],
				name="fk_project_places_place_id_places",
				ondelete="CASCADE",
			),
			sa.PrimaryKeyConstraint("project_id", "place_id", name="pk_project_places"),
		)

	if _has_column("places", "project_id"):
		op.execute(
			"""
            INSERT INTO project_places (project_id, place_id)
            SELECT DISTINCT project_id, id
            FROM places
            WHERE project_id IS NOT NULL
            ON CONFLICT DO NOTHING
            """
		)
		op.execute("ALTER TABLE places ALTER COLUMN project_id DROP NOT NULL")

	if not _has_column("audits", "project_id"):
		op.add_column(
			"audits",
			sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
		)

	if _has_column("audits", "project_id") and _has_column("places", "project_id"):
		op.execute(
			"""
            UPDATE audits AS a
            SET project_id = p.project_id
            FROM places AS p
            WHERE a.place_id = p.id
              AND a.project_id IS NULL
            """
		)

	if _has_column("audits", "project_id"):
		op.execute("ALTER TABLE audits ALTER COLUMN project_id SET NOT NULL")

	if not _has_constraint("audits", "fk_audits_project_id_projects"):
		op.create_foreign_key(
			"fk_audits_project_id_projects",
			"audits",
			"projects",
			["project_id"],
			["id"],
			ondelete="CASCADE",
		)

	if not _has_constraint("audits", "fk_audits_project_place_pair"):
		op.create_foreign_key(
			"fk_audits_project_place_pair",
			"audits",
			"project_places",
			["project_id", "place_id"],
			["project_id", "place_id"],
			ondelete="CASCADE",
		)

	op.execute("ALTER TABLE auditor_assignments DROP CONSTRAINT IF EXISTS ck_auditor_assignments_single_scope")
	op.execute(
		"ALTER TABLE auditor_assignments DROP CONSTRAINT IF EXISTS ck_auditor_assignments_ck_auditor_assignments_single_scope"
	)
	op.execute("ALTER TABLE auditor_assignments DROP CONSTRAINT IF EXISTS uq_auditor_assignments_auditor_project")
	op.execute("ALTER TABLE auditor_assignments DROP CONSTRAINT IF EXISTS uq_auditor_assignments_auditor_place")
	op.execute(
		"""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_auditor_assignments_auditor_project_scope
        ON auditor_assignments (auditor_profile_id, project_id)
        WHERE place_id IS NULL
        """
	)
	op.execute(
		"""
        CREATE UNIQUE INDEX IF NOT EXISTS uq_auditor_assignments_auditor_project_place
        ON auditor_assignments (auditor_profile_id, project_id, place_id)
        WHERE place_id IS NOT NULL
        """
	)

	if not _has_constraint("auditor_assignments", "fk_auditor_assignments_project_place_pair"):
		op.create_foreign_key(
			"fk_auditor_assignments_project_place_pair",
			"auditor_assignments",
			"project_places",
			["project_id", "place_id"],
			["project_id", "place_id"],
			ondelete="CASCADE",
		)


def downgrade() -> None:
	if not _is_target_product("yee"):
		return
	if context.is_offline_mode():
		return

	if _has_constraint("auditor_assignments", "fk_auditor_assignments_project_place_pair"):
		op.drop_constraint(
			"fk_auditor_assignments_project_place_pair",
			"auditor_assignments",
			type_="foreignkey",
		)
	if _has_constraint("audits", "fk_audits_project_place_pair"):
		op.drop_constraint("fk_audits_project_place_pair", "audits", type_="foreignkey")
	if _has_constraint("audits", "fk_audits_project_id_projects"):
		op.drop_constraint("fk_audits_project_id_projects", "audits", type_="foreignkey")
	if _has_column("audits", "project_id"):
		op.drop_column("audits", "project_id")
	if _has_table("project_places"):
		op.drop_table("project_places")

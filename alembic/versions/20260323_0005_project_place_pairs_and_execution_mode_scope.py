"""Refactor project/place pairs and remove assignment role storage.

Revision ID: 20260323_0005
Revises: 20260320_0004
Create Date: 2026-03-23 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20260323_0005"
down_revision: str | None = "20260320_0004"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None

NOW_SQL = sa.text("now()")


def upgrade() -> None:
    """Move places/projects to many-to-many and audits/assignments to project-place pairs."""

    op.create_table(
        "project_places",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("place_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "linked_at",
            sa.DateTime(timezone=True),
            server_default=NOW_SQL,
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
    op.create_index(
        "ix_project_places_place_id",
        "project_places",
        ["place_id"],
        unique=False,
    )

    op.execute(
        sa.text(
            """
            INSERT INTO project_places (project_id, place_id, linked_at)
            SELECT project_id, id, COALESCE(created_at, now())
            FROM places
            WHERE project_id IS NOT NULL
            ON CONFLICT (project_id, place_id) DO NOTHING
            """
        )
    )

    op.add_column(
        "audits",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_audits_project_id", "audits", ["project_id"], unique=False)
    op.create_foreign_key(
        "fk_audits_project_id_projects",
        "audits",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.execute(
        sa.text(
            """
            UPDATE audits
            SET project_id = places.project_id
            FROM places
            WHERE audits.place_id = places.id
              AND audits.project_id IS NULL
            """
        )
    )
    _delete_duplicate_audits()
    op.alter_column(
        "audits",
        "project_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_audits_project_place_pair",
        "audits",
        "project_places",
        ["project_id", "place_id"],
        ["project_id", "place_id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_audits_project_place_auditor",
        "audits",
        ["project_id", "place_id", "auditor_profile_id"],
    )

    op.drop_constraint(
        "ck_auditor_assignments_single_scope",
        "auditor_assignments",
        type_="check",
    )
    op.drop_constraint(
        "uq_auditor_assignments_auditor_project",
        "auditor_assignments",
        type_="unique",
    )
    op.drop_constraint(
        "uq_auditor_assignments_auditor_place",
        "auditor_assignments",
        type_="unique",
    )
    # Remove legacy single-scope constraints before backfilling project ids for place rows.
    op.execute(
        sa.text(
            """
            UPDATE auditor_assignments
            SET project_id = places.project_id
            FROM places
            WHERE auditor_assignments.place_id = places.id
              AND auditor_assignments.project_id IS NULL
            """
        )
    )
    op.alter_column(
        "auditor_assignments",
        "project_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )
    op.create_foreign_key(
        "fk_auditor_assignments_project_place_pair",
        "auditor_assignments",
        "project_places",
        ["project_id", "place_id"],
        ["project_id", "place_id"],
        ondelete="CASCADE",
    )
    op.create_index(
        "uq_auditor_assignments_auditor_project_scope",
        "auditor_assignments",
        ["auditor_profile_id", "project_id"],
        unique=True,
        postgresql_where=sa.text("place_id IS NULL"),
    )
    op.create_index(
        "uq_auditor_assignments_auditor_project_place",
        "auditor_assignments",
        ["auditor_profile_id", "project_id", "place_id"],
        unique=True,
        postgresql_where=sa.text("place_id IS NOT NULL"),
    )
    op.drop_column("auditor_assignments", "audit_roles")

    op.drop_constraint(
        "fk_places_project_id_projects",
        "places",
        type_="foreignkey",
    )
    op.drop_index("ix_places_project_id", table_name="places")
    op.drop_column("places", "project_id")


def downgrade() -> None:
    """Collapse project/place pairs back into one project per place."""

    op.add_column(
        "places",
        sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index("ix_places_project_id", "places", ["project_id"], unique=False)
    op.create_foreign_key(
        "fk_places_project_id_projects",
        "places",
        "projects",
        ["project_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.execute(
        sa.text(
            """
            WITH ranked_project_links AS (
                SELECT
                    place_id,
                    project_id,
                    ROW_NUMBER() OVER (
                        PARTITION BY place_id
                        ORDER BY linked_at ASC, project_id ASC
                    ) AS row_number
                FROM project_places
            )
            UPDATE places
            SET project_id = ranked_project_links.project_id
            FROM ranked_project_links
            WHERE places.id = ranked_project_links.place_id
              AND ranked_project_links.row_number = 1
            """
        )
    )
    op.alter_column(
        "places",
        "project_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=False,
    )

    op.add_column(
        "auditor_assignments",
        sa.Column(
            "audit_roles",
            postgresql.ARRAY(sa.String(length=40)),
            nullable=False,
            server_default=sa.text("ARRAY['auditor']::varchar[]"),
        ),
    )
    op.drop_index(
        "uq_auditor_assignments_auditor_project_place",
        table_name="auditor_assignments",
    )
    op.drop_index(
        "uq_auditor_assignments_auditor_project_scope",
        table_name="auditor_assignments",
    )
    op.drop_constraint(
        "fk_auditor_assignments_project_place_pair",
        "auditor_assignments",
        type_="foreignkey",
    )
    op.alter_column(
        "auditor_assignments",
        "project_id",
        existing_type=postgresql.UUID(as_uuid=True),
        nullable=True,
    )
    op.execute(
        sa.text(
            """
            UPDATE auditor_assignments
            SET project_id = NULL
            WHERE place_id IS NOT NULL
            """
        )
    )
    _delete_duplicate_place_assignments_for_downgrade()
    op.create_unique_constraint(
        "uq_auditor_assignments_auditor_project",
        "auditor_assignments",
        ["auditor_profile_id", "project_id"],
    )
    op.create_unique_constraint(
        "uq_auditor_assignments_auditor_place",
        "auditor_assignments",
        ["auditor_profile_id", "place_id"],
    )
    op.create_check_constraint(
        "ck_auditor_assignments_single_scope",
        "auditor_assignments",
        "(project_id IS NOT NULL) <> (place_id IS NOT NULL)",
    )
    op.alter_column("auditor_assignments", "audit_roles", server_default=None)

    op.drop_constraint(
        "uq_audits_project_place_auditor",
        "audits",
        type_="unique",
    )
    op.drop_constraint(
        "fk_audits_project_place_pair",
        "audits",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_audits_project_id_projects",
        "audits",
        type_="foreignkey",
    )
    op.drop_index("ix_audits_project_id", table_name="audits")
    op.drop_column("audits", "project_id")

    op.drop_index("ix_project_places_place_id", table_name="project_places")
    op.drop_table("project_places")


def _delete_duplicate_audits() -> None:
    """Keep only the latest audit per project/place/auditor triple before adding uniqueness."""

    op.execute(
        sa.text(
            """
            WITH ranked_audits AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY project_id, place_id, auditor_profile_id
                        ORDER BY submitted_at DESC NULLS LAST, started_at DESC, created_at DESC, id DESC
                    ) AS row_number
                FROM audits
                WHERE project_id IS NOT NULL
            )
            DELETE FROM audits
            WHERE id IN (
                SELECT id
                FROM ranked_audits
                WHERE row_number > 1
            )
            """
        )
    )


def _delete_duplicate_place_assignments_for_downgrade() -> None:
    """Collapse multiple project-place rows back to one place-scoped row per auditor/place pair."""

    op.execute(
        sa.text(
            """
            WITH ranked_assignments AS (
                SELECT
                    id,
                    ROW_NUMBER() OVER (
                        PARTITION BY auditor_profile_id, place_id
                        ORDER BY assigned_at DESC, id DESC
                    ) AS row_number
                FROM auditor_assignments
                WHERE place_id IS NOT NULL
            )
            DELETE FROM auditor_assignments
            WHERE id IN (
                SELECT id
                FROM ranked_assignments
                WHERE row_number > 1
            )
            """
        )
    )

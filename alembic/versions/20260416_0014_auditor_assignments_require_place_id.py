"""Require place_id on auditor_assignments; single triple unique constraint.

Revision ID: 20260416_0014
Revises: 20260415_0013
Create Date: 2026-04-16 12:00:00

Project-wide assignment rows (place_id IS NULL) are removed. Partial unique
indexes from earlier migrations are dropped and replaced with one
UniqueConstraint on (auditor_profile_id, project_id, place_id), matching
``app.models.AuditorAssignment``.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "20260416_0014"
down_revision: str | None = "20260415_0013"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def _inspector() -> sa.Inspector:
    if context.is_offline_mode():
        raise RuntimeError("Schema inspection is unavailable in offline migration mode.")
    return sa.inspect(op.get_bind())


def _has_table(table_name: str) -> bool:
    return _inspector().has_table(table_name)


def upgrade() -> None:
    if context.is_offline_mode():
        return

    if not _has_table("auditor_assignments"):
        return

    op.execute(sa.text("DELETE FROM auditor_assignments WHERE place_id IS NULL"))

    op.execute(sa.text("DROP INDEX IF EXISTS uq_auditor_assignments_auditor_project_scope"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_auditor_assignments_auditor_project_place"))

    op.execute(
        sa.text(
            'ALTER TABLE auditor_assignments DROP CONSTRAINT IF EXISTS "uq_auditor_assignments_auditor_project"'
        )
    )
    op.execute(
        sa.text(
            'ALTER TABLE auditor_assignments DROP CONSTRAINT IF EXISTS "uq_auditor_assignments_auditor_place"'
        )
    )

    op.execute(sa.text("ALTER TABLE auditor_assignments ALTER COLUMN place_id SET NOT NULL"))

    op.create_unique_constraint(
        "uq_auditor_assignments_auditor_project_place",
        "auditor_assignments",
        ["auditor_profile_id", "project_id", "place_id"],
    )


def downgrade() -> None:
    if context.is_offline_mode():
        return

    if not _has_table("auditor_assignments"):
        return

    op.drop_constraint(
        "uq_auditor_assignments_auditor_project_place",
        "auditor_assignments",
        type_="unique",
    )

    op.alter_column("auditor_assignments", "place_id", existing_nullable=False, nullable=True)

    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_auditor_assignments_auditor_project_scope
            ON auditor_assignments (auditor_profile_id, project_id)
            WHERE place_id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS uq_auditor_assignments_auditor_project_place
            ON auditor_assignments (auditor_profile_id, project_id, place_id)
            WHERE place_id IS NOT NULL
            """
        )
    )

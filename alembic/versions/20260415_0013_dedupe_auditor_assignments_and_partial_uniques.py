"""Remove duplicate auditor_assignments and enforce partial unique indexes.

Revision ID: 20260415_0013
Revises: 20260414_0012
Create Date: 2026-04-15 12:00:00

The 20260323_0005 migration installs partial unique indexes on
``auditor_assignments`` (see ``app.models.AuditorAssignment``), but its
``upgrade()`` exits early when ``project_places`` already exists.  Databases
created or restored in that order never received those indexes, so duplicate
(project, place, auditor) rows could accumulate despite application-layer
checks.  This migration deduplicates rows (keeping the smallest ``id`` per
scope) and creates the same partial unique indexes used by the ORM.

Legacy unique constraints from the pre–project-place model may still be
present; they are dropped when present so they do not fight the partial
indexes.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "20260415_0013"
down_revision: str | None = "20260414_0012"
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

    # 1) Drop duplicates for place-scoped rows (place_id IS NOT NULL).
    op.execute(
        sa.text(
            """
            DELETE FROM auditor_assignments AS a
            WHERE a.place_id IS NOT NULL
              AND EXISTS (
                  SELECT 1
                  FROM auditor_assignments AS b
                  WHERE b.auditor_profile_id = a.auditor_profile_id
                    AND b.project_id = a.project_id
                    AND b.place_id = a.place_id
                    AND b.id < a.id
              )
            """
        )
    )

    # 2) Drop duplicates for project-scoped rows (place_id IS NULL).
    op.execute(
        sa.text(
            """
            DELETE FROM auditor_assignments AS a
            WHERE a.place_id IS NULL
              AND EXISTS (
                  SELECT 1
                  FROM auditor_assignments AS b
                  WHERE b.auditor_profile_id = a.auditor_profile_id
                    AND b.project_id = a.project_id
                    AND b.place_id IS NULL
                    AND b.id < a.id
              )
            """
        )
    )

    # 3) Remove legacy unique constraints from the XOR project/place model if
    # they are still attached (skipped migrations may have left them behind).
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

    # 4) Partial unique indexes (must match app.models.AuditorAssignment).
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


def downgrade() -> None:
    """One-way data repair; reversing could reintroduce duplicates."""
    raise NotImplementedError("This corrective migration is intentionally one-way.")

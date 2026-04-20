"""Backfill auditor invite ownership for legacy manager-scoped dashboards.

Revision ID: 20260420_0014
Revises: 20260420_0013
Create Date: 2026-04-20 14:05:00.000000
"""

from __future__ import annotations

from alembic import op


revision = "20260420_0014"
down_revision = "20260420_0013"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO auditor_invites (
            id,
            account_id,
            invited_by_user_id,
            auditor_id,
            email,
            token_hash,
            created_at,
            expires_at,
            accepted_at
        )
        SELECT
            gen_random_uuid(),
            auditors.account_id,
            owner_user.id,
            auditors.id,
            COALESCE(auditors.email, users.email, CONCAT('backfill+', auditors.id::text, '@local.invalid')),
            CONCAT('backfill-invite-', auditors.id::text),
            COALESCE(auditors.created_at, NOW()),
            COALESCE(auditors.created_at, NOW()) + INTERVAL '365 days',
            COALESCE(auditors.created_at, NOW())
        FROM auditor_profiles AS auditors
        LEFT JOIN users ON users.id = auditors.user_id
        JOIN LATERAL (
            SELECT manager_users.id
            FROM users AS manager_users
            WHERE manager_users.account_id = auditors.account_id
              AND manager_users.account_type = 'MANAGER'
            ORDER BY manager_users.created_at ASC, manager_users.id ASC
            LIMIT 1
        ) AS owner_user ON TRUE
        WHERE auditors.account_id IS NOT NULL
          AND NOT EXISTS (
              SELECT 1
              FROM auditor_invites
              WHERE auditor_invites.auditor_id = auditors.id
          )
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM auditor_invites
        WHERE token_hash LIKE 'backfill-invite-%'
        """
    )

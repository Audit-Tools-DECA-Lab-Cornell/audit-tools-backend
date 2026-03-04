"""rename user role

Revision ID: 98ba2fff7639
Revises: d3c57a1c4711
Create Date: 2026-03-04 18:40:56.479216

"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision = "98ba2fff7639"
down_revision = "d3c57a1c4711"
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Rename the user role column and enum type to match the domain language.

    - users.account_type -> users.role
    - PostgreSQL type `account_type` -> `user_role`
    """

    op.execute(sa.text("ALTER TYPE account_type RENAME TO user_role"))
    op.alter_column("users", "account_type", new_column_name="role")


def downgrade() -> None:
    """
    Revert the user role renames.

    - users.role -> users.account_type
    - PostgreSQL type `user_role` -> `account_type`
    """

    op.alter_column("users", "role", new_column_name="account_type")
    op.execute(sa.text("ALTER TYPE user_role RENAME TO account_type"))

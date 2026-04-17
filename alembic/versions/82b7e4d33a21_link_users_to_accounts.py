"""link users to accounts

Revision ID: 82b7e4d33a21
Revises: 1f2a6c9d4b10
Create Date: 2026-03-26 13:05:00
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa

from alembic import context, op

# revision identifiers, used by Alembic.
revision = "82b7e4d33a21"
down_revision = "1f2a6c9d4b10"
branch_labels = None
depends_on = None


users_table = sa.table(
	"users",
	sa.column("id", sa.String),
	sa.column("email", sa.String),
	sa.column("name", sa.String),
	sa.column("account_type", sa.String),
	sa.column("account_id", sa.String),
)

accounts_table = sa.table(
	"accounts",
	sa.column("id", sa.String),
	sa.column("name", sa.String),
)


def _is_target_product(product_key: str) -> bool:
	x_args = context.get_x_argument(as_dictionary=True)
	return x_args.get("product", "yee").strip().lower() == product_key


def upgrade() -> None:
	if not _is_target_product("yee"):
		return
	if context.is_offline_mode():
		return
	op.add_column("users", sa.Column("account_id", sa.UUID(), nullable=True))
	op.create_index(op.f("ix_users_account_id"), "users", ["account_id"], unique=False)
	op.create_foreign_key(
		op.f("fk_users_account_id_accounts"),
		"users",
		"accounts",
		["account_id"],
		["id"],
		ondelete="SET NULL",
	)

	bind = op.get_bind()
	rows = bind.execute(
		sa.select(
			users_table.c.id,
			users_table.c.email,
			users_table.c.name,
		).where(
			sa.cast(users_table.c.account_type, sa.String) == "MANAGER",
			users_table.c.account_id.is_(None),
		)
	).fetchall()

	for row in rows:
		account_id = uuid.uuid4()
		display_name = (row.name or "").strip()
		account_name = f"{display_name}'s Workspace" if display_name else f"{row.email.split('@', 1)[0]}'s Workspace"
		bind.execute(accounts_table.insert().values(id=account_id, name=account_name))
		bind.execute(users_table.update().where(users_table.c.id == row.id).values(account_id=account_id))


def downgrade() -> None:
	if not _is_target_product("yee"):
		return
	if context.is_offline_mode():
		return
	op.drop_constraint(op.f("fk_users_account_id_accounts"), "users", type_="foreignkey")
	op.drop_index(op.f("ix_users_account_id"), table_name="users")
	op.drop_column("users", "account_id")

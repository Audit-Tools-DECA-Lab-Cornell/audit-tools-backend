"""Restore Playspace auth fields and backfill user sessions.

Revision ID: 20260408_0010
Revises: 20260408_0009
Create Date: 2026-04-08 20:10:00
"""

from __future__ import annotations

import uuid

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

# revision identifiers, used by Alembic.
revision = "20260408_0010"
down_revision = "20260408_0009"
branch_labels = None
depends_on = None

# Matches PostgreSQL enum ``shared_account_type`` (see shared core migrations).
_SHARED_ACCOUNT_TYPE = postgresql.ENUM(
	"ADMIN",
	"MANAGER",
	"AUDITOR",
	name="shared_account_type",
	create_type=False,
)

users_table = sa.table(
	"users",
	sa.column("id", postgresql.UUID(as_uuid=True)),
	sa.column("email", sa.String(length=320)),
	sa.column("password_hash", sa.String(length=255)),
	sa.column("account_id", postgresql.UUID(as_uuid=True)),
	sa.column("account_type", _SHARED_ACCOUNT_TYPE),
	sa.column("name", sa.String(length=255)),
	sa.column("email_verified", sa.Boolean()),
	sa.column("email_verified_at", sa.DateTime(timezone=True)),
	sa.column("failed_login_attempts", sa.Integer()),
	sa.column("approved", sa.Boolean()),
	sa.column("approved_at", sa.DateTime(timezone=True)),
	sa.column("profile_completed", sa.Boolean()),
	sa.column("profile_completed_at", sa.DateTime(timezone=True)),
	sa.column("last_login_at", sa.DateTime(timezone=True)),
	sa.column("created_at", sa.DateTime(timezone=True)),
)

accounts_table = sa.table(
	"accounts",
	sa.column("id", postgresql.UUID(as_uuid=True)),
	sa.column("name", sa.String(length=255)),
	sa.column("email", sa.String(length=320)),
	sa.column("password_hash", sa.String(length=255)),
	sa.column("account_type", _SHARED_ACCOUNT_TYPE),
	sa.column("created_at", sa.DateTime(timezone=True)),
)

auditor_profiles_table = sa.table(
	"auditor_profiles",
	sa.column("id", postgresql.UUID(as_uuid=True)),
	sa.column("account_id", postgresql.UUID(as_uuid=True)),
	sa.column("user_id", postgresql.UUID(as_uuid=True)),
	sa.column("full_name", sa.String(length=255)),
	sa.column("created_at", sa.DateTime(timezone=True)),
)


def _inspector() -> sa.Inspector:
	if context.is_offline_mode():
		raise RuntimeError("Schema inspection is unavailable in offline migration mode.")
	return sa.inspect(op.get_bind())


def _is_target_product(product_key: str) -> bool:
	x_args = context.get_x_argument(as_dictionary=True)
	return x_args.get("product", "yee").strip().lower() == product_key


def _has_table(table_name: str) -> bool:
	return _inspector().has_table(table_name)


def _has_column(table_name: str, column_name: str) -> bool:
	if not _has_table(table_name):
		return False
	return column_name in {column["name"] for column in _inspector().get_columns(table_name)}


def _has_index(table_name: str, index_name: str) -> bool:
	if not _has_table(table_name):
		return False
	return any(index["name"] == index_name for index in _inspector().get_indexes(table_name))


def _ensure_users_auth_columns() -> None:
	if not _has_table("users"):
		return

	if not _has_column("users", "account_id"):
		op.add_column(
			"users",
			sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=True),
		)
		op.create_foreign_key(
			"fk_users_account_id_accounts",
			"users",
			"accounts",
			["account_id"],
			["id"],
			ondelete="SET NULL",
		)
		op.create_index("ix_users_account_id", "users", ["account_id"], unique=False)

	for column_name, column in [
		(
			"email_verified",
			sa.Column(
				"email_verified",
				sa.Boolean(),
				nullable=False,
				server_default=sa.text("false"),
			),
		),
		(
			"email_verification_token_hash",
			sa.Column("email_verification_token_hash", sa.String(length=255), nullable=True),
		),
		(
			"email_verification_sent_at",
			sa.Column("email_verification_sent_at", sa.DateTime(timezone=True), nullable=True),
		),
		(
			"email_verified_at",
			sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
		),
		(
			"failed_login_attempts",
			sa.Column(
				"failed_login_attempts",
				sa.Integer(),
				nullable=False,
				server_default=sa.text("0"),
			),
		),
		(
			"approved",
			sa.Column(
				"approved",
				sa.Boolean(),
				nullable=False,
				server_default=sa.text("false"),
			),
		),
		(
			"approved_at",
			sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
		),
		(
			"profile_completed",
			sa.Column(
				"profile_completed",
				sa.Boolean(),
				nullable=False,
				server_default=sa.text("false"),
			),
		),
		(
			"profile_completed_at",
			sa.Column("profile_completed_at", sa.DateTime(timezone=True), nullable=True),
		),
		(
			"last_login_at",
			sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
		),
	]:
		if not _has_column("users", column_name):
			op.add_column("users", column)


def _ensure_auditor_profile_user_link() -> None:
	if not _has_table("auditor_profiles"):
		return

	if not _has_column("auditor_profiles", "user_id"):
		op.add_column(
			"auditor_profiles",
			sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
		)
		op.create_foreign_key(
			"fk_auditor_profiles_user_id_users",
			"auditor_profiles",
			"users",
			["user_id"],
			["id"],
			ondelete="SET NULL",
		)
	if not _has_index("auditor_profiles", "ix_auditor_profiles_user_id"):
		op.create_index("ix_auditor_profiles_user_id", "auditor_profiles", ["user_id"], unique=True)


def _backfill_users_from_accounts() -> None:
	bind = op.get_bind()
	account_rows = bind.execute(
		sa.select(
			accounts_table.c.id,
			accounts_table.c.name,
			accounts_table.c.email,
			accounts_table.c.password_hash,
			accounts_table.c.account_type,
			accounts_table.c.created_at,
		).order_by(accounts_table.c.created_at.asc(), accounts_table.c.id.asc())
	).mappings()

	for row in account_rows:
		display_name = row["name"] or row["email"].split("@", 1)[0]
		existing_user = (
			bind.execute(
				sa.select(
					users_table.c.id,
					users_table.c.account_id,
					users_table.c.name,
				)
				.where((users_table.c.account_id == row["id"]) | (users_table.c.email == row["email"]))
				.limit(1)
			)
			.mappings()
			.first()
		)

		if existing_user is None:
			bind.execute(
				users_table.insert().values(
					id=uuid.uuid4(),
					email=row["email"],
					password_hash=row["password_hash"] or f"playspace-unusable::{uuid.uuid4().hex}",
					account_id=row["id"],
					account_type=row["account_type"],
					name=display_name,
					email_verified=True,
					email_verified_at=row["created_at"],
					failed_login_attempts=0,
					approved=True,
					approved_at=row["created_at"],
					profile_completed=bool(display_name.strip()),
					profile_completed_at=(row["created_at"] if display_name.strip() else None),
					last_login_at=None,
					created_at=row["created_at"],
				)
			)
			continue

		updates: dict[str, object] = {}
		if existing_user["account_id"] is None:
			updates["account_id"] = row["id"]
		existing_name = existing_user["name"]
		if existing_name is None or not existing_name.strip():
			updates["name"] = display_name
		if updates:
			bind.execute(users_table.update().where(users_table.c.id == existing_user["id"]).values(**updates))


def _activate_playspace_users() -> None:
	op.execute(
		"""
        UPDATE users
        SET
            email_verified = true,
            email_verified_at = COALESCE(email_verified_at, created_at, NOW()),
            failed_login_attempts = COALESCE(failed_login_attempts, 0),
            approved = true,
            approved_at = COALESCE(approved_at, created_at, NOW()),
            profile_completed = CASE
                WHEN NULLIF(BTRIM(COALESCE(name, '')), '') IS NOT NULL THEN true
                ELSE COALESCE(profile_completed, false)
            END,
            profile_completed_at = CASE
                WHEN NULLIF(BTRIM(COALESCE(name, '')), '') IS NOT NULL
                    THEN COALESCE(profile_completed_at, created_at, NOW())
                ELSE profile_completed_at
            END
        WHERE account_id IS NOT NULL
        """
	)


def _link_auditor_profiles_to_users() -> None:
	bind = op.get_bind()
	linked_user_ids = set(
		bind.execute(
			sa.select(auditor_profiles_table.c.user_id).where(auditor_profiles_table.c.user_id.is_not(None))
		).scalars()
	)
	claimed_account_ids: set[uuid.UUID] = set()
	profile_rows = bind.execute(
		sa.select(
			auditor_profiles_table.c.id,
			auditor_profiles_table.c.account_id,
			auditor_profiles_table.c.user_id,
			auditor_profiles_table.c.created_at,
		).order_by(
			auditor_profiles_table.c.created_at.asc(),
			auditor_profiles_table.c.id.asc(),
		)
	).mappings()

	for row in profile_rows:
		if row["user_id"] is not None or row["account_id"] in claimed_account_ids:
			continue

		user_row = (
			bind.execute(
				sa.select(users_table.c.id)
				.where(users_table.c.account_id == row["account_id"])
				.order_by(users_table.c.created_at.asc(), users_table.c.id.asc())
				.limit(1)
			)
			.mappings()
			.first()
		)
		if user_row is None:
			continue

		user_id = user_row["id"]
		if user_id in linked_user_ids:
			continue

		bind.execute(
			auditor_profiles_table.update().where(auditor_profiles_table.c.id == row["id"]).values(user_id=user_id)
		)
		linked_user_ids.add(user_id)
		claimed_account_ids.add(row["account_id"])


def upgrade() -> None:
	if not _is_target_product("playspace"):
		return
	if context.is_offline_mode():
		return

	_ensure_users_auth_columns()
	_ensure_auditor_profile_user_link()
	_backfill_users_from_accounts()
	_activate_playspace_users()
	_link_auditor_profiles_to_users()


def downgrade() -> None:
	if not _is_target_product("playspace"):
		return
	if context.is_offline_mode():
		return
	raise NotImplementedError("This corrective migration is intentionally one-way.")

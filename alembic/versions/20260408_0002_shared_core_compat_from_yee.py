"""Add a compatibility upgrade path from the legacy YEE schema to the merged shared-core shape.

Revision ID: 20260408_0002
Revises: f2d41c7aa991
Create Date: 2026-04-08 12:30:00
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import context, op

# revision identifiers, used by Alembic.
revision = "20260408_0002"
down_revision = "f2d41c7aa991"
branch_labels = None
depends_on = None


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


def _create_shared_enums() -> None:
	op.execute(
		"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'shared_account_type') THEN
                CREATE TYPE shared_account_type AS ENUM ('ADMIN', 'MANAGER', 'AUDITOR');
            END IF;
        END
        $$;
        """
	)
	op.execute(
		"""
        DO $$
        BEGIN
            IF NOT EXISTS (SELECT 1 FROM pg_type WHERE typname = 'shared_audit_status') THEN
                CREATE TYPE shared_audit_status AS ENUM ('IN_PROGRESS', 'PAUSED', 'SUBMITTED');
            END IF;
        END
        $$;
        """
	)


def _upgrade_users_table() -> None:
	if not _has_table("users"):
		return

	if not _has_column("users", "created_at"):
		op.add_column(
			"users",
			sa.Column(
				"created_at",
				sa.DateTime(timezone=True),
				server_default=sa.text("now()"),
				nullable=False,
			),
		)

	op.execute(
		"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'users'
                  AND column_name = 'account_type'
                  AND udt_name = 'account_type'
            ) THEN
                ALTER TABLE users
                ALTER COLUMN account_type
                TYPE shared_account_type
                USING account_type::text::shared_account_type;
            END IF;
        END
        $$;
        """
	)


def _upgrade_accounts_table() -> None:
	if not _has_table("accounts"):
		return

	if not _has_column("accounts", "email"):
		op.add_column("accounts", sa.Column("email", sa.String(length=320), nullable=True))
	if not _has_column("accounts", "password_hash"):
		op.add_column("accounts", sa.Column("password_hash", sa.String(length=255), nullable=True))
	if not _has_column("accounts", "account_type"):
		op.add_column(
			"accounts",
			sa.Column(
				"account_type",
				postgresql.ENUM(
					"ADMIN",
					"MANAGER",
					"AUDITOR",
					name="shared_account_type",
					create_type=False,
				),
				nullable=True,
			),
		)
	if not _has_column("accounts", "created_at"):
		op.add_column(
			"accounts",
			sa.Column(
				"created_at",
				sa.DateTime(timezone=True),
				server_default=sa.text("now()"),
				nullable=False,
			),
		)

	op.execute(
		"""
        WITH account_source AS (
            SELECT DISTINCT ON (u.account_id)
                u.account_id,
                u.email,
                u.password_hash
            FROM users AS u
            WHERE u.account_id IS NOT NULL
            ORDER BY
                u.account_id,
                CASE WHEN u.account_type::text = 'MANAGER' THEN 0 ELSE 1 END,
                u.email
        )
        UPDATE accounts AS a
        SET
            email = COALESCE(a.email, account_source.email),
            password_hash = COALESCE(a.password_hash, account_source.password_hash),
            account_type = COALESCE(a.account_type, 'MANAGER'::shared_account_type)
        FROM account_source
        WHERE a.id = account_source.account_id;
        """
	)
	op.execute(
		"""
        UPDATE accounts
        SET email = CONCAT('account-', id::text, '@local.invalid')
        WHERE email IS NULL;
        """
	)
	op.execute(
		"""
        UPDATE accounts
        SET account_type = 'MANAGER'::shared_account_type
        WHERE account_type IS NULL;
        """
	)

	op.alter_column("accounts", "email", existing_type=sa.String(length=320), nullable=False)
	op.alter_column(
		"accounts",
		"account_type",
		existing_type=postgresql.ENUM(
			"ADMIN",
			"MANAGER",
			"AUDITOR",
			name="shared_account_type",
			create_type=False,
		),
		nullable=False,
	)

	if not _has_index("accounts", "ix_accounts_email"):
		op.create_index("ix_accounts_email", "accounts", ["email"], unique=True)


def _upgrade_projects_table() -> None:
	if not _has_table("projects"):
		return

	if not _has_column("projects", "overview"):
		op.add_column("projects", sa.Column("overview", sa.Text(), nullable=True))
	if not _has_column("projects", "place_types"):
		op.add_column(
			"projects",
			sa.Column(
				"place_types",
				postgresql.ARRAY(sa.String(length=100)),
				nullable=False,
				server_default=sa.text("'{}'::varchar[]"),
			),
		)
	if not _has_column("projects", "est_places"):
		op.add_column("projects", sa.Column("est_places", sa.Integer(), nullable=True))
	if not _has_column("projects", "est_auditors"):
		op.add_column("projects", sa.Column("est_auditors", sa.Integer(), nullable=True))
	if not _has_column("projects", "auditor_description"):
		op.add_column("projects", sa.Column("auditor_description", sa.Text(), nullable=True))
	if not _has_column("projects", "created_at"):
		op.add_column(
			"projects",
			sa.Column(
				"created_at",
				sa.DateTime(timezone=True),
				server_default=sa.text("now()"),
				nullable=False,
			),
		)

	if _has_column("projects", "description"):
		op.execute(
			"""
            UPDATE projects
            SET overview = description
            WHERE overview IS NULL AND description IS NOT NULL;
            """
		)


def _upgrade_places_table() -> None:
	if not _has_table("places"):
		return

	for column_name, column in [
		("city", sa.Column("city", sa.String(length=120), nullable=True)),
		("province", sa.Column("province", sa.String(length=120), nullable=True)),
		("country", sa.Column("country", sa.String(length=120), nullable=True)),
		("place_type", sa.Column("place_type", sa.String(length=120), nullable=True)),
		("lat", sa.Column("lat", sa.Float(), nullable=True)),
		("lng", sa.Column("lng", sa.Float(), nullable=True)),
		("start_date", sa.Column("start_date", sa.Date(), nullable=True)),
		("end_date", sa.Column("end_date", sa.Date(), nullable=True)),
		("est_auditors", sa.Column("est_auditors", sa.Integer(), nullable=True)),
		(
			"auditor_description",
			sa.Column("auditor_description", sa.Text(), nullable=True),
		),
	]:
		if not _has_column("places", column_name):
			op.add_column("places", column)

	if not _has_column("places", "created_at"):
		op.add_column(
			"places",
			sa.Column(
				"created_at",
				sa.DateTime(timezone=True),
				server_default=sa.text("now()"),
				nullable=False,
			),
		)

	if _has_column("places", "address"):
		op.execute(
			"""
            UPDATE places
            SET city = address
            WHERE city IS NULL AND address IS NOT NULL;
            """
		)
	if _has_column("places", "notes"):
		op.execute(
			"""
            UPDATE places
            SET auditor_description = notes
            WHERE auditor_description IS NULL AND notes IS NOT NULL;
            """
		)


def _create_manager_profiles_table() -> None:
	if _has_table("manager_profiles"):
		return

	op.create_table(
		"manager_profiles",
		sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
		sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
		sa.Column("full_name", sa.String(length=200), nullable=False),
		sa.Column("email", sa.String(length=320), nullable=False),
		sa.Column("phone", sa.String(length=50), nullable=True),
		sa.Column("position", sa.String(length=200), nullable=True),
		sa.Column("organization", sa.String(length=200), nullable=True),
		sa.Column("is_primary", sa.Boolean(), nullable=False, server_default=sa.text("false")),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.ForeignKeyConstraint(
			["account_id"],
			["accounts.id"],
			name="fk_manager_profiles_account_id_accounts",
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id", name="pk_manager_profiles"),
		sa.UniqueConstraint("email", name="uq_manager_profiles_email"),
	)
	op.create_index(
		"ix_manager_profiles_account_id",
		"manager_profiles",
		["account_id"],
		unique=False,
	)
	op.create_index("ix_manager_profiles_email", "manager_profiles", ["email"], unique=False)


def _backfill_manager_profiles() -> None:
	if not _has_table("manager_profiles"):
		return

	bind = op.get_bind()
	rows = bind.execute(
		sa.text(
			"""
            SELECT u.id, u.account_id, COALESCE(NULLIF(u.name, ''), a.name) AS full_name, u.email, a.name AS organization
            FROM users AS u
            JOIN accounts AS a ON a.id = u.account_id
            WHERE u.account_id IS NOT NULL
              AND u.account_type::text = 'MANAGER'
              AND NOT EXISTS (
                  SELECT 1
                  FROM manager_profiles AS mp
                  WHERE mp.email = u.email
              )
            ORDER BY u.email
            """
		)
	).mappings()

	for row in rows:
		bind.execute(
			sa.text(
				"""
                INSERT INTO manager_profiles (
                    id,
                    account_id,
                    full_name,
                    email,
                    organization,
                    is_primary,
                    created_at
                )
                VALUES (
                    :id,
                    :account_id,
                    :full_name,
                    :email,
                    :organization,
                    :is_primary,
                    NOW()
                )
                """
			),
			{
				"id": row["id"],
				"account_id": row["account_id"],
				"full_name": row["full_name"] or row["email"],
				"email": row["email"],
				"organization": row["organization"],
				"is_primary": True,
			},
		)


def _create_auditor_profiles_table() -> None:
	if _has_table("auditor_profiles"):
		return

	op.create_table(
		"auditor_profiles",
		sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
		sa.Column("account_id", postgresql.UUID(as_uuid=True), nullable=False),
		sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=True),
		sa.Column("auditor_code", sa.String(length=50), nullable=False),
		sa.Column("email", sa.String(length=320), nullable=True),
		sa.Column("full_name", sa.String(length=200), nullable=False),
		sa.Column("age_range", sa.String(length=80), nullable=True),
		sa.Column("gender", sa.String(length=80), nullable=True),
		sa.Column("country", sa.String(length=120), nullable=True),
		sa.Column("role", sa.String(length=120), nullable=True),
		sa.Column(
			"created_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.ForeignKeyConstraint(
			["account_id"],
			["accounts.id"],
			name="fk_auditor_profiles_account_id_accounts",
			ondelete="CASCADE",
		),
		sa.ForeignKeyConstraint(
			["user_id"],
			["users.id"],
			name="fk_auditor_profiles_user_id_users",
			ondelete="SET NULL",
		),
		sa.PrimaryKeyConstraint("id", name="pk_auditor_profiles"),
		sa.UniqueConstraint("auditor_code", name="uq_auditor_profiles_auditor_code"),
		sa.UniqueConstraint("user_id", name="uq_auditor_profiles_user_id"),
	)
	op.create_index(
		"ix_auditor_profiles_account_id",
		"auditor_profiles",
		["account_id"],
		unique=False,
	)
	op.create_index(
		"ix_auditor_profiles_auditor_code",
		"auditor_profiles",
		["auditor_code"],
		unique=False,
	)
	op.create_index("ix_auditor_profiles_email", "auditor_profiles", ["email"], unique=False)


def _backfill_auditor_profiles() -> None:
	if not (_has_table("auditors") and _has_table("auditor_profiles")):
		return

	bind = op.get_bind()
	rows = bind.execute(
		sa.text(
			"""
            SELECT
                a.id,
                a.account_id,
                a.user_id,
                a.auditor_code,
                u.email,
                COALESCE(NULLIF(u.name, ''), a.auditor_code) AS full_name,
                a.created_at
            FROM auditors AS a
            LEFT JOIN users AS u ON u.id = a.user_id
            WHERE NOT EXISTS (
                SELECT 1
                FROM auditor_profiles AS ap
                WHERE ap.id = a.id
            )
            """
		)
	).mappings()

	for row in rows:
		bind.execute(
			sa.text(
				"""
                INSERT INTO auditor_profiles (
                    id,
                    account_id,
                    user_id,
                    auditor_code,
                    email,
                    full_name,
                    created_at
                )
                VALUES (
                    :id,
                    :account_id,
                    :user_id,
                    :auditor_code,
                    :email,
                    :full_name,
                    :created_at
                )
                """
			),
			{
				"id": row["id"],
				"account_id": row["account_id"],
				"user_id": row["user_id"],
				"auditor_code": row["auditor_code"],
				"email": row["email"],
				"full_name": row["full_name"],
				"created_at": row["created_at"],
			},
		)


def _create_auditor_assignments_table() -> None:
	if _has_table("auditor_assignments"):
		return

	op.create_table(
		"auditor_assignments",
		sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
		sa.Column("auditor_profile_id", postgresql.UUID(as_uuid=True), nullable=False),
		sa.Column("project_id", postgresql.UUID(as_uuid=True), nullable=True),
		sa.Column("place_id", postgresql.UUID(as_uuid=True), nullable=True),
		sa.Column(
			"assigned_at",
			sa.DateTime(timezone=True),
			server_default=sa.text("now()"),
			nullable=False,
		),
		sa.CheckConstraint(
			"(project_id IS NOT NULL) <> (place_id IS NOT NULL)",
			name="ck_auditor_assignments_single_scope",
		),
		sa.ForeignKeyConstraint(
			["auditor_profile_id"],
			["auditor_profiles.id"],
			name="fk_auditor_assignments_auditor_profile_id_auditor_profiles",
			ondelete="CASCADE",
		),
		sa.ForeignKeyConstraint(
			["project_id"],
			["projects.id"],
			name="fk_auditor_assignments_project_id_projects",
			ondelete="CASCADE",
		),
		sa.ForeignKeyConstraint(
			["place_id"],
			["places.id"],
			name="fk_auditor_assignments_place_id_places",
			ondelete="CASCADE",
		),
		sa.PrimaryKeyConstraint("id", name="pk_auditor_assignments"),
		sa.UniqueConstraint(
			"auditor_profile_id",
			"project_id",
			name="uq_auditor_assignments_auditor_project",
		),
		sa.UniqueConstraint(
			"auditor_profile_id",
			"place_id",
			name="uq_auditor_assignments_auditor_place",
		),
	)
	op.create_index(
		"ix_auditor_assignments_auditor_profile_id",
		"auditor_assignments",
		["auditor_profile_id"],
		unique=False,
	)
	op.create_index(
		"ix_auditor_assignments_project_id",
		"auditor_assignments",
		["project_id"],
		unique=False,
	)
	op.create_index(
		"ix_auditor_assignments_place_id",
		"auditor_assignments",
		["place_id"],
		unique=False,
	)


def _backfill_auditor_assignments() -> None:
	if not (_has_table("assignments") and _has_table("auditor_assignments")):
		return

	bind = op.get_bind()
	rows = bind.execute(
		sa.text(
			"""
            SELECT old_assignments.id, old_assignments.auditor_id, old_assignments.place_id
            FROM assignments AS old_assignments
            WHERE NOT EXISTS (
                SELECT 1
                FROM auditor_assignments AS new_assignments
                WHERE new_assignments.id = old_assignments.id
            )
            """
		)
	).mappings()

	for row in rows:
		bind.execute(
			sa.text(
				"""
                INSERT INTO auditor_assignments (
                    id,
                    auditor_profile_id,
                    project_id,
                    place_id,
                    assigned_at
                )
                VALUES (
                    :id,
                    :auditor_profile_id,
                    NULL,
                    :place_id,
                    NOW()
                )
                """
			),
			{
				"id": row["id"],
				"auditor_profile_id": row["auditor_id"],
				"place_id": row["place_id"],
			},
		)


def _upgrade_audits_table() -> None:
	if not _has_table("audits"):
		return

	op.execute(
		"""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = 'audits'
                  AND column_name = 'status'
                  AND udt_name = 'audit_status'
            ) THEN
                ALTER TABLE audits
                ALTER COLUMN status
                TYPE shared_audit_status
                USING status::text::shared_audit_status;
            END IF;
        END
        $$;
        """
	)

	for column_name, column in [
		(
			"auditor_profile_id",
			sa.Column("auditor_profile_id", postgresql.UUID(as_uuid=True), nullable=True),
		),
		("audit_code", sa.Column("audit_code", sa.String(length=120), nullable=True)),
		(
			"instrument_key",
			sa.Column("instrument_key", sa.String(length=80), nullable=True),
		),
		(
			"instrument_version",
			sa.Column("instrument_version", sa.String(length=40), nullable=True),
		),
		("total_minutes", sa.Column("total_minutes", sa.Integer(), nullable=True)),
		("summary_score", sa.Column("summary_score", sa.Float(), nullable=True)),
		(
			"created_at",
			sa.Column(
				"created_at",
				sa.DateTime(timezone=True),
				server_default=sa.text("now()"),
				nullable=False,
			),
		),
		(
			"updated_at",
			sa.Column(
				"updated_at",
				sa.DateTime(timezone=True),
				server_default=sa.text("now()"),
				nullable=False,
			),
		),
	]:
		if not _has_column("audits", column_name):
			op.add_column("audits", column)

	if _has_column("audits", "auditor_id"):
		op.execute(
			"""
            UPDATE audits
            SET auditor_profile_id = auditor_id
            WHERE auditor_profile_id IS NULL;
            """
		)
	if _has_column("audits", "started_at"):
		op.execute(
			"""
            UPDATE audits
            SET created_at = COALESCE(created_at, started_at)
            WHERE started_at IS NOT NULL;
            """
		)
		op.execute(
			"""
            UPDATE audits
            SET updated_at = COALESCE(submitted_at, started_at, updated_at, NOW())
            WHERE updated_at IS NULL OR updated_at = created_at;
            """
		)

	op.execute(
		"""
        UPDATE audits
        SET audit_code = CONCAT('AUD-', REPLACE(SUBSTRING(id::text, 1, 8), '-', ''))
        WHERE audit_code IS NULL;
        """
	)
	if _has_table("instruments") and _has_column("audits", "instrument_id"):
		op.execute(
			"""
            UPDATE audits AS a
            SET
                instrument_key = COALESCE(a.instrument_key, i.key),
                instrument_version = COALESCE(a.instrument_version, i.version)
            FROM instruments AS i
            WHERE a.instrument_id = i.id;
            """
		)
	op.execute(
		"""
        UPDATE audits
        SET instrument_key = COALESCE(instrument_key, 'legacy'),
            instrument_version = COALESCE(instrument_version, '1')
        WHERE instrument_key IS NULL OR instrument_version IS NULL;
        """
	)
	op.execute(
		"""
        UPDATE audits
        SET summary_score = NULLIF(scores_json ->> 'total_score', '')::double precision
        WHERE summary_score IS NULL
          AND scores_json ? 'total_score';
        """
	)

	op.alter_column(
		"audits",
		"auditor_profile_id",
		existing_type=postgresql.UUID(as_uuid=True),
		nullable=False,
	)
	op.alter_column("audits", "audit_code", existing_type=sa.String(length=120), nullable=False)

	if not _has_index("audits", "ix_audits_auditor_profile_id"):
		op.create_index(
			"ix_audits_auditor_profile_id",
			"audits",
			["auditor_profile_id"],
			unique=False,
		)
	if not _has_index("audits", "ix_audits_audit_code"):
		op.create_index("ix_audits_audit_code", "audits", ["audit_code"], unique=True)


def upgrade() -> None:
	if not _is_target_product("yee"):
		return
	if context.is_offline_mode():
		return
	_create_shared_enums()
	_upgrade_users_table()
	_upgrade_accounts_table()
	_upgrade_projects_table()
	_upgrade_places_table()
	_create_manager_profiles_table()
	_backfill_manager_profiles()
	_create_auditor_profiles_table()
	_backfill_auditor_profiles()
	_create_auditor_assignments_table()
	_backfill_auditor_assignments()
	_upgrade_audits_table()


def downgrade() -> None:
	if not _is_target_product("yee"):
		return
	if context.is_offline_mode():
		return
	raise NotImplementedError("This compatibility migration is intentionally one-way.")

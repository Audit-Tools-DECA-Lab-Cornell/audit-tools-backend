"""Allow a project to outlive the user who created it (Playspace).

Self-service account deletion hard-deletes the login ``User`` while preserving
everything the organization still depends on - projects, places, and submitted
audits. ``projects.created_by_user_id`` was ``NOT NULL`` with an ``ON DELETE
RESTRICT`` foreign key, so the delete was refused outright.

This migration makes the creator reference optional and switches the foreign key
to ``ON DELETE SET NULL``, so deleting a person detaches their authorship and
leaves the project itself intact.

The downgrade refuses to restore ``NOT NULL`` while any project has a ``NULL``
creator: the original user rows are gone by then and authorship cannot be
reconstructed. Rolling back after real deletions requires assigning a surviving
owner to those projects first, or restoring from backup.

Created on the ``playspace`` branch:

    alembic -x product=playspace upgrade playspace@head

Hand-written and idempotent (guards on table/column/constraint state), so it is
safe on fresh, partially-migrated, and rerun states.

Revision ID: ps_0011
Revises: ps_0010
Create Date: 2026-08-07
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "ps_0011"
down_revision = "ps_0010"
branch_labels = None
depends_on = None

_TABLE_NAME = "projects"
_COLUMN_NAME = "created_by_user_id"
_FK_NAME = "fk_projects_created_by_user_id_users"


def _has_table(table_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	return table_name in inspector.get_table_names()


def _column_metadata(table_name: str, column_name: str) -> dict[str, object] | None:
	inspector = sa.inspect(op.get_bind())
	return next((column for column in inspector.get_columns(table_name) if column["name"] == column_name), None)


def _foreign_key(table_name: str, constraint_name: str) -> dict[str, object] | None:
	inspector = sa.inspect(op.get_bind())
	return next(
		(constraint for constraint in inspector.get_foreign_keys(table_name) if constraint["name"] == constraint_name),
		None,
	)


def _current_ondelete(table_name: str, constraint_name: str) -> str | None:
	constraint = _foreign_key(table_name, constraint_name)
	if constraint is None:
		return None
	options = constraint.get("options")
	if not isinstance(options, dict):
		return None
	ondelete = options.get("ondelete")
	return ondelete.upper() if isinstance(ondelete, str) else None


def _recreate_creator_fk(ondelete: str) -> None:
	"""Point the creator foreign key at ``users`` with the requested delete rule."""

	if _foreign_key(_TABLE_NAME, _FK_NAME) is not None:
		op.drop_constraint(_FK_NAME, _TABLE_NAME, type_="foreignkey")

	op.create_foreign_key(
		_FK_NAME,
		_TABLE_NAME,
		"users",
		[_COLUMN_NAME],
		["id"],
		ondelete=ondelete,
	)


def upgrade() -> None:
	if not _has_table(_TABLE_NAME):
		return

	creator = _column_metadata(_TABLE_NAME, _COLUMN_NAME)
	if creator is None:
		return

	if creator.get("nullable") is False:
		op.alter_column(
			_TABLE_NAME,
			_COLUMN_NAME,
			existing_type=sa.UUID(),
			nullable=True,
		)

	if _current_ondelete(_TABLE_NAME, _FK_NAME) != "SET NULL":
		_recreate_creator_fk("SET NULL")


def downgrade() -> None:
	if not _has_table(_TABLE_NAME):
		return

	creator = _column_metadata(_TABLE_NAME, _COLUMN_NAME)
	if creator is None:
		return

	if creator.get("nullable") is True:
		project_without_creator = (
			op.get_bind().execute(sa.text("SELECT 1 FROM projects WHERE created_by_user_id IS NULL LIMIT 1")).first()
		)
		if project_without_creator is not None:
			raise RuntimeError(
				"Refusing to restore projects.created_by_user_id NOT NULL while any project has no creator; "
				"assign a surviving owner to those projects or restore from backup."
			)

	if _current_ondelete(_TABLE_NAME, _FK_NAME) != "RESTRICT":
		_recreate_creator_fk("RESTRICT")

	if creator.get("nullable") is True:
		op.alter_column(
			_TABLE_NAME,
			_COLUMN_NAME,
			existing_type=sa.UUID(),
			nullable=False,
		)

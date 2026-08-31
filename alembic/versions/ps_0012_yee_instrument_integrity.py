"""Enforce YEE instrument catalog integrity (Playspace branch).

``instruments`` is a shared-core table that exists physically in BOTH product
databases, so a row with ``instrument_key='yee'`` can land in either one. These
two partial indexes are therefore mirrored from ``yee_0010``: the shared ORM
metadata must describe the same objects on both connections, and a YEE catalog
row orphaned in the Playspace database has to be caught there too.

Both indexes are scoped by ``instrument_key = 'yee'``, so Playspace's own
instrument rows are untouched on either branch.

1. ``uq_instruments_yee_version_label_ci`` - one YEE version label, compared
   case-insensitively. Duplicate labels make an audit's stamp ambiguous, which
   is what the Phase 3 history inventory exists to rule out.
2. ``uq_instruments_yee_single_active`` - at most one active YEE version. The
   activation path deactivates the previous row and activates the candidate in
   one transaction; this index is what makes a concurrent double-activation a
   recoverable unique-violation instead of two live instruments.

This migration NEVER repairs data. If it finds duplicate labels or more than one
active row it raises and leaves the database untouched: choosing which row wins
is a reviewed decision that belongs to the separately approved history repair,
not to a schema migration running unattended.

Plain ``CREATE UNIQUE INDEX`` (not ``CONCURRENTLY``) is deliberate. Alembic runs
this inside a transaction, ``CONCURRENTLY`` cannot, and the YEE catalog is a
handful of rows - the brief ACCESS EXCLUSIVE lock is not worth the complexity.

Created on the ``playspace`` branch:

    alembic -x product=playspace upgrade playspace@head

Hand-written and idempotent (guards on table/index state), so it is safe on
fresh, partially-migrated, and rerun states.

Revision ID: ps_0012
Revises: ps_0011
Create Date: 2026-08-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "ps_0012"
down_revision = "ps_0011"
branch_labels = None
depends_on = None

_TABLE_NAME = "instruments"
_LABEL_INDEX_NAME = "uq_instruments_yee_version_label_ci"
_ACTIVE_INDEX_NAME = "uq_instruments_yee_single_active"


def _has_table(table_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	return table_name in inspector.get_table_names()


def _has_index(table_name: str, index_name: str) -> bool:
	inspector = sa.inspect(op.get_bind())
	return any(index["name"] == index_name for index in inspector.get_indexes(table_name))


def _duplicate_version_labels() -> list[tuple[str, int]]:
	rows = op.get_bind().execute(
		sa.text(
			"""
			SELECT lower(instrument_version) AS label, count(*) AS row_count
			FROM instruments
			WHERE instrument_key = 'yee'
			GROUP BY lower(instrument_version)
			HAVING count(*) > 1
			ORDER BY label
			"""
		)
	)
	return [(str(row.label), int(row.row_count)) for row in rows]


def _active_instrument_ids() -> list[str]:
	rows = op.get_bind().execute(
		sa.text(
			"""
			SELECT id
			FROM instruments
			WHERE instrument_key = 'yee' AND is_active
			ORDER BY created_at, id
			"""
		)
	)
	return [str(row.id) for row in rows]


def _require_unique_version_labels() -> None:
	duplicates = _duplicate_version_labels()
	if not duplicates:
		return
	detail = ", ".join(f"{label!r} x{count}" for label, count in duplicates)
	raise RuntimeError(
		f"Refusing to create {_LABEL_INDEX_NAME}: YEE version labels are duplicated "
		f"case-insensitively ({detail}). Resolve them through the reviewed instrument "
		"history repair first; this migration never renames or deletes a catalog row."
	)


def _require_at_most_one_active() -> None:
	active_ids = _active_instrument_ids()
	if len(active_ids) <= 1:
		return
	raise RuntimeError(
		f"Refusing to create {_ACTIVE_INDEX_NAME}: {len(active_ids)} YEE instrument rows are "
		f"active at once ({', '.join(active_ids)}). Choose the canonical active version through "
		"the reviewed history repair first; this migration never deactivates a row."
	)


def upgrade() -> None:
	if not _has_table(_TABLE_NAME):
		return

	if not _has_index(_TABLE_NAME, _LABEL_INDEX_NAME):
		_require_unique_version_labels()
		op.execute(
			sa.text(
				"""
				CREATE UNIQUE INDEX IF NOT EXISTS uq_instruments_yee_version_label_ci
				ON instruments (lower(instrument_version))
				WHERE instrument_key = 'yee'
				"""
			)
		)

	if not _has_index(_TABLE_NAME, _ACTIVE_INDEX_NAME):
		_require_at_most_one_active()
		op.execute(
			sa.text(
				"""
				CREATE UNIQUE INDEX IF NOT EXISTS uq_instruments_yee_single_active
				ON instruments (instrument_key)
				WHERE instrument_key = 'yee' AND is_active
				"""
			)
		)


def downgrade() -> None:
	"""Drop the indexes only.

	The downgrade deliberately does not reverse any data decision: nothing here
	created, renamed, deactivated, or deleted a catalog row, so there is nothing
	to restore.
	"""

	if not _has_table(_TABLE_NAME):
		return

	op.execute(sa.text("DROP INDEX IF EXISTS uq_instruments_yee_single_active"))
	op.execute(sa.text("DROP INDEX IF EXISTS uq_instruments_yee_version_label_ci"))

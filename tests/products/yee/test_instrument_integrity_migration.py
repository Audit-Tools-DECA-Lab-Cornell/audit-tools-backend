"""Safety properties of the mirrored YEE instrument-integrity migrations.

``yee_0010`` and ``ps_0012`` create the same two YEE-scoped partial indexes on
the shared ``instruments`` table. Two things must hold and neither is visible
from a passing upgrade:

1. A dirty catalog makes the migration REFUSE, not repair. Picking which
   duplicate label or which active row wins is a reviewed decision.
2. The two branch files cannot drift apart. They are separate files on separate
   branches describing one shared table, which is exactly the shape that rots.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path
from types import ModuleType

import pytest
from typing import cast

from sqlalchemy import Table

_VERSIONS = Path(__file__).resolve().parents[3] / "alembic" / "versions"
_YEE_PATH = _VERSIONS / "yee_0010_yee_instrument_integrity.py"
_PLAYSPACE_PATH = _VERSIONS / "ps_0012_yee_instrument_integrity.py"


def _load(path: Path) -> ModuleType:
	spec = importlib.util.spec_from_file_location(path.stem, path)
	assert spec is not None and spec.loader is not None
	module = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(module)
	return module


def test_duplicate_version_labels_refuse_instead_of_repairing() -> None:
	module = _load(_YEE_PATH)
	module._duplicate_version_labels = lambda: [("2.0", 2), ("draft", 3)]  # type: ignore[attr-defined]

	with pytest.raises(RuntimeError) as error:
		module._require_unique_version_labels()

	message = str(error.value)
	# The operator has to be able to act on this without opening a shell.
	assert "'2.0' x2" in message
	assert "'draft' x3" in message
	assert "never renames or deletes" in message


def test_multiple_active_rows_refuse_instead_of_deactivating() -> None:
	module = _load(_YEE_PATH)
	module._active_instrument_ids = lambda: ["id-a", "id-b"]  # type: ignore[attr-defined]

	with pytest.raises(RuntimeError) as error:
		module._require_at_most_one_active()

	message = str(error.value)
	assert "id-a" in message and "id-b" in message
	assert "never deactivates" in message


def test_a_clean_catalog_passes_both_preflights() -> None:
	module = _load(_YEE_PATH)
	module._duplicate_version_labels = lambda: []  # type: ignore[attr-defined]
	module._active_instrument_ids = lambda: ["only-one"]  # type: ignore[attr-defined]

	module._require_unique_version_labels()
	module._require_at_most_one_active()


def test_zero_active_rows_is_not_a_migration_blocker() -> None:
	"""The catalog can legitimately hold no active YEE row.

	The Playspace database normally holds none at all, and the index only
	constrains rows that exist.
	"""

	module = _load(_YEE_PATH)
	module._active_instrument_ids = lambda: []  # type: ignore[attr-defined]
	module._require_at_most_one_active()


def _normalized_body(path: Path) -> str:
	"""File contents with branch-specific identity stripped."""

	text = path.read_text(encoding="utf-8")
	text = re.sub(r'revision = "(yee_0010|ps_0012)"', 'revision = "REV"', text)
	text = re.sub(r'down_revision = "(yee_0009|ps_0011)"', 'down_revision = "PARENT"', text)
	# Drop the docstring, which names its own branch by design.
	return text.split('"""', 2)[-1]


def test_the_two_branch_migrations_cannot_drift() -> None:
	assert _normalized_body(_YEE_PATH) == _normalized_body(_PLAYSPACE_PATH)


def test_both_branches_declare_the_same_indexes_as_the_orm() -> None:
	from app.models import Instrument

	# __table__ is annotated FromClause on the declarative base; the concrete
	# Table is what carries index metadata.
	table = cast(Table, Instrument.__table__)
	orm_indexes = {index.name for index in table.indexes}
	for path in (_YEE_PATH, _PLAYSPACE_PATH):
		module = _load(path)
		assert {module._LABEL_INDEX_NAME, module._ACTIVE_INDEX_NAME} == orm_indexes

	# The ORM predicates must match the SQL the migrations actually run, or
	# autogenerate will propose to drop and recreate these on the next change.
	predicates = {str(index.name): str(index.dialect_options["postgresql"].get("where")) for index in table.indexes}
	migration_sql = _YEE_PATH.read_text(encoding="utf-8")
	assert predicates["uq_instruments_yee_version_label_ci"] == "instrument_key = 'yee'"
	assert predicates["uq_instruments_yee_single_active"] == "instrument_key = 'yee' AND is_active"
	assert "WHERE instrument_key = 'yee'" in migration_sql
	assert "WHERE instrument_key = 'yee' AND is_active" in migration_sql

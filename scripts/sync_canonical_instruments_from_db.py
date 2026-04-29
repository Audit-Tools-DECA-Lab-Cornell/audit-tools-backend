"""
Export Playspace ``instruments`` rows to canonical JSON files.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# Repo root: audit-tools-backend/
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(_REPO_ROOT))

from app.models import Instrument  # noqa: E402
from app.products.playspace.schemas.instrument import PlayspaceInstrumentResponse  # noqa: E402


@dataclass(frozen=True, slots=True)
class InstrumentSyncPaths:
	"""Output locations under ``app/products/playspace/instruments/``."""

	legacy_basename: str
	active_suffix: str
	catalog_version_sep: str

	@staticmethod
	def default() -> InstrumentSyncPaths:
		return InstrumentSyncPaths(
			legacy_basename="pvua_v5_2.instrument.json",
			active_suffix=".active.instrument.json",
			catalog_version_sep="__v",
		)


def _env_database_url() -> str | None:
	for key in ("PLAYSPACE_INSTRUMENT_SYNC_DATABASE_URL", "DATABASE_URL_PLAYSPACE", "DEV_DATABASE_URL_PLAYSPACE"):
		raw = os.getenv(key)
		if raw and raw.strip():
			return raw.strip()
	return None


def _normalize_postgres_url(raw_url: str) -> tuple[URL, dict[str, object]]:
	"""Build a SQLAlchemy asyncpg URL and connect args (mirrors ``app.database``)."""

	normalized = raw_url.strip()
	if normalized.startswith("postgres://"):
		normalized = normalized.replace("postgres://", "postgresql://", 1)
	sqlalchemy_url = make_url(normalized)
	if sqlalchemy_url.drivername == "postgresql":
		sqlalchemy_url = sqlalchemy_url.set(drivername="postgresql+asyncpg")
	url_query = dict(sqlalchemy_url.query)
	sslmode = url_query.pop("sslmode", None)
	url_query.pop("channel_binding", None)
	connect_args: dict[str, object] = {}
	if isinstance(sslmode, str) and sslmode.lower() in {"require", "verify-ca", "verify-full"}:
		connect_args["ssl"] = True
	return sqlalchemy_url.set(query=url_query), connect_args


def _pick_winner_for_pair(rows: Sequence[Instrument]) -> Instrument:
	"""DB wins: prefer active, then latest ``updated_at``, then ``created_at``."""

	return max(rows, key=lambda r: (r.is_active, r.updated_at, r.created_at))


def _pick_active_for_key(all_rows: Iterable[Instrument], instrument_key: str) -> Instrument | None:
	"""Match ``get_active_instrument`` ordering (``created_at`` desc) for one key."""

	candidates = [r for r in all_rows if r.instrument_key == instrument_key and r.is_active]
	if not candidates:
		return None
	return max(candidates, key=lambda r: r.created_at)


def _content_to_canonical_file_object(content: Mapping[str, Any]) -> dict[str, Any]:
	"""Match on-disk format (``{"en": { ... PlayspaceInstrument ... }}``)."""

	if "en" in content and isinstance(content.get("en"), dict):
		return dict(content)
	inner = dict(content) if isinstance(content, Mapping) else {}
	return {"en": inner}


def _to_playspace_instrument_dict_for_validation(file_payload: Mapping[str, Any]) -> dict[str, Any]:
	"""
	Unwrap the same way as ``get_canonical_instrument_payload`` in
	``app.products.playspace.instrument`` (before the fixed legacy key / version check).
	"""

	payload: dict[str, Any] = dict(file_payload)
	if "instrument_key" not in payload and isinstance(payload.get("en"), dict):
		inner = payload.get("en")
		if not isinstance(inner, dict):
			raise ValueError("Expected the localized Playspace instrument payload to be a JSON object.")
		return dict(inner)
	return dict(payload)


def _validate_against_playspace_schema(
	*,
	file_payload: Mapping[str, Any],
	instrument_id: uuid.UUID,
	instrument_key: str,
	instrument_version: str,
	label: str,
) -> None:
	"""
	Ensure the written shape matches ``PlayspaceInstrumentResponse`` and thus can be loaded
	like ``get_canonical_instrument_response`` and ``content["en"]`` in audit services.
	"""

	try:
		inner = _to_playspace_instrument_dict_for_validation(file_payload)
		PlayspaceInstrumentResponse.model_validate(inner)
	except (ValidationError, ValueError) as err:
		raise ValueError(
			f"Instrument failed PlayspaceInstrumentResponse validation "
			f"({label}, id={instrument_id}, key={instrument_key!r}, version={instrument_version!r}): {err}"
		) from err


def _slug_version_for_filename(version: str) -> str:
	"""File-system safe fragment for a version string."""

	return re.sub(r"[^0-9A-Za-z._-]+", "_", version).strip("_") or "unknown"


def _write_json_file(path: Path, data: object, *, dry_run: bool) -> bool:
	"""Return True if a write would occur / occurred."""

	serialized = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=False) + "\n"
	if dry_run:
		print(f"[dry-run] would write {path} ({len(serialized)} bytes)")
		return True
	path.parent.mkdir(parents=True, exist_ok=True)
	changed = (not path.is_file()) or path.read_text(encoding="utf-8") != serialized
	if changed:
		path.write_text(serialized, encoding="utf-8")
	return changed


def _list_managed_extras(instruments_dir: Path, *, paths: InstrumentSyncPaths) -> list[Path]:
	"""JSON files that this tool owns (and may delete when obsolete)."""

	if not instruments_dir.is_dir():
		return []
	result: list[Path] = []
	for child in instruments_dir.iterdir():
		if not child.is_file() or not child.suffix == ".json":
			continue
		name = child.name
		if name == paths.legacy_basename:
			continue
		if name.endswith(paths.active_suffix):
			result.append(child)
		elif paths.catalog_version_sep in name and name.endswith(".instrument.json"):
			result.append(child)
	return result


async def _load_all_instruments(session: AsyncSession) -> list[Instrument]:
	result = await session.execute(select(Instrument))
	return list(result.scalars().all())


def sync_instruments(
	rows: list[Instrument],
	*,
	legacy_instrument_key: str,
	legacy_instrument_version: str,
	instruments_dir: Path,
	paths: InstrumentSyncPaths,
	dry_run: bool,
	skip_schema_validation: bool = False,
) -> set[Path]:
	"""
	Write canonical files; return the set of paths that are part of the current sync.

	That set is used to remove managed catalog / active files that are no longer needed.
	"""

	by_key_version: dict[tuple[str, str], list[Instrument]] = defaultdict(list)
	keys: set[str] = set()
	for row in rows:
		by_key_version[(row.instrument_key, row.instrument_version)].append(row)
		keys.add(row.instrument_key)

	desired_paths: set[Path] = set()

	# 1) One file per (key, version) in the database.
	for (ikey, iversion), group in sorted(by_key_version.items()):
		winner = _pick_winner_for_pair(group)
		payload = _content_to_canonical_file_object(winner.content)
		if not skip_schema_validation:
			_validate_against_playspace_schema(
				file_payload=payload,
				instrument_id=winner.id,
				instrument_key=ikey,
				instrument_version=iversion,
				label="catalog",
			)
		filename = f"{ikey}{paths.catalog_version_sep}{_slug_version_for_filename(iversion)}.instrument.json"
		out = instruments_dir / filename
		desired_paths.add(out)
		_write_json_file(out, payload, dry_run=dry_run)

	# 2) Active snapshot per instrument_key (current published line).
	for ikey in sorted(keys):
		active_row = _pick_active_for_key(rows, ikey)
		if active_row is None:
			continue
		payload = _content_to_canonical_file_object(active_row.content)
		if not skip_schema_validation:
			_validate_against_playspace_schema(
				file_payload=payload,
				instrument_id=active_row.id,
				instrument_key=active_row.instrument_key,
				instrument_version=active_row.instrument_version,
				label="active",
			)
		out = instruments_dir / f"{ikey}{paths.active_suffix}"
		desired_paths.add(out)
		_write_json_file(out, payload, dry_run=dry_run)

	# 3) Legacy anchor file: best row for a fixed (key, version) — used by
	#    ``get_canonical_instrument_payload`` as the v5.2 fallback.
	legacy_key = (legacy_instrument_key, legacy_instrument_version)
	if legacy_key in by_key_version:
		winner = _pick_winner_for_pair(by_key_version[legacy_key])
		payload = _content_to_canonical_file_object(winner.content)
		if not skip_schema_validation:
			_validate_against_playspace_schema(
				file_payload=payload,
				instrument_id=winner.id,
				instrument_key=legacy_instrument_key,
				instrument_version=legacy_instrument_version,
				label="legacy",
			)
		out = instruments_dir / paths.legacy_basename
		desired_paths.add(out)
		_write_json_file(out, payload, dry_run=dry_run)
	else:
		print(
			f"Note: no database row for legacy anchor {legacy_instrument_key!r} v{legacy_instrument_version!r}; "
			f"leaving {paths.legacy_basename} unchanged.",
			file=sys.stderr,
		)

	# 4) Remove managed catalog/active files that are no longer produced.
	if not dry_run:
		for extra in _list_managed_extras(instruments_dir, paths=paths):
			if extra not in desired_paths:
				extra.unlink()
				print(f"Removed obsolete {extra.name}")
	else:
		for extra in _list_managed_extras(instruments_dir, paths=paths):
			if extra not in desired_paths:
				print(f"[dry-run] would remove {extra}")

	return desired_paths


async def _amain() -> int:
	parser = argparse.ArgumentParser(
		description="Export the playspace instruments table into app/products/playspace/instruments/.",
	)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Print actions without writing or deleting files.",
	)
	parser.add_argument(
		"--database-url",
		default=None,
		help="Override database URL (otherwise env PLAYSPACE / DATABASE_URL_PLAYSPACE).",
	)
	parser.add_argument(
		"--no-validate",
		action="store_true",
		help="Skip PlayspaceInstrumentResponse validation (not recommended; use only for recovery).",
	)
	args = parser.parse_args()
	raw_url = args.database_url or _env_database_url()
	if not raw_url:
		print(
			"Missing database URL. Set PLAYSPACE_INSTRUMENT_SYNC_DATABASE_URL or DATABASE_URL_PLAYSPACE.",
			file=sys.stderr,
		)
		return 1

	url, connect_args = _normalize_postgres_url(raw_url)
	engine = create_async_engine(url, echo=False, pool_pre_ping=True, connect_args=connect_args)
	session_factory = async_sessionmaker(
		bind=engine,
		autoflush=False,
		expire_on_commit=False,
	)

	instruments_dir = _REPO_ROOT / "app" / "products" / "playspace" / "instruments"
	paths = InstrumentSyncPaths.default()
	try:
		async with session_factory() as session:
			rows = await _load_all_instruments(session)
	finally:
		await engine.dispose()

	if not rows:
		print("No rows in instruments table; nothing to export.", file=sys.stderr)
		return 0

	# Local import so this module can be imported for tests without loading disk JSON.
	from app.products.playspace import instrument as instrument_mod  # noqa: E402

	try:
		sync_instruments(
			rows,
			legacy_instrument_key=instrument_mod.INSTRUMENT_KEY,
			legacy_instrument_version=instrument_mod.INSTRUMENT_VERSION,
			instruments_dir=instruments_dir,
			paths=paths,
			dry_run=bool(args.dry_run),
			skip_schema_validation=bool(args.no_validate),
		)
	except ValueError as err:
		print(f"Sync failed: {err}", file=sys.stderr)
		return 1
	return 0


def main() -> None:
	raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
	main()

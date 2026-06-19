"""
Import canonical Playspace instrument JSON files into the ``instruments`` table.

This is the inverse of ``sync_canonical_instruments_from_db.py``. It updates every
database row that shares a ``(instrument_key, instrument_version)`` pair with a
catalog file under ``app/products/playspace/instruments/``.

Optionally recalculates cached score payloads for submitted audits so dashboards
and exports reflect the updated scale scoring without waiting for a live rescore.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from dotenv import find_dotenv, load_dotenv
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified

load_dotenv(find_dotenv())

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(_REPO_ROOT))

from app.models import AuditStatus, Instrument, PlayspaceSubmission  # noqa: E402
from app.products.playspace.schemas.instrument import PlayspaceInstrumentResponse  # noqa: E402
from app.products.playspace.scoring import score_audit  # noqa: E402
from app.products.playspace.services.instrument import (  # noqa: E402
	build_instrument_response_from_row,
	get_instrument_version,
	sync_instrument_version_in_content,
)
from scripts.sync_canonical_instruments_from_db import (  # noqa: E402
	InstrumentSyncPaths,
	_to_playspace_instrument_dict_for_validation,
	_validate_against_playspace_schema,
)


def _env_database_url() -> str | None:
	for key in ("PLAYSPACE_INSTRUMENT_SYNC_DATABASE_URL", "DATABASE_URL_PLAYSPACE", "DEV_DATABASE_URL_PLAYSPACE"):
		raw = os.getenv(key)
		if raw and raw.strip():
			return raw.strip()
	return None


def _normalize_postgres_url(raw_url: str) -> tuple[URL, dict[str, object]]:
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


def _catalog_instrument_files(instruments_dir: Path, *, paths: InstrumentSyncPaths) -> list[Path]:
	"""Return versioned catalog files managed by the sync tooling."""

	if not instruments_dir.is_dir():
		return []
	result: list[Path] = []
	for child in sorted(instruments_dir.iterdir()):
		if not child.is_file() or child.suffix != ".json":
			continue
		name = child.name
		if paths.catalog_version_sep in name and name.endswith(".instrument.json"):
			result.append(child)
	return result


def _instrument_identity(payload: Mapping[str, Any]) -> tuple[str, str]:
	"""Read ``(instrument_key, instrument_version)`` from a canonical file payload."""

	inner = _to_playspace_instrument_dict_for_validation(payload)
	instrument_key = inner.get("instrument_key")
	instrument_version = inner.get("instrument_version")
	if not isinstance(instrument_key, str) or not instrument_key.strip():
		raise ValueError("Instrument payload is missing instrument_key.")
	if not isinstance(instrument_version, str) or not instrument_version.strip():
		raise ValueError("Instrument payload is missing instrument_version.")
	return instrument_key.strip(), instrument_version.strip()


def _parse_catalog_filename(path: Path, *, paths: InstrumentSyncPaths) -> tuple[str, str] | None:
	"""Return ``(instrument_key, instrument_version)`` encoded in a catalog filename."""

	name = path.name
	if not name.endswith(".instrument.json"):
		return None
	stem = name[: -len(".instrument.json")]
	sep = paths.catalog_version_sep
	if sep not in stem:
		return None
	instrument_key, version_slug = stem.split(sep, 1)
	if not instrument_key or not version_slug:
		return None
	return instrument_key, version_slug


def _instrument_identity_from_file(
	path: Path, payload: Mapping[str, Any], *, paths: InstrumentSyncPaths
) -> tuple[str, str]:
	"""Prefer the catalog filename version over stale metadata inside older exports."""

	parsed = _parse_catalog_filename(path, paths=paths)
	if parsed is not None:
		instrument_key, instrument_version = parsed
		return instrument_key, instrument_version
	return _instrument_identity(payload)


def _prepare_catalog_payload(
	path: Path,
	file_payload: Mapping[str, Any],
	*,
	paths: InstrumentSyncPaths,
) -> tuple[tuple[str, str], dict[str, Any]]:
	"""Normalize one on-disk catalog payload before writing it to the database."""

	instrument_key, instrument_version = _instrument_identity_from_file(path, file_payload, paths=paths)
	payload = sync_instrument_version_in_content(dict(file_payload), instrument_version)
	return (instrument_key, instrument_version), payload


def _load_catalog_payloads(
	instruments_dir: Path,
	*,
	paths: InstrumentSyncPaths,
	skip_schema_validation: bool,
) -> dict[tuple[str, str], dict[str, Any]]:
	"""Load one payload per ``(instrument_key, instrument_version)`` from disk."""

	payloads: dict[tuple[str, str], dict[str, Any]] = {}
	for path in _catalog_instrument_files(instruments_dir, paths=paths):
		file_payload = json.loads(path.read_text(encoding="utf-8"))
		identity, prepared_payload = _prepare_catalog_payload(path, file_payload, paths=paths)
		instrument_key, instrument_version = identity
		if not skip_schema_validation:
			_validate_against_playspace_schema(
				file_payload=prepared_payload,
				instrument_id=path,
				instrument_key=instrument_key,
				instrument_version=instrument_version,
				label=str(path.name),
			)
		payloads[identity] = prepared_payload
	return payloads


async def _load_instrument_rows(session: AsyncSession) -> list[Instrument]:
	result = await session.execute(select(Instrument))
	return list(result.scalars().all())


def _build_score_totals(raw_partition: object) -> dict[str, float] | None:
	if not isinstance(raw_partition, dict):
		return None
	fields = (
		"play_value_total",
		"usability_total",
	)
	values = {field: raw_partition.get(field) for field in fields}
	if not all(isinstance(value, (int, float)) for value in values.values()):
		return None
	return {field: float(values[field]) for field in fields}


def _combined_construct_total(score_totals: dict[str, float] | None) -> float | None:
	if score_totals is None:
		return None
	return round(score_totals["play_value_total"] + score_totals["usability_total"], 2)


async def _apply_submission_rescore(
	session: AsyncSession,
	*,
	submission: PlayspaceSubmission,
	dry_run: bool,
) -> bool:
	"""Recalculate one submitted audit's cached score payload."""

	responses_json = submission.responses_json
	if not isinstance(responses_json, dict) or not responses_json:
		return False

	instrument_key = submission.instrument_key or "pvua_v5_2"
	instrument_version = submission.instrument_version
	if instrument_version is None:
		return False

	instrument_row = await get_instrument_version(session, instrument_key, instrument_version)
	if instrument_row is None:
		return False

	instrument = build_instrument_response_from_row(instrument_row)
	if instrument is None:
		return False

	try:
		calculated_scores = score_audit(
			responses_json=responses_json,
			include_maximums=True,
			instrument=instrument,
		)
	except ValueError:
		return False

	if dry_run:
		return True

	submission.scores_json = calculated_scores
	flag_modified(submission, "scores_json")

	audit_partition = _build_score_totals(calculated_scores.get("audit"))
	survey_partition = _build_score_totals(calculated_scores.get("survey"))
	overall_payload = _build_score_totals(calculated_scores.get("overall"))

	submission.audit_play_value_score = audit_partition["play_value_total"] if audit_partition else None
	submission.audit_usability_score = audit_partition["usability_total"] if audit_partition else None
	submission.survey_play_value_score = survey_partition["play_value_total"] if survey_partition else None
	submission.survey_usability_score = survey_partition["usability_total"] if survey_partition else None
	submission.summary_score = _combined_construct_total(overall_payload)
	return True


async def sync_instruments_to_db(
	session: AsyncSession,
	*,
	instruments_dir: Path,
	paths: InstrumentSyncPaths,
	dry_run: bool,
	skip_schema_validation: bool,
	rescore_submissions: bool,
) -> tuple[int, int, int]:
	"""Push catalog payloads to the database and optionally rescore submissions."""

	catalog_payloads = _load_catalog_payloads(
		instruments_dir,
		paths=paths,
		skip_schema_validation=skip_schema_validation,
	)
	rows = await _load_instrument_rows(session)

	updated_rows = 0
	missing_pairs: set[tuple[str, str]] = set()
	for row in rows:
		payload = catalog_payloads.get((row.instrument_key, row.instrument_version))
		if payload is None:
			missing_pairs.add((row.instrument_key, row.instrument_version))
			continue
		if row.content == payload:
			continue
		updated_rows += 1
		if dry_run:
			print(
				f"[dry-run] would update instrument id={row.id} "
				f"key={row.instrument_key!r} version={row.instrument_version!r}"
			)
			continue
		row.content = payload
		flag_modified(row, "content")

	if not dry_run and updated_rows:
		await session.commit()

	rescored_submissions = 0
	if rescore_submissions:
		result = await session.execute(
			select(PlayspaceSubmission)
			.where(PlayspaceSubmission.status == AuditStatus.SUBMITTED)
			.options(
				selectinload(PlayspaceSubmission.submission_context),
				selectinload(PlayspaceSubmission.submission_sections),
			)
		)
		submissions = list(result.scalars().all())
		for submission in submissions:
			changed = await _apply_submission_rescore(session, submission=submission, dry_run=dry_run)
			if changed:
				rescored_submissions += 1
		if not dry_run and rescored_submissions:
			await session.commit()

	for instrument_key, instrument_version in sorted(missing_pairs):
		print(
			f"Warning: no catalog file for instrument key={instrument_key!r} version={instrument_version!r}",
			file=sys.stderr,
		)

	return len(catalog_payloads), updated_rows, rescored_submissions


async def _amain() -> int:
	parser = argparse.ArgumentParser(
		description="Import canonical Playspace instrument JSON files into the instruments table.",
	)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Print actions without writing to the database.",
	)
	parser.add_argument(
		"--database-url",
		default=None,
		help="Override database URL (otherwise env PLAYSPACE / DATABASE_URL_PLAYSPACE).",
	)
	parser.add_argument(
		"--no-validate",
		action="store_true",
		help="Skip PlayspaceInstrumentResponse validation (not recommended).",
	)
	parser.add_argument(
		"--rescore-submissions",
		action="store_true",
		help="Recalculate cached score payloads for submitted audits after instrument updates.",
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
			catalog_count, updated_rows, rescored_submissions = await sync_instruments_to_db(
				session,
				instruments_dir=instruments_dir,
				paths=paths,
				dry_run=bool(args.dry_run),
				skip_schema_validation=bool(args.no_validate),
				rescore_submissions=bool(args.rescore_submissions),
			)
	finally:
		await engine.dispose()

	action = "would update" if args.dry_run else "updated"
	print(f"Catalog files loaded: {catalog_count}")
	print(f"Instrument rows {action}: {updated_rows}")
	if args.rescore_submissions:
		rescore_action = "would rescore" if args.dry_run else "rescored"
		print(f"Submitted audits {rescore_action}: {rescored_submissions}")
	return 0


def main() -> None:
	raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
	main()

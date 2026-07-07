#!/usr/bin/env python3
"""Sync the active ``yee`` instrument DB row to the committed snapshot IN PLACE.

This overwrites the **currently active** ``yee`` instrument row's stored
``content`` with the canonical snapshot at
``app/products/yee/instruments/yee.active.instrument.json`` (loaded and validated
via ``app.yee_scoring.get_yee_instrument_data``). It does NOT create a new
version — the ``instrument_version`` is preserved (per the pre-test/pre-production
decision to correct the first-ever instrument in place rather than version it).

Why this is needed: ``GET /yee/instrument`` now serves the DB row
authoritatively (no canonical overlay), so an environment whose row predates the
expanded, fully-backend-supplied content must be brought up to date. The snapshot
already folds in the 2026-07-06 text fixes (``NO`` -> ``No``, ``Yes, alot`` ->
``Yes, a lot``, condition wording) AND the new content the mobile app now reads
from the backend (public-access / open-hours context questions, per-domain
weighting prompts, condition prompt, final-comments prompt). One run brings any
environment fully current. It is idempotent: re-running when the row already
matches the snapshot writes nothing. A fresh environment with no row needs no
action — bootstrap seeds it from the same snapshot.

Targets ``DATABASE_URL_YEE`` (loaded from your environment / .env), mirroring
``scripts/add_test_auditors.py``.

Usage:
    python -m scripts.sync_yee_active_instrument --dry-run
    python -m scripts.sync_yee_active_instrument --yes
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

from dotenv import find_dotenv, load_dotenv
from sqlalchemy import select
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified

from app.models import Instrument
from app.yee_instrument_schema import YeeInstrumentResponse
from app.yee_scoring import get_yee_instrument_data

INSTRUMENT_KEY = "yee"


def _canonical_snapshot() -> dict[str, object]:
	"""The committed snapshot, validated + normalized to the response schema."""

	return YeeInstrumentResponse.model_validate(get_yee_instrument_data()).model_dump()


def _summarize_changes(current: object, target: dict[str, object]) -> list[str]:
	"""Human-readable list of top-level keys that would change."""

	notes: list[str] = []
	current_map = current if isinstance(current, dict) else {}
	for key in target:
		if current_map.get(key) != target[key]:
			notes.append(key)
	for key in current_map:
		if key not in target:
			notes.append(f"{key} (removed)")
	return notes


async def _sync_active_instrument(session: AsyncSession, *, dry_run: bool) -> None:
	stmt = (
		select(Instrument)
		.where(Instrument.instrument_key == INSTRUMENT_KEY)
		.where(Instrument.is_active.is_(True))
		.order_by(Instrument.created_at.desc())
		.limit(1)
	)
	row = (await session.execute(stmt)).scalar_one_or_none()
	if row is None:
		print(
			"No active 'yee' instrument row found. Nothing to sync — a fresh "
			"bootstrap will seed the already-current canonical snapshot."
		)
		return

	print(f"Active instrument: id={row.id} version={row.instrument_version!r}")

	target = _canonical_snapshot()

	if row.content == target:
		print("Active instrument content already matches the snapshot. No changes.")
		return

	changed_keys = _summarize_changes(row.content, target)
	print(f"{len(changed_keys)} top-level field(s) to update: {', '.join(changed_keys)}")

	if dry_run:
		print("Dry run — no changes written.")
		return

	row.content = target
	flag_modified(row, "content")
	await session.commit()
	print("Done. Active instrument content synced to snapshot in place (same version).")


def _normalize_async_url(raw_url: str):
	"""Mirror app.database URL normalization for asyncpg (see add_test_auditors)."""

	normalized = raw_url.strip()
	if normalized.startswith("postgres://"):
		normalized = normalized.replace("postgres://", "postgresql://", 1)

	url = make_url(normalized)
	if url.drivername == "postgresql":
		url = url.set(drivername="postgresql+asyncpg")

	query = dict(url.query)
	sslmode = query.pop("sslmode", None)
	query.pop("channel_binding", None)

	connect_args: dict[str, object] = {}
	if isinstance(sslmode, str) and sslmode.lower() in {"require", "verify-ca", "verify-full"}:
		connect_args["ssl"] = True
		connect_args["statement_cache_size"] = 0

	return url.set(query=query), connect_args


def _resolve_target_url():
	load_dotenv(find_dotenv())
	raw_url = os.getenv("DATABASE_URL_YEE", "").strip()
	if not raw_url:
		raise SystemExit("DATABASE_URL_YEE is not set in your environment / .env file.")
	return _normalize_async_url(raw_url)


async def _main_async(url, connect_args, *, dry_run: bool) -> None:
	engine = create_async_engine(url, connect_args=connect_args, pool_pre_ping=True)
	session_factory = async_sessionmaker(bind=engine, expire_on_commit=False)
	try:
		async with session_factory() as session:
			await _sync_active_instrument(session, dry_run=dry_run)
	finally:
		await engine.dispose()


def main() -> None:
	parser = argparse.ArgumentParser(
		description="Sync the active YEE instrument row to the committed snapshot in place."
	)
	parser.add_argument("--yes", action="store_true", help="Skip the confirmation prompt.")
	parser.add_argument("--dry-run", action="store_true", help="Show what would change without writing.")
	args = parser.parse_args()

	url, connect_args = _resolve_target_url()
	print(f"Target DB: {url.host}/{url.database}")

	if not args.dry_run and not args.yes:
		answer = input("This writes to the database shown above. Continue? [y/N] ").strip().lower()
		if answer not in {"y", "yes"}:
			print("Aborted.")
			sys.exit(1)

	asyncio.run(_main_async(url, connect_args, dry_run=args.dry_run))


if __name__ == "__main__":
	main()

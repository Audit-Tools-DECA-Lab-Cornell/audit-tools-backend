"""Publish the on-disk canonical Playspace instrument to a database and activate it.

Deploying backend code does not move the instrument. The ``/playspace/instrument``
and ``/playspace/instruments/active/{key}`` routes serve the active ``instruments``
row and fall back to the on-disk canonical JSON only when no row exists at all, so
a database whose active row predates a shipped instrument keeps serving the old
definition to every client indefinitely. Clients render whatever the server sends,
which is how a stale row shows up as a missing feature rather than an error: a
Sociability scale published before 5.32 arrives without ``selection_mode``, and the
mobile app falls back to single-select.

This script closes that gap. It reads ``pvua_v5_2.active.instrument.json``, compares
it against the target database's active row ignoring version stamps, and publishes it
as a new active version when they differ. Re-running once the content matches is a
no-op.

Note that publication numbers are assigned by the server, not by the file: activating
always takes one above the highest existing publication, and the version embedded in
the content is rewritten to match. A 5.33 file published into a database whose highest
version is 5.31 therefore becomes 5.32. The script prints the resulting number before
it writes anything.

Usage::

    python scripts/publish_active_instrument_from_file.py --dry-run
    python scripts/publish_active_instrument_from_file.py

The database URL is read from the same env vars as the sync tool
(``PLAYSPACE_INSTRUMENT_SYNC_DATABASE_URL`` / ``DATABASE_URL_PLAYSPACE`` /
``DEV_DATABASE_URL_PLAYSPACE``) or ``--database-url``.

After publishing, regenerate the on-disk canonical JSON exports with
``scripts/sync_canonical_instruments_from_db.py`` so the files match the database
again -- the published version number will differ from the file's when the target
database was more than one publication behind.
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Repo root: audit-tools-backend/
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(_REPO_ROOT))

# Sibling scripts (scripts/ is on sys.path[0] when this file is run directly).
from sync_canonical_instruments_from_db import (  # noqa: E402
	_env_database_url,
	_normalize_postgres_url,
)

from app.products.playspace.instrument import (  # noqa: E402
	INSTRUMENT_KEY,
	get_active_instrument_content,
	get_active_instrument_version,
)
from app.products.playspace.schemas.management import InstrumentCreateRequest  # noqa: E402
from app.products.playspace.services.instrument import (  # noqa: E402
	InstrumentValidationError,
	create_instrument_version,
	get_active_instrument,
	list_instrument_versions,
	next_published_version,
)


def _localized_payloads(content: Any) -> list[dict[str, Any]]:
	"""Return every locale payload in an instrument content map."""

	if not isinstance(content, dict):
		return []
	if "instrument_key" in content:
		return [content]
	return [payload for payload in content.values() if isinstance(payload, dict)]


def _locale_keys(content: Any) -> set[str]:
	"""Return the locale keys in an instrument content map.

	A bare instrument object (one carrying ``instrument_key`` at the top level)
	is the un-wrapped English payload, matching how the service layer reads it.
	"""

	if not isinstance(content, dict):
		return set()
	if "instrument_key" in content:
		return {"en"}
	return {locale for locale, payload in content.items() if isinstance(payload, dict)}


def _without_version_stamps(content: Any) -> Any:
	"""Copy content with ``instrument_version`` removed from every locale payload.

	Publication rewrites the embedded version, so comparing content with the stamp
	still in place would report a difference for two otherwise identical payloads.
	"""

	stripped = copy.deepcopy(content)
	for payload in _localized_payloads(stripped):
		payload.pop("instrument_version", None)
	return stripped


def _count_multiselect_sociability(content: Any) -> int:
	"""Count Sociability scales declaring ``selection_mode: "multiple"``."""

	total = 0
	for payload in _localized_payloads(content):
		for section in payload.get("sections", []):
			if not isinstance(section, dict):
				continue
			for question in section.get("questions", []):
				if not isinstance(question, dict):
					continue
				for scale in question.get("scales", []):
					if not isinstance(scale, dict):
						continue
					if scale.get("key") == "sociability" and scale.get("selection_mode") == "multiple":
						total += 1
	return total


async def _amain() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Report what would change without publishing a new version.",
	)
	parser.add_argument(
		"--database-url",
		default=None,
		help="Override database URL (otherwise env PLAYSPACE / DATABASE_URL_PLAYSPACE).",
	)
	args = parser.parse_args()

	raw_url = args.database_url or _env_database_url()
	if not raw_url:
		print(
			"Missing database URL. Set PLAYSPACE_INSTRUMENT_SYNC_DATABASE_URL or DATABASE_URL_PLAYSPACE.",
			file=sys.stderr,
		)
		return 1

	file_version = get_active_instrument_version()
	desired_content: dict[str, Any] = get_active_instrument_content()

	url, connect_args = _normalize_postgres_url(raw_url)
	engine = create_async_engine(url, echo=False, pool_pre_ping=True, connect_args=connect_args)
	session_factory = async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

	try:
		async with session_factory() as session:
			rows = await list_instrument_versions(session, INSTRUMENT_KEY)
			published_versions = [row.instrument_version for row in rows if row.parent_instrument_id is None]
			active = await get_active_instrument(session, INSTRUMENT_KEY)

			print(f"On-disk canonical file: version {file_version}")
			print(f"  multi-select Sociability scales: {_count_multiselect_sociability(desired_content)}")
			print(f"  locales: {', '.join(sorted(_locale_keys(desired_content)))}")

			if active is None:
				# Publishing here would number the new row from an empty history
				# (1.0), and it is not needed anyway: with no active row the routes
				# already fall back to this exact file.
				print(
					f"No active instrument row for {INSTRUMENT_KEY!r} in this database. "
					"The API already falls back to the on-disk canonical file, so there is nothing to publish."
				)
				return 0

			print(f"Database active row:    version {active.instrument_version}")
			print(f"  multi-select Sociability scales: {_count_multiselect_sociability(active.content)}")
			print(f"  locales: {', '.join(sorted(_locale_keys(active.content)))}")

			if _without_version_stamps(active.content) == _without_version_stamps(desired_content):
				print("Database already serves the canonical content; nothing to publish.")
				return 0

			# Publication replaces content outright rather than merging, so a file
			# carrying fewer locales than the live row would drop the difference.
			# The script cannot invent the missing translations, so the operator
			# decides instead of losing them silently.
			dropped_locales = _locale_keys(active.content) - _locale_keys(desired_content)
			if dropped_locales:
				print(
					f"Refused to publish: the active row carries locale(s) "
					f"{', '.join(sorted(dropped_locales))} that the canonical file does not. "
					"Publishing would drop them. Re-export the file with "
					"scripts/sync_canonical_instruments_from_db.py, add the missing locales, and retry.",
					file=sys.stderr,
				)
				return 1

			resolved_version = next_published_version(published_versions) if published_versions else file_version
			print(
				f"Content differs. Publishing the file as a new active version: "
				f"{active.instrument_version} -> {resolved_version}."
			)
			if resolved_version != file_version:
				print(
					f"  Note: the published number ({resolved_version}) differs from the file's "
					f"({file_version}) because publication numbers follow this database's history."
				)

			if args.dry_run:
				print("[dry-run] No new version published.")
				return 0

			request = InstrumentCreateRequest(
				instrument_key=INSTRUMENT_KEY,
				instrument_version=file_version,
				content=desired_content,
			)
			try:
				created = await create_instrument_version(session, request, activate=True)
			except InstrumentValidationError as error:
				print(f"Refused to publish: {error}", file=sys.stderr)
				return 1

			if created is None:
				print("Failed to publish the canonical instrument version.", file=sys.stderr)
				return 1

			print(f"Published and activated instrument version {created.instrument_version}.")
			print("Previous versions are retained and deactivated, so existing submissions keep their own version.")
			print("Next: run scripts/sync_canonical_instruments_from_db.py to refresh the on-disk JSON exports.")
			return 0
	finally:
		await engine.dispose()


def main() -> None:
	raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
	main()

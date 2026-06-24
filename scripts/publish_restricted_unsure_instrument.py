"""Publish a restricted-Unsure instrument version to the Playspace database.

One-off operational script. It reads the active ``pvua_v5_2`` instrument from the
configured Playspace database, removes the Unsure option from every scale except the
Provision scale of the two diversity sections (Accommodating diverse abilities,
Playspace suitability for diverse users), and publishes the result as a new active
instrument version (e.g. ``5.30`` -> ``5.31``). The previous version is left intact
and simply deactivated, so existing submissions keep rendering against their own
stamped version.

Re-running once the fix is live is a no-op: if the active version already matches the
restricted shape, nothing is published.

Usage::

    python scripts/publish_restricted_unsure_instrument.py --dry-run
    python scripts/publish_restricted_unsure_instrument.py

The database URL is read from the same env vars as the sync tool
(``PLAYSPACE_INSTRUMENT_SYNC_DATABASE_URL`` / ``DATABASE_URL_PLAYSPACE`` /
``DEV_DATABASE_URL_PLAYSPACE``) or ``--database-url``.

After publishing, regenerate the on-disk canonical JSON exports with
``scripts/sync_canonical_instruments_from_db.py``.
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
from restrict_unsure_options import (  # noqa: E402
	_instrument_payloads,
	_is_unsure_option,
	restrict_unsure_options,
)
from sync_canonical_instruments_from_db import (  # noqa: E402
	_env_database_url,
	_normalize_postgres_url,
)

from app.products.playspace.instrument import INSTRUMENT_KEY  # noqa: E402
from app.products.playspace.schemas.management import InstrumentCreateRequest  # noqa: E402
from app.products.playspace.services.instrument import (  # noqa: E402
	create_instrument_version,
	get_active_instrument,
)


def _count_unsure(content: Any) -> int:
	"""Count Unsure options across every localized payload in an instrument content map."""

	total = 0
	for instrument in _instrument_payloads(content):
		for section in instrument.get("sections", []):
			if not isinstance(section, dict):
				continue
			for question in section.get("questions", []):
				if not isinstance(question, dict):
					continue
				for scale in question.get("scales", []):
					if not isinstance(scale, dict):
						continue
					options = scale.get("options")
					if isinstance(options, list):
						total += sum(1 for option in options if _is_unsure_option(option))
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

	url, connect_args = _normalize_postgres_url(raw_url)
	engine = create_async_engine(url, echo=False, pool_pre_ping=True, connect_args=connect_args)
	session_factory = async_sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

	try:
		async with session_factory() as session:
			active = await get_active_instrument(session, INSTRUMENT_KEY)
			if active is None:
				print(f"No active instrument found for key {INSTRUMENT_KEY!r}; nothing to publish.", file=sys.stderr)
				return 1

			before = _count_unsure(active.content)
			restricted_content = copy.deepcopy(active.content)
			scales_changed = restrict_unsure_options(restricted_content)
			after = _count_unsure(restricted_content)

			print(f"Active version {active.instrument_version}: {before} Unsure option(s) across all scales.")

			if scales_changed == 0:
				print("Active instrument already matches the restricted shape; nothing to publish.")
				return 0

			print(f"Restriction would change {scales_changed} scale(s), leaving {after} Unsure option(s).")

			if args.dry_run:
				print("[dry-run] No new version published.")
				return 0

			request = InstrumentCreateRequest(
				instrument_key=INSTRUMENT_KEY,
				instrument_version=active.instrument_version,
				content=restricted_content,
			)
			created = await create_instrument_version(session, request, activate=True)
			if created is None:
				print("Failed to publish the restricted instrument version.", file=sys.stderr)
				return 1

			print(f"Published and activated instrument version {created.instrument_version}.")
			print("Next: run scripts/sync_canonical_instruments_from_db.py to refresh the on-disk JSON exports.")
			return 0
	finally:
		await engine.dispose()


def main() -> None:
	raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
	main()

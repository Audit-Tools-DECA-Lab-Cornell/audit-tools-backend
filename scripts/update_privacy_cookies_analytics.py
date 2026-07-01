"""Update the Playspace Privacy Notice cookies/analytics disclosure in the database.

One-off operational script. It reads the active ``pvua_v5_2`` instrument from the
configured Playspace database and, inside ``content["en"]["legal_documents"]``, updates
the ``privacy`` document's ``cookies-analytics`` section so it discloses Microsoft
Clarity and Google Analytics exactly as the just-committed frontend copy does. The
document's ``last_updated`` is set to ``July 1, 2026``.

The change is applied *in place* to the active row, keeping the published version
(``5.31``) so the database stays aligned with the frontend, which is seeded from that
same version. The database is the source of truth; the on-disk canonical JSON exports
under ``app/products/playspace/instruments/`` are regenerated afterwards with
``scripts/sync_canonical_instruments_from_db.py``.

Re-running once the fix is live is a no-op: if the active row already matches the
target copy, nothing is written.

Usage::

    python scripts/update_privacy_cookies_analytics.py --dry-run
    python scripts/update_privacy_cookies_analytics.py

The database URL is read from the same env vars as the sync tool
(``PLAYSPACE_INSTRUMENT_SYNC_DATABASE_URL`` / ``DATABASE_URL_PLAYSPACE`` /
``DEV_DATABASE_URL_PLAYSPACE``) or ``--database-url``.

After running, regenerate the on-disk canonical JSON exports with
``scripts/sync_canonical_instruments_from_db.py``.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm.attributes import flag_modified

# Repo root: audit-tools-backend/
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
	sys.path.insert(0, str(_REPO_ROOT))

# Sibling scripts (scripts/ is on sys.path[0] when this file is run directly).
from sync_canonical_instruments_from_db import (  # noqa: E402
	_env_database_url,
	_normalize_postgres_url,
)

from app.products.playspace.instrument import INSTRUMENT_KEY  # noqa: E402
from app.products.playspace.services.instrument import get_active_instrument  # noqa: E402

# --- Target copy, verbatim from the frontend source of truth --------------------------
#
# playspace/copa-frontend/src/lib/resources/legal-documents.ts
#   -> the "privacy" document -> section with key "cookies-analytics".
# The strings below must match that file exactly; ``_assert_matches_frontend`` re-reads
# the frontend file at runtime and fails loudly if they have drifted.

DOC_KEY = "privacy"
SECTION_KEY = "cookies-analytics"
TARGET_LAST_UPDATED = "July 1, 2026"

TARGET_BODY: list[str] = [
	"The web platform and mobile app may use cookies, local storage, device storage, crash logs, diagnostics, or similar technologies to keep users signed in, remember preferences, support offline drafts, measure reliability, and protect the platform from abuse.",
	"To understand how the web dashboard is used and to improve it, we also use third-party analytics services. These may set cookies and process usage data such as pages viewed, actions taken, approximate location derived from your IP address, and device or browser details.",
	"If you are in the European Economic Area, the United Kingdom, or Switzerland, we show a consent banner and do not run non-essential analytics until you accept it. In other regions, analytics runs by default and you can turn it off using the banner. To change a previous choice, clear this site's stored data in your browser.",
	"Where required, the organization or platform operator may provide additional choices for non-essential cookies or analytics.",
]

TARGET_BULLETS: list[str] = [
	"Microsoft Clarity: captures how you use and interact with the dashboard through product-usage metrics, heatmaps, and session replay so we can improve it. This data is collected using first- and third-party cookies and similar technologies. To learn how Microsoft collects and uses it, see the Microsoft Privacy Statement (https://privacy.microsoft.com/privacystatement).",
	"Google Analytics 4: helps us measure traffic and understand how features are used. In the European Economic Area, the United Kingdom, and Switzerland it runs under Google Consent Mode, so analytics cookies are set only after you accept the consent banner. To learn how Google uses this data, see Google's Privacy Policy (https://policies.google.com/privacy).",
]

# Frontend file, relative to the workspace root (audit-tools-backend/ sits next to playspace/).
_FRONTEND_LEGAL_DOCS = (
	_REPO_ROOT.parent / "playspace" / "copa-frontend" / "src" / "lib" / "resources" / "legal-documents.ts"
)


def _assert_matches_frontend() -> None:
	"""Fail loudly if our target strings are not present verbatim in the frontend file.

	Guards against transcription drift: the frontend is the source of truth for the exact
	wording, so every target string (and the effective date) must appear in it literally.
	Skipped with a warning if the frontend file cannot be found (e.g. backend checked out
	on its own).
	"""

	if not _FRONTEND_LEGAL_DOCS.is_file():
		print(
			f"[warn] Frontend file not found at {_FRONTEND_LEGAL_DOCS}; skipping verbatim check.",
			file=sys.stderr,
		)
		return

	text = _FRONTEND_LEGAL_DOCS.read_text(encoding="utf-8")
	missing = [s for s in (*TARGET_BODY, *TARGET_BULLETS, TARGET_LAST_UPDATED) if s not in text]
	if missing:
		preview = "\n".join(f"  - {s[:80]}..." for s in missing)
		raise SystemExit(
			"Target copy does not match the frontend source of truth "
			f"({_FRONTEND_LEGAL_DOCS}). Missing strings:\n{preview}"
		)


def _find_localized_instrument(content: Any) -> dict[str, Any]:
	"""Return the ``en`` localized payload from an instrument content map."""

	if not isinstance(content, dict):
		raise SystemExit("Instrument content is not a JSON object; cannot locate legal documents.")
	localized = content.get("en")
	if not isinstance(localized, dict):
		raise SystemExit("Instrument content has no 'en' payload; cannot locate legal documents.")
	return localized


def _find_privacy_doc(localized: dict[str, Any]) -> dict[str, Any]:
	"""Return the privacy legal document dict (owner of ``last_updated``)."""

	legal_documents = localized.get("legal_documents")
	if not isinstance(legal_documents, list):
		raise SystemExit("Localized payload has no 'legal_documents' list.")
	privacy = next((d for d in legal_documents if isinstance(d, dict) and d.get("key") == DOC_KEY), None)
	if privacy is None:
		raise SystemExit(f"No legal document with key {DOC_KEY!r} found.")
	return privacy


def _find_section(privacy: dict[str, Any]) -> dict[str, Any]:
	"""Return the privacy document's cookies-analytics section dict."""

	sections = privacy.get("sections")
	if not isinstance(sections, list):
		raise SystemExit("Privacy document has no 'sections' list.")
	section = next((s for s in sections if isinstance(s, dict) and s.get("key") == SECTION_KEY), None)
	if section is None:
		raise SystemExit(f"No section with key {SECTION_KEY!r} found in the privacy document.")
	return section


async def _amain() -> int:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Report what would change without writing to the database.",
	)
	parser.add_argument(
		"--database-url",
		default=None,
		help="Override database URL (otherwise env PLAYSPACE / DATABASE_URL_PLAYSPACE).",
	)
	args = parser.parse_args()

	_assert_matches_frontend()

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
				print(f"No active instrument found for key {INSTRUMENT_KEY!r}; nothing to update.", file=sys.stderr)
				return 1

			print(f"Active instrument: key={active.instrument_key!r} version={active.instrument_version!r}")

			localized = _find_localized_instrument(active.content)
			privacy = _find_privacy_doc(localized)
			section = _find_section(privacy)

			current_body = section.get("body")
			current_bullets = section.get("bullets")
			current_last_updated = privacy.get("last_updated")

			body_changed = current_body != TARGET_BODY
			bullets_changed = current_bullets != TARGET_BULLETS
			date_changed = current_last_updated != TARGET_LAST_UPDATED

			if not (body_changed or bullets_changed or date_changed):
				print("Active instrument already matches the target Clarity/GA copy; nothing to update.")
				return 0

			print("Changes to apply:")
			if body_changed:
				print(f"  - section '{SECTION_KEY}' body: {len(current_body or [])} -> {len(TARGET_BODY)} paragraph(s)")
			if bullets_changed:
				print(
					f"  - section '{SECTION_KEY}' bullets: {len(current_bullets or [])} -> {len(TARGET_BULLETS)} bullet(s)"
				)
			if date_changed:
				print(f"  - privacy last_updated: {current_last_updated!r} -> {TARGET_LAST_UPDATED!r}")

			if args.dry_run:
				print("[dry-run] No database write performed.")
				return 0

			section["body"] = list(TARGET_BODY)
			section["bullets"] = list(TARGET_BULLETS)
			privacy["last_updated"] = TARGET_LAST_UPDATED
			# Nested mutation of a JSON/JSONB column: mark it dirty so SQLAlchemy flushes it.
			flag_modified(active, "content")

			await session.commit()
			print(
				f"Updated privacy cookies-analytics copy on version {active.instrument_version} (in place)."
			)
			print("Next: run scripts/sync_canonical_instruments_from_db.py to refresh the on-disk JSON exports.")
			return 0
	finally:
		await engine.dispose()


def main() -> None:
	raise SystemExit(asyncio.run(_amain()))


if __name__ == "__main__":
	main()

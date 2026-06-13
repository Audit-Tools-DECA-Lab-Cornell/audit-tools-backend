"""Scheduled job: email auditors whose intended submissions never completed.

The mobile app records a submit-intent beacon when an auditor taps submit. If
that submission never reaches SUBMITTED (the device stayed offline, the app was
removed, the background sync never ran), this job notices the stalled intent and
emails the auditor so the data is not silently lost.

Run from a scheduler (cron / Render cron job); the platform does not run it
automatically. Example:

    python -m app.products.playspace.jobs.stalled_submissions --stall-hours 6 --renotify-hours 24
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import timedelta

from app.database import ASYNC_SESSION_FACTORY_BY_PRODUCT, ProductKey
from app.products.playspace.services import PlayspaceAuditService

logger = logging.getLogger(__name__)

DEFAULT_STALL_HOURS = 6.0
DEFAULT_RENOTIFY_HOURS = 24.0
DEFAULT_LIMIT = 200


async def run_stalled_submission_sweep(
	*,
	stall_threshold: timedelta,
	renotify_after: timedelta,
	limit: int = DEFAULT_LIMIT,
) -> list[str]:
	"""Run one never-arrived sweep against the Playspace database.

	Returns the string audit ids notified this run.
	"""

	session_factory = ASYNC_SESSION_FACTORY_BY_PRODUCT[ProductKey.PLAYSPACE]
	async with session_factory() as session:
		service = PlayspaceAuditService(session)
		notified = await service.notify_stalled_submissions(
			stall_threshold=stall_threshold,
			renotify_after=renotify_after,
			limit=limit,
		)
	return [str(audit_id) for audit_id in notified]


def _parse_args() -> argparse.Namespace:
	parser = argparse.ArgumentParser(description="Notify auditors of stalled (never-arrived) audit submissions.")
	parser.add_argument(
		"--stall-hours",
		type=float,
		default=DEFAULT_STALL_HOURS,
		help="Hours since the submit-intent beacon before an audit is considered stalled.",
	)
	parser.add_argument(
		"--renotify-hours",
		type=float,
		default=DEFAULT_RENOTIFY_HOURS,
		help="Minimum hours between repeat notifications for the same audit.",
	)
	parser.add_argument(
		"--limit",
		type=int,
		default=DEFAULT_LIMIT,
		help="Maximum number of audits to notify in one run.",
	)
	return parser.parse_args()


def main() -> None:
	logging.basicConfig(level=logging.INFO)
	args = _parse_args()
	notified = asyncio.run(
		run_stalled_submission_sweep(
			stall_threshold=timedelta(hours=args.stall_hours),
			renotify_after=timedelta(hours=args.renotify_hours),
			limit=args.limit,
		)
	)
	logger.info("Stalled-submission sweep notified %d auditor(s): %s", len(notified), ", ".join(notified) or "none")


if __name__ == "__main__":
	main()

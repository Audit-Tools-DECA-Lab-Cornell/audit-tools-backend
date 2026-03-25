"""
CLI for backfilling Playspace canonical audit aggregates and parity checks.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid

from app.database import ASYNC_SESSION_FACTORY_BY_PRODUCT, ProductKey
from app.products.playspace.migration_tools import backfill_canonical_aggregates


def _parse_args() -> argparse.Namespace:
    """Parse CLI arguments for Playspace aggregate backfills."""

    parser = argparse.ArgumentParser(
        description="Backfill canonical Playspace audit aggregates and verify parity.",
    )
    parser.add_argument(
        "--audit-id",
        action="append",
        dest="audit_ids",
        default=[],
        help="Limit the run to one or more specific audit UUIDs.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute backfills and parity without committing database changes.",
    )
    parser.add_argument(
        "--fail-on-parity-mismatch",
        action="store_true",
        help="Exit with status 1 when any migrated audit fails parity checks.",
    )
    return parser.parse_args()


async def _run_async(arguments: argparse.Namespace) -> int:
    """Execute the backfill with one Playspace database session."""

    audit_ids = [uuid.UUID(raw_audit_id) for raw_audit_id in arguments.audit_ids]

    async with ASYNC_SESSION_FACTORY_BY_PRODUCT[ProductKey.PLAYSPACE]() as session:
        results = await backfill_canonical_aggregates(
            session,
            audit_ids=audit_ids,
            dry_run=arguments.dry_run,
        )

    mismatch_count = sum(1 for result in results if not result.is_matching)
    print(
        f"Processed {len(results)} Playspace audits. "
        f"Parity mismatches: {mismatch_count}. "
        f"Mode: {'dry-run' if arguments.dry_run else 'commit'}."
    )
    for result in results:
        status = "OK" if result.is_matching else "MISMATCH"
        print(
            f"{status} audit_id={result.audit_id} "
            f"schema_version={result.schema_version} revision={result.revision}"
        )

    if arguments.fail_on_parity_mismatch and mismatch_count > 0:
        return 1
    return 0


def main() -> None:
    """CLI entry point."""

    arguments = _parse_args()
    raise SystemExit(asyncio.run(_run_async(arguments)))


if __name__ == "__main__":
    main()

"""Generate the read-only Phase 3 YEE migration inventory for ONE product database.

Local operator tool. SELECT-only: it opens no transaction, writes nothing, and
is deliberately not exposed over HTTP.

``instruments`` is a shared table that exists in both physical databases, so a
YEE catalog row can live in either one. Both must be inventoried before the
mirrored integrity migrations (``yee_0010`` / ``ps_0012``) run, which is why
``--product`` is required rather than defaulted.

    python -m scripts.generate_yee_migration_manifest \
        --product yee \
        --environment-label prod-snapshot-2026-08-28 \
        --output ~/phase3/yee.manifest.json

The target database is resolved from the environment exactly as the application
resolves it. A database URL is never accepted as an argument and never printed.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from app.db_urls import ProductKey
from app.products.yee.schemas.migration import MigrationMappingDocument
from app.products.yee.services.migration_manifest import (
	ResolutionOverlayError,
	generate_migration_manifest,
)

#: Anything that could smuggle a connection string into the artifact.
_URL_MARKERS = ("://", "@", " ")


def _environment_label(value: str) -> str:
	label = value.strip()
	if not label:
		raise argparse.ArgumentTypeError("--environment-label cannot be blank.")
	if len(label) > 64:
		raise argparse.ArgumentTypeError("--environment-label must be 64 characters or fewer.")
	if any(marker in label for marker in _URL_MARKERS):
		raise argparse.ArgumentTypeError(
			"--environment-label must be a short non-secret name (for example "
			"'prod-snapshot-2026-08-28'), never a database URL or connection string."
		)
	return label


def _parse_args(argv: list[str]) -> argparse.Namespace:
	parser = argparse.ArgumentParser(
		prog="generate_yee_migration_manifest",
		description="Read-only YEE instrument migration inventory for one product database.",
	)
	parser.add_argument(
		"--product",
		required=True,
		choices=[product.value for product in ProductKey],
		help="Which physical product database to inventory. Required: the instruments table is shared.",
	)
	parser.add_argument(
		"--environment-label",
		required=True,
		type=_environment_label,
		help="Short non-secret label for the target, recorded in the artifact. Never a database URL.",
	)
	parser.add_argument(
		"--output",
		type=Path,
		default=None,
		help="Write the manifest here instead of stdout.",
	)
	parser.add_argument(
		"--resolution-overlay",
		type=Path,
		default=None,
		metavar="MAPPING_JSON",
		help=(
			"Apply a reviewed mapping document in memory and emit the authorization manifest. "
			"Still SELECT-only: no decision is written to the database by this tool."
		),
	)
	return parser.parse_args(argv)


async def _generate(
	product: ProductKey,
	environment_label: str,
	mapping: MigrationMappingDocument | None,
) -> dict[str, Any]:
	# Import after the product is parsed and validated so an argument error never
	# touches a database, and use the per-product accessor so an unset or invalid
	# URL for the OTHER product cannot block this inventory.
	import hashlib

	from app.database import ASYNC_ENGINE_BY_PRODUCT, get_database_url, get_session_factory
	from app.db_urls import describe_database_target

	session_factory = get_session_factory(product)
	engine = ASYNC_ENGINE_BY_PRODUCT[product]
	# Identity of the connection actually opened, derived from the redacted
	# "host/database" label so no credential reaches the artifact. Hashed rather
	# than embedded to keep infrastructure topology out of a shared file.
	target_fingerprint = hashlib.sha256(describe_database_target(get_database_url(product)).encode("utf-8")).hexdigest()
	try:
		async with session_factory() as session:
			return await generate_migration_manifest(
				session,
				product=product.value,
				environment_label=environment_label,
				target_fingerprint=target_fingerprint,
				mapping=mapping,
			)
	finally:
		# Dispose only the engine this run actually opened.
		await engine.dispose()


def _load_mapping(path: Path) -> MigrationMappingDocument:
	document = json.loads(path.expanduser().read_text(encoding="utf-8"))
	return MigrationMappingDocument.model_validate(document)


def main(argv: list[str] | None = None) -> int:
	args = _parse_args(sys.argv[1:] if argv is None else argv)
	product = ProductKey(args.product)
	mapping = None if args.resolution_overlay is None else _load_mapping(args.resolution_overlay)

	try:
		manifest = asyncio.run(_generate(product, args.environment_label, mapping))
	except ResolutionOverlayError as error:
		print(f"Resolution overlay refused: {error}", file=sys.stderr)
		return 2

	rendered = json.dumps(manifest, indent=2, sort_keys=True, default=str)

	if args.output is None:
		print(rendered)
	else:
		output_path: Path = args.output.expanduser()
		output_path.parent.mkdir(parents=True, exist_ok=True)
		output_path.write_text(f"{rendered}\n", encoding="utf-8")
		hashes = manifest["hashes"]
		print(f"Wrote {product.value} {manifest['mode']} manifest to {output_path}", file=sys.stderr)
		print(f"  migration_scope_sha256 (gate):     {hashes['migration_scope_sha256']}", file=sys.stderr)
		print(f"  full_payload_sha256    (evidence): {hashes['full_payload_sha256']}", file=sys.stderr)

	authorization = manifest.get("authorization")
	if authorization is not None:
		verdict = "AUTHORIZES APPLY" if authorization["authorizes_apply"] else "DOES NOT AUTHORIZE APPLY"
		print(f"  authorization: {verdict}", file=sys.stderr)
		print(f"    records_skipped:     {authorization['records_skipped']}", file=sys.stderr)
		print(f"    records_quarantined: {authorization['records_quarantined']}", file=sys.stderr)
		print(f"    unexplained differences: {not authorization['zero_unexplained_differences']}", file=sys.stderr)
		if not authorization["authorizes_apply"]:
			return 3
	return 0


if __name__ == "__main__":
	raise SystemExit(main())

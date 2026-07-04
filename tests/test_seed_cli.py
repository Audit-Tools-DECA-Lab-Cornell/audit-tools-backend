from __future__ import annotations

from app.seed import _parse_args


def test_seed_cli_defaults_to_test_and_reset() -> None:
	args = _parse_args([])

	assert args.environment == "test"
	assert args.product == "all"
	assert args.reset is True
	assert args.skip_migrate is False
	assert args.allow_destructive is False


def test_seed_cli_allows_non_reset_mode() -> None:
	args = _parse_args(["--no-reset", "--product", "yee", "--environment", "dev"])

	assert args.reset is False
	assert args.product == "yee"
	assert args.environment == "dev"

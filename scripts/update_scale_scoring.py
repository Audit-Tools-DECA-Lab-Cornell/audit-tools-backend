#!/usr/bin/env python3
"""Normalize addition_value and boost_value on every scale option in instrument JSON.

Scoring rules applied per scale (in option list order):

- Regular options receive ``addition_value`` 0, 1, 2, … and ``boost_value`` of
  ``addition_value + 1``.
- ``not_applicable`` / ``unsure`` options (by key or ``is_unsure`` flag) always
  receive ``addition_value`` ``0`` and ``boost_value`` ``1``.

The script is idempotent and supports plain instrument payloads and
locale-wrapped payloads such as ``{"en": {...}}``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

ZERO_SCORE_KEYS = frozenset({"not_applicable", "unsure"})


def _instrument_payloads(payload: Any) -> list[dict[str, Any]]:
	"""Return instrument objects inside a plain or locale-wrapped payload."""

	if not isinstance(payload, dict):
		return []
	if isinstance(payload.get("sections"), list):
		return [payload]
	return [value for value in payload.values() if isinstance(value, dict) and isinstance(value.get("sections"), list)]


def _is_zero_sum_option(option: dict[str, Any]) -> bool:
	"""Return whether an option uses the not-applicable / unsure sum score (0)."""

	if option.get("is_unsure") is True:
		return True
	key = option.get("key")
	return isinstance(key, str) and key in ZERO_SCORE_KEYS


def _normalize_scale_options(options: list[Any]) -> int:
	"""Apply scoring rules to one scale's options and return the number changed."""

	if not options:
		return 0

	changed = 0
	sum_score = 0
	for option in options:
		if not isinstance(option, dict):
			continue

		if _is_zero_sum_option(option):
			target_addition = 0
			target_boost = 1
		else:
			target_addition = sum_score
			target_boost = sum_score + 1
			sum_score += 1

		current_addition = option.get("addition_value")
		current_boost = option.get("boost_value")
		if current_addition != target_addition or current_boost != target_boost:
			option["addition_value"] = target_addition
			option["boost_value"] = target_boost
			changed += 1

	return changed


def update_scale_scoring(payload: Any) -> int:
	"""Normalize scoring values and return the number of options changed."""

	changed = 0
	for instrument in _instrument_payloads(payload):
		for section in instrument.get("sections", []):
			if not isinstance(section, dict):
				continue
			for question in section.get("questions", []):
				if not isinstance(question, dict):
					continue
				if question.get("question_type", "scaled") != "scaled":
					continue
				for scale in question.get("scales", []):
					if not isinstance(scale, dict):
						continue
					options = scale.get("options")
					if not isinstance(options, list):
						continue
					changed += _normalize_scale_options(options)
	return changed


def update_file(path: Path, *, dry_run: bool) -> int:
	"""Update one instrument JSON file and return the number of options changed."""

	payload = json.loads(path.read_text(encoding="utf-8"))
	changed = update_scale_scoring(payload)
	if changed > 0 and not dry_run:
		path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
	return changed


_DEFAULT_INSTRUMENTS_DIR = Path(__file__).resolve().parents[1] / "app" / "products" / "playspace" / "instruments"


def _managed_instrument_files(instruments_dir: Path) -> list[Path]:
	"""Return every canonical ``*.instrument.json`` file in the instruments directory."""

	return sorted(path for path in instruments_dir.glob("*.instrument.json") if path.is_file())


def main() -> None:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument(
		"paths",
		nargs="*",
		type=Path,
		help="Instrument JSON files to update (omit when using --all)",
	)
	parser.add_argument(
		"--all",
		action="store_true",
		help="Update every *.instrument.json file under app/products/playspace/instruments/",
	)
	parser.add_argument(
		"--dry-run",
		action="store_true",
		help="Report changes without writing files",
	)
	args = parser.parse_args()

	paths = list(args.paths)
	if args.all:
		paths = _managed_instrument_files(_DEFAULT_INSTRUMENTS_DIR)
	if not paths:
		parser.error("Provide one or more paths, or pass --all.")

	total_changed = 0
	for path in paths:
		changed = update_file(path, dry_run=args.dry_run)
		total_changed += changed
		action = "would update" if args.dry_run else "updated"
		print(f"{path}: {action} {changed} option(s)")
	print(f"Total options {('would be ' if args.dry_run else '')}updated: {total_changed}")


if __name__ == "__main__":
	main()

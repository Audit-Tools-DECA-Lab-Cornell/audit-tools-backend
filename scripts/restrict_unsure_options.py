#!/usr/bin/env python3
"""Restrict the Playspace Unsure option to the Provision scale of the diversity sections.

Auditors only need an "I don't know" answer on the Provision question of the two
diversity-focused sections (Accommodating diverse abilities, Playspace suitability
for diverse users). Selecting Unsure on Provision hides the follow-up scales via the
instrument's normal display logic, so the option must not appear on Variety, Challenge
or Sociability - nor on any other section's questions.

This script makes the on-disk instrument match that rule:

- Provision scales in the target sections keep (or receive) a single canonical Unsure
  option.
- Every other scale has all Unsure options removed.

Admins can still add Unsure to additional Provision scales themselves from the
instrumentation editor when needed. The script is idempotent and supports both plain
instrument payloads and locale-wrapped payloads such as ``{"en": {...}}``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

# Sections where Provision may carry an Unsure option, keyed by stable section_key.
TARGET_SECTION_KEYS: frozenset[str] = frozenset(
	{
		"section_21_accommodating_diverse_abilities",
		"section_22_playspace_suitability_for_diverse_users",
	}
)

UNSURE_SCALE_KEY = "provision"

# Field order mirrors the existing scale options in the instrument JSON for clean diffs.
CANONICAL_UNSURE_OPTION: dict[str, Any] = {
	"key": "unsure",
	"label": "Unsure / I don't know",
	"is_unsure": True,
	"boost_value": 1,
	"addition_value": 0,
	"is_not_applicable": False,
	"allows_follow_up_scales": False,
}


def _instrument_payloads(payload: Any) -> list[dict[str, Any]]:
	"""Return instrument objects inside a plain or locale-wrapped payload."""

	if not isinstance(payload, dict):
		return []
	if isinstance(payload.get("sections"), list):
		return [payload]
	return [value for value in payload.values() if isinstance(value, dict) and isinstance(value.get("sections"), list)]


def _is_unsure_option(option: Any) -> bool:
	"""Return whether an option is the Unsure / I-don't-know answer."""

	return isinstance(option, dict) and (option.get("is_unsure") is True or option.get("key") == "unsure")


def _strip_unsure(options: list[Any]) -> int:
	"""Remove every Unsure option in place and return how many were removed."""

	removed = [option for option in options if _is_unsure_option(option)]
	for option in removed:
		options.remove(option)
	return len(removed)


def _ensure_single_unsure(options: list[Any]) -> int:
	"""Guarantee exactly one canonical Unsure option at the end; return options changed."""

	existing = [option for option in options if _is_unsure_option(option)]
	if len(existing) == 1 and existing[0] == CANONICAL_UNSURE_OPTION and options[-1] is existing[0]:
		return 0

	for option in existing:
		options.remove(option)
	options.append(dict(CANONICAL_UNSURE_OPTION))
	return 1


def restrict_unsure_options(payload: Any) -> int:
	"""Apply the Provision-only Unsure rule and return the number of scales changed."""

	changed = 0
	for instrument in _instrument_payloads(payload):
		for section in instrument.get("sections", []):
			if not isinstance(section, dict):
				continue
			section_key = section.get("section_key")
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
					allowed = section_key in TARGET_SECTION_KEYS and scale.get("key") == UNSURE_SCALE_KEY
					if allowed:
						if _ensure_single_unsure(options):
							changed += 1
					elif _strip_unsure(options):
						changed += 1
	return changed


def update_file(path: Path, *, dry_run: bool) -> int:
	"""Update one instrument JSON file in place and return the number of scales changed."""

	payload = json.loads(path.read_text(encoding="utf-8"))
	changed = restrict_unsure_options(payload)
	if changed > 0 and not dry_run:
		path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
	return changed


def main() -> None:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("paths", nargs="+", type=Path, help="Instrument JSON files to update")
	parser.add_argument("--dry-run", action="store_true", help="Report changes without writing files")
	args = parser.parse_args()

	total_changed = 0
	for path in args.paths:
		changed = update_file(path, dry_run=args.dry_run)
		total_changed += changed
		action = "would restrict" if args.dry_run else "restricted"
		print(f"{path}: {action} Unsure on {changed} scale(s)")
	print(f"Total scales {('would be ' if args.dry_run else '')}changed: {total_changed}")


if __name__ == "__main__":
	main()

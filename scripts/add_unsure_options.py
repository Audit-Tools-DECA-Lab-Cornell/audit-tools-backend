#!/usr/bin/env python3
"""Append the Playspace Unsure option to every scaled-question scale.

The script is idempotent and supports both plain instrument payloads and
locale-wrapped payloads such as {"en": {...}}.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

UNSURE_OPTION: dict[str, Any] = {
	"key": "unsure",
	"label": "Unsure / I don't know",
	"addition_value": 0,
	"boost_value": 1,
	"allows_follow_up_scales": False,
	"is_not_applicable": False,
	"is_unsure": True,
}


def _instrument_payloads(payload: Any) -> list[dict[str, Any]]:
	"""Return instrument objects inside a plain or locale-wrapped payload."""

	if not isinstance(payload, dict):
		return []
	if isinstance(payload.get("sections"), list):
		return [payload]
	return [value for value in payload.values() if isinstance(value, dict) and isinstance(value.get("sections"), list)]


def add_unsure_options(payload: Any) -> int:
	"""Append Unsure options and return the number of scales changed."""

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
					if any(isinstance(option, dict) and option.get("is_unsure") is True for option in options):
						continue
					options.append(dict(UNSURE_OPTION))
					changed += 1
	return changed


def update_file(path: Path) -> int:
	"""Update one instrument JSON file in place."""

	payload = json.loads(path.read_text(encoding="utf-8"))
	changed = add_unsure_options(payload)
	if changed > 0:
		path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
	return changed


def main() -> None:
	parser = argparse.ArgumentParser(description=__doc__)
	parser.add_argument("paths", nargs="+", type=Path)
	args = parser.parse_args()

	for path in args.paths:
		changed = update_file(path)
		print(f"{path}: appended Unsure option to {changed} scale(s)")


if __name__ == "__main__":
	main()

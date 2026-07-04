"""Scoring compatibility contract for the YEE instrument.

The YEE scoring engine reads a fixed set of item ids, matrix choice ids, and
answer scales from ``scoring_spec.ITEM_SPECS`` — it never reads the instrument
JSON. Admins can now edit and publish instrument versions, so a published
version must stay a superset of the ids the engine looks up; otherwise scored
questions silently drop to zero.

This module derives the required id set from ``ITEM_SPECS`` (so the contract can
never drift from the engine) and checks a candidate instrument content against
it. Wording/order edits stay compatible; dropping or renaming a scored question
does not.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.products.yee.schemas.instrument import ScoringCompatibilityReport
from app.products.yee.services.scoring_spec import (
	ITEM_SPECS,
	SCORING_VERSION,
	PairedItemSpec,
	PresenceItemSpec,
)


def required_scoring_items() -> dict[str, set[str]]:
	"""Map each scored ``item_id`` to the matrix ``choice_id``s the engine reads.

	Derived from ``ITEM_SPECS`` so the contract is always in lockstep with the
	scoring engine.
	"""

	required: dict[str, set[str]] = {}
	for spec in ITEM_SPECS:
		if isinstance(spec, PairedItemSpec):
			required.setdefault(spec.presence_item_id, set()).add(spec.choice_id)
			required.setdefault(spec.condition_item_id, set()).add(spec.choice_id)
		elif isinstance(spec, PresenceItemSpec):
			required.setdefault(spec.item_id, set()).add(spec.choice_id)
	return required


def _index_scoring_items(content: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
	items = content.get("scoring_items")
	indexed: dict[str, Mapping[str, Any]] = {}
	if isinstance(items, list):
		for item in items:
			if isinstance(item, Mapping):
				item_id = item.get("item_id")
				if isinstance(item_id, str):
					indexed[item_id] = item
	return indexed


def validate_scoring_compatibility(content: Mapping[str, Any] | None) -> ScoringCompatibilityReport:
	"""Check candidate instrument content against the active scoring contract.

	``ok`` is driven by whole-question coverage: every scored ``item_id`` the
	engine reads must be present. Missing matrix choices are surfaced as a
	warning (``missing_choices``) but do not block publishing, because choice-key
	drift is a subtler edit than dropping a question and the whole-question check
	already guards the dominant failure mode.
	"""

	required = required_scoring_items()
	indexed = _index_scoring_items(content) if isinstance(content, Mapping) else {}

	missing_items = sorted(item_id for item_id in required if item_id not in indexed)

	missing_choices: list[str] = []
	for item_id, choice_ids in required.items():
		item = indexed.get(item_id)
		if item is None:
			continue
		choices = item.get("choices")
		if isinstance(choices, Mapping) and choices:
			missing_choices.extend(
				f"{item_id}:{choice_id}" for choice_id in sorted(choice_ids) if choice_id not in choices
			)

	present_item_count = sum(1 for item_id in required if item_id in indexed)
	return ScoringCompatibilityReport(
		ok=not missing_items,
		scoring_version=SCORING_VERSION,
		required_item_count=len(required),
		present_item_count=present_item_count,
		missing_items=missing_items,
		missing_choices=sorted(missing_choices),
	)

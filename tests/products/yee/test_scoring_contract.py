"""Unit tests for the YEE scoring-compatibility contract.

Pure function tests (no DB): the contract is derived from ``ITEM_SPECS`` and
checked against instrument content, guarding the seam between the now-editable
instrument and the hardcoded scoring engine.
"""

from __future__ import annotations

from app.products.yee.services.scoring import get_yee_instrument_data
from app.products.yee.services.scoring_contract import (
	required_scoring_items,
	validate_scoring_compatibility,
)
from app.products.yee.services.scoring_spec import SCORING_VERSION
from app.yee_instrument_schema import YeeInstrumentResponse


def _canonical_content() -> dict:
	return YeeInstrumentResponse.model_validate(get_yee_instrument_data()).model_dump()


def test_required_items_derive_from_spec() -> None:
	required = required_scoring_items()
	# A representative slice of the paired + presence ids the engine reads.
	assert "QID1#1" in required
	assert "QID1#2" in required
	assert "QID11#1" in required
	assert required["QID1#1"], "paired presence item must declare at least one choice id"


def test_canonical_instrument_is_scoring_compatible() -> None:
	"""The seeded/published instrument must always be fully scoreable."""

	report = validate_scoring_compatibility(_canonical_content())
	assert report.ok is True, report.missing_items
	assert report.missing_items == []
	assert report.scoring_version == SCORING_VERSION
	assert report.present_item_count == report.required_item_count


def test_empty_scoring_items_is_incompatible() -> None:
	report = validate_scoring_compatibility({"survey_name": "x", "version": "1", "scoring_items": []})
	assert report.ok is False
	assert report.present_item_count == 0
	assert len(report.missing_items) == report.required_item_count


def test_none_content_is_incompatible() -> None:
	report = validate_scoring_compatibility(None)
	assert report.ok is False
	assert report.missing_items


def test_dropping_one_scored_question_is_detected() -> None:
	content = _canonical_content()
	dropped = "QID1#1"
	content["scoring_items"] = [item for item in content["scoring_items"] if item.get("item_id") != dropped]
	report = validate_scoring_compatibility(content)
	assert report.ok is False
	assert dropped in report.missing_items


def test_wording_only_edit_stays_compatible() -> None:
	"""Editing question text (the admin's main use case) never breaks scoring."""

	content = _canonical_content()
	for item in content["scoring_items"]:
		item["question_text"] = f"Reworded: {item.get('question_text', '')}"
	report = validate_scoring_compatibility(content)
	assert report.ok is True
	assert report.missing_items == []

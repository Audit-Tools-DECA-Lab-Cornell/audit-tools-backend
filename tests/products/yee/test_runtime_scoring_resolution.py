from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument
from app.products.yee.services.runtime_scoring import (
	InstrumentStamp,
	RuntimeScorer,
	RuntimeScoringResolutionError,
)
from app.products.yee.services.scoring import get_yee_instrument_data
from app.products.yee.services.scoring_spec import SCHEMA_V1_SCORING_CONTRACT


class _ScalarRows:
	def __init__(self, rows: list[Instrument]) -> None:
		self._rows = rows

	def all(self) -> list[Instrument]:
		return self._rows


class _Result:
	def __init__(self, rows: list[Instrument]) -> None:
		self._rows = rows

	def scalars(self) -> _ScalarRows:
		return _ScalarRows(self._rows)


def _instrument(version: str, *, active: bool = False) -> Instrument:
	return Instrument(
		id=uuid.uuid4(),
		instrument_key="yee",
		instrument_version=version,
		is_active=active,
		content=get_yee_instrument_data(),
	)


def _v2_instrument(version: str) -> Instrument:
	return Instrument(
		id=uuid.uuid4(),
		instrument_key="yee",
		instrument_version=version,
		is_active=False,
		content={
			"survey_name": "Versioned runtime scoring",
			"version": version,
			"scoring_items": [],
			"authoring": {
				"schemaVersion": 2,
				"sections": [
					{
						"id": "access",
						"title": "Access",
						"instructions": "",
						"commentPrompt": "",
						"questions": [
							{
								"id": "access.custom",
								"prompt": "Custom",
								"primary": {
									"type": "single_select",
									"options": [{"id": "bonus", "label": "Bonus", "score": 9}],
								},
								"followUp": None,
								"scoring": {"method": "option_score", "domain": "access"},
								"responseBinding": {
									"presenceItemId": "CUSTOM#1",
									"choiceId": "choice",
									"conditionItemId": None,
								},
							}
						],
					}
				],
			},
		},
	)


def test_unstamped_records_use_frozen_schema_v1_without_a_query() -> None:
	session = AsyncMock(spec=AsyncSession)
	scorer = RuntimeScorer(session)

	contract = asyncio.run(scorer.contract_for_stamp(InstrumentStamp(None, None)))

	assert contract is SCHEMA_V1_SCORING_CONTRACT
	session.execute.assert_not_awaited()


def test_exact_inactive_instrument_version_is_resolved() -> None:
	row = _instrument("historical", active=False)
	session = AsyncMock(spec=AsyncSession)
	session.execute.return_value = _Result([row])

	contract = asyncio.run(RuntimeScorer(session).contract_for_stamp(InstrumentStamp("yee", "historical")))

	assert contract is SCHEMA_V1_SCORING_CONTRACT
	session.execute.assert_awaited_once()


def test_partial_stamp_fails_without_legacy_or_active_fallback() -> None:
	session = AsyncMock(spec=AsyncSession)

	with pytest.raises(RuntimeScoringResolutionError) as raised:
		asyncio.run(RuntimeScorer(session).contract_for_stamp(InstrumentStamp("yee", None)))

	assert raised.value.detail["code"] == "partial_instrument_stamp"
	session.execute.assert_not_awaited()


def test_missing_stamped_version_does_not_substitute_an_active_row() -> None:
	session = AsyncMock(spec=AsyncSession)
	session.execute.return_value = _Result([])

	with pytest.raises(RuntimeScoringResolutionError) as raised:
		asyncio.run(RuntimeScorer(session).contract_for_stamp(InstrumentStamp("yee", "missing")))

	assert raised.value.detail["code"] == "missing_stamped_instrument"


def test_duplicate_exact_rows_fail_visibly() -> None:
	rows = [_instrument("duplicate"), _instrument("duplicate")]
	session = AsyncMock(spec=AsyncSession)
	session.execute.return_value = _Result(rows)

	with pytest.raises(RuntimeScoringResolutionError) as raised:
		asyncio.run(RuntimeScorer(session).contract_for_stamp(InstrumentStamp("yee", "duplicate")))

	assert raised.value.detail["code"] == "duplicate_stamped_instrument"
	assert len(raised.value.detail["conflicts"]) == 2


def test_multiple_active_rows_fail_visibly() -> None:
	rows = [_instrument("one", active=True), _instrument("two", active=True)]
	session = AsyncMock(spec=AsyncSession)
	session.execute.return_value = _Result(rows)

	with pytest.raises(RuntimeScoringResolutionError) as raised:
		asyncio.run(RuntimeScorer(session).active_stamp_and_contract())

	assert raised.value.detail["code"] == "multiple_active_instruments"
	assert {row["instrument_version"] for row in raised.value.detail["conflicts"]} == {"one", "two"}


def test_stamped_authoring_v2_uses_version_owned_option_score() -> None:
	session = AsyncMock(spec=AsyncSession)
	session.execute.return_value = _Result([_v2_instrument("v2-score")])
	scorer = RuntimeScorer(session)

	score = asyncio.run(
		scorer.score_for_stamp(
			InstrumentStamp("yee", "v2-score"),
			{"CUSTOM#1": {"choice": "bonus"}},
		)
	)

	assert score["total_score"] == 9
	assert score["canonical_score"]["raw"]["item_scores"] == {"access.custom": 9}

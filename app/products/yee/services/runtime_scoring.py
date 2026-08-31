from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, cast

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument
from app.products.yee.services.scoring import get_yee_instrument_data, score_yee_responses
from app.products.yee.services.scoring_resolution import (
	ScoringContractResolutionError,
	scoring_contract_from_instrument,
)
from app.products.yee.services.scoring_spec import SCHEMA_V1_SCORING_CONTRACT, ScoringContract
from app.products.yee.services.scoring_types import JsonValue, LegacyScoreResult
from app.yee_instrument_schema import YeeInstrumentResponse


@dataclass(frozen=True, slots=True)
class InstrumentStamp:
	instrument_key: str | None
	instrument_version: str | None

	@property
	def is_unstamped(self) -> bool:
		return self.instrument_key is None and self.instrument_version is None


class RuntimeScoringResolutionError(HTTPException):
	def __init__(
		self,
		code: str,
		message: str,
		*,
		stamp: InstrumentStamp,
		conflicts: list[Instrument] | None = None,
	) -> None:
		detail: dict[str, Any] = {
			"code": code,
			"message": message,
			"instrument_key": stamp.instrument_key,
			"instrument_version": stamp.instrument_version,
		}
		if conflicts:
			detail["conflicts"] = [
				{
					"id": str(row.id),
					"instrument_key": row.instrument_key,
					"instrument_version": row.instrument_version,
				}
				for row in conflicts
			]
		super().__init__(status_code=409, detail=detail)


class RuntimeScorer:
	def __init__(self, session: AsyncSession) -> None:
		self._session = session
		self._contracts: dict[tuple[str, str], ScoringContract] = {}

	async def _row_for_stamp(self, stamp: InstrumentStamp) -> Instrument:
		"""The single catalog row a stamp names.

		One lookup shared by every stamped read, so a submission can never be
		scored against one row and validated against another.
		"""

		if stamp.instrument_key is None or stamp.instrument_version is None:
			raise RuntimeScoringResolutionError(
				"partial_instrument_stamp",
				"Historical instrument stamps must contain both key and version.",
				stamp=stamp,
			)
		rows = list(
			(
				await self._session.execute(
					select(Instrument).where(
						Instrument.instrument_key == stamp.instrument_key,
						Instrument.instrument_version == stamp.instrument_version,
					)
				)
			)
			.scalars()
			.all()
		)
		if not rows:
			raise RuntimeScoringResolutionError(
				"missing_stamped_instrument",
				"The instrument version stamped on this audit is unavailable.",
				stamp=stamp,
			)
		if len(rows) > 1:
			raise RuntimeScoringResolutionError(
				"duplicate_stamped_instrument",
				"More than one instrument row matches this audit stamp.",
				stamp=stamp,
				conflicts=rows,
			)
		return rows[0]

	async def contract_for_stamp(self, stamp: InstrumentStamp) -> ScoringContract:
		if stamp.is_unstamped:
			return SCHEMA_V1_SCORING_CONTRACT
		if stamp.instrument_key is None or stamp.instrument_version is None:
			raise RuntimeScoringResolutionError(
				"partial_instrument_stamp",
				"Historical instrument stamps must contain both key and version.",
				stamp=stamp,
			)
		cache_key = (stamp.instrument_key, stamp.instrument_version)
		cached = self._contracts.get(cache_key)
		if cached is not None:
			return cached
		contract = self._contract_from_row(await self._row_for_stamp(stamp), stamp)
		self._contracts[cache_key] = contract
		return contract

	async def active_stamp_and_contract(self, instrument_key: str = "yee") -> tuple[InstrumentStamp, ScoringContract]:
		rows = list(
			(
				await self._session.execute(
					select(Instrument).where(
						Instrument.instrument_key == instrument_key,
						Instrument.is_active.is_(True),
					)
				)
			)
			.scalars()
			.all()
		)
		stamp = InstrumentStamp(instrument_key, None)
		if not rows:
			raise RuntimeScoringResolutionError(
				"missing_active_instrument",
				"No active instrument is available for new audit work.",
				stamp=stamp,
			)
		if len(rows) > 1:
			raise RuntimeScoringResolutionError(
				"multiple_active_instruments",
				"More than one active instrument exists for new audit work.",
				stamp=stamp,
				conflicts=rows,
			)
		row = rows[0]
		resolved_stamp = InstrumentStamp(row.instrument_key, row.instrument_version)
		cache_key = (row.instrument_key, row.instrument_version)
		contract = self._contracts.get(cache_key) or self._contract_from_row(row, resolved_stamp)
		self._contracts[cache_key] = contract
		return resolved_stamp, contract

	async def score_for_stamp(
		self,
		stamp: InstrumentStamp,
		responses: Mapping[str, JsonValue],
		participant_info: Mapping[str, JsonValue] | None = None,
	) -> LegacyScoreResult:
		contract = await self.contract_for_stamp(stamp)
		return score_yee_responses(responses, participant_info, contract=contract)

	async def content_for_stamp(self, stamp: InstrumentStamp) -> YeeInstrumentResponse:
		"""The instrument DOCUMENT for a stamp, for checks scoring cannot answer.

		``contract_for_stamp`` returns only the scoring spec; logical
		completeness needs the authoring view (triggers, requiredness, bindings).
		Both resolve through the same stamp rules so a submission can never be
		scored against one version and validated against another.

		An unstamped legacy record resolves to the frozen schema-v1 snapshot,
		matching the scoring fallback.
		"""

		if stamp.is_unstamped:
			return YeeInstrumentResponse.model_validate(get_yee_instrument_data())
		row = await self._row_for_stamp(stamp)
		try:
			return YeeInstrumentResponse.model_validate(row.content)
		except ValidationError as exc:
			raise RuntimeScoringResolutionError(
				"invalid_stamped_instrument",
				"The instrument version stamped on this audit cannot be read.",
				stamp=stamp,
			) from exc

	@staticmethod
	def _contract_from_row(row: Instrument, stamp: InstrumentStamp) -> ScoringContract:
		try:
			content = cast(Mapping[str, JsonValue], row.content)
			return scoring_contract_from_instrument(content)
		except ScoringContractResolutionError as exc:
			raise RuntimeScoringResolutionError(
				"invalid_stamped_instrument",
				str(exc),
				stamp=stamp,
			) from exc

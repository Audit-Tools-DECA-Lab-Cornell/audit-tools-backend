from __future__ import annotations

import json
from collections.abc import Mapping
from functools import lru_cache
from pathlib import Path

from app.products.yee.services.scoring_engine import score_yee_responses_with_participant_info
from app.products.yee.services.scoring_resolution import (
	ScoringContractResolutionError,
	scoring_contract_from_instrument,
)
from app.products.yee.services.scoring_spec import SCHEMA_V1_SCORING_CONTRACT, ScoringContract
from app.products.yee.services.scoring_types import JsonMap, JsonValue, LegacyScoreResult

__all__ = [
	"ScoringContractResolutionError",
	"get_yee_instrument_data",
	"score_yee_responses",
	"scoring_contract_from_instrument",
]

YEE_INSTRUMENT_SNAPSHOT_PATH = Path(__file__).resolve().parents[1] / "instruments" / "yee.active.instrument.json"
TOTAL_CATEGORY_NAME = "Score"


def score_yee_responses(
	responses: Mapping[str, JsonValue],
	participant_info: Mapping[str, JsonValue] | None = None,
	*,
	contract: ScoringContract = SCHEMA_V1_SCORING_CONTRACT,
) -> LegacyScoreResult:
	return score_yee_responses_with_participant_info(
		responses,
		participant_info or {},
		contract=contract,
	)


@lru_cache(maxsize=1)
def get_yee_instrument_data() -> JsonMap:
	"""Return the committed seed/bootstrap YEE instrument snapshot."""

	with YEE_INSTRUMENT_SNAPSHOT_PATH.open("r", encoding="utf-8") as snapshot_file:
		return json.load(snapshot_file)

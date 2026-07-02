from __future__ import annotations

from typing import Any

from app.yee_scoring import get_yee_instrument_data as _get_yee_instrument_data
from app.products.yee.services.scoring_engine import score_yee_responses_with_participant_info
from app.products.yee.services.scoring_types import LegacyScoreResult

__all__ = ["get_yee_instrument_data", "score_yee_responses"]


def score_yee_responses(
	responses: dict[str, Any],
	participant_info: dict[str, Any] | None = None,
) -> LegacyScoreResult:
	return score_yee_responses_with_participant_info(responses, participant_info or {})


def get_yee_instrument_data() -> dict[str, Any]:
	"""Return the canonical YEE instrument data extracted from the QSF source."""

	return _get_yee_instrument_data()

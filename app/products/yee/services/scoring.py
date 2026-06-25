"""YEE scoring service.

Product-scoped entry point that wraps the existing scoring implementation in
`app/yee_scoring.py` without changing score semantics. Re-exported here so
product code depends on `app.products.yee.services.scoring` rather than the
top-level module; the underlying logic is migrated under this package in a
later slice.
"""

from __future__ import annotations

from typing import Any

from app.yee_scoring import get_yee_instrument_data as _get_yee_instrument_data
from app.yee_scoring import score_yee_responses as _score_yee_responses

__all__ = ["get_yee_instrument_data", "score_yee_responses"]


def score_yee_responses(responses: dict[str, Any]) -> dict[str, Any]:
	"""Score a YEE response map (total/section/category scores, match count).

	Typed boundary over the implementation in ``app.yee_scoring`` so product code
	gets a concrete ``dict[str, Any]`` result instead of an untyped value.
	"""

	return _score_yee_responses(responses)


def get_yee_instrument_data() -> dict[str, Any]:
	"""Return the canonical YEE instrument data extracted from the QSF source."""

	return _get_yee_instrument_data()

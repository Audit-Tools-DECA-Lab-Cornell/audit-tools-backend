"""YEE instrument loading and scoring utilities.

The YEE instrument content is served from a single committed snapshot at
``app/products/yee/instruments/yee.active.instrument.json``. At runtime the
active database row is authoritative (see
``app.products.yee.services.instrument``); this snapshot is only the
seed/bootstrap and last-resort fallback source used when no row exists yet.

The snapshot was migrated from the original Qualtrics QSF export (see git
history for ``app/data/yee_instrument.qsf``); the QSF is no longer a runtime
dependency. Scoring is decoupled from this content entirely — it is driven by
``app.products.yee.services.scoring_spec`` keyed on answer IDs, not text.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.products.yee.services.scoring_types import LegacyScoreResult

YEE_INSTRUMENT_SNAPSHOT_PATH = (
	Path(__file__).resolve().parent / "products" / "yee" / "instruments" / "yee.active.instrument.json"
)
TOTAL_CATEGORY_NAME = "Score"


@lru_cache(maxsize=1)
def get_yee_instrument_data() -> dict[str, object]:
	"""Return the canonical YEE instrument content from the committed snapshot.

	Seed/bootstrap + fallback source only. The served instrument is the active
	database row; this backfills a fresh environment that has no row yet.
	"""

	with YEE_INSTRUMENT_SNAPSHOT_PATH.open("r", encoding="utf-8") as f:
		return json.load(f)


def score_yee_responses(
	responses: dict[str, Any],
	participant_info: dict[str, Any] | None = None,
) -> LegacyScoreResult:
	from app.products.yee.services.scoring_engine import score_yee_responses_with_participant_info

	return score_yee_responses_with_participant_info(responses, participant_info or {})

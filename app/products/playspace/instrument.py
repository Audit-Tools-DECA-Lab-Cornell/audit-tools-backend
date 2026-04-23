"""
Playspace instrument metadata and canonical payload loader.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.products.playspace.schemas.instrument import PlayspaceInstrumentResponse



INSTRUMENT_KEY = "pvua_v5_2"
INSTRUMENT_VERSION = "5.2"
INSTRUMENT_NAME = "Playspace Play Value and Usability Audit Tool"

_INSTRUMENT_PATH = Path(__file__).with_name("pvua_v5_2.instrument.json")


@lru_cache(maxsize=1)
def get_canonical_instrument_payload() -> dict[str, Any]:
	"""Load the backend-owned canonical Playspace instrument JSON."""

	with _INSTRUMENT_PATH.open("r", encoding="utf-8") as instrument_file:
		payload = json.load(instrument_file)

	if not isinstance(payload, dict):
		raise ValueError("Expected the Playspace instrument payload to be a JSON object.")

	if "instrument_key" not in payload and isinstance(payload.get("en"), dict):
		payload = payload.get("en")
		if not isinstance(payload, dict):
			raise ValueError("Expected the localized Playspace instrument payload to be a JSON object.")

	instrument_key = payload.get("instrument_key")
	instrument_version = payload.get("instrument_version")
	if instrument_key != INSTRUMENT_KEY or instrument_version != INSTRUMENT_VERSION:
		raise ValueError(
			f"Canonical Playspace instrument payload metadata does not match {INSTRUMENT_KEY} v{INSTRUMENT_VERSION}."
		)

	return dict(payload)


@lru_cache(maxsize=1)
def get_canonical_instrument_response() -> PlayspaceInstrumentResponse:
	"""Return the validated typed Playspace instrument response model."""

	return PlayspaceInstrumentResponse.model_validate(get_canonical_instrument_payload())

"""
Playspace instrument metadata and canonical payload loader.

The ``app/products/playspace/instruments/`` directory is updated from the
``instruments`` table by ``scripts/sync_canonical_instruments_from_db.py`` (see
``sync-playspace-instruments`` GitHub workflow). Expected layout:

* ``pvua_v5_2.instrument.json`` — best row for the legacy anchor (``INSTRUMENT_KEY`` /
  ``INSTRUMENT_VERSION``), used as the server fallback when the database has no
  active copy.
* ``<instrument_key>.active.instrument.json`` — the row with ``is_active`` for that
  key (``created_at`` tie-break, matching ``get_active_instrument``).
* ``<instrument_key>__v<version>.instrument.json`` — one export per
  ``(instrument_key, instrument_version)`` pair (winner chosen when many rows
  share a pair: active first, then newest ``updated_at``).
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

_INSTRUMENT_PATH = Path(__file__).parent / "instruments" / "pvua_v5_2.instrument.json"


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

"""
Playspace instrument metadata and canonical payload loader.

The ``app/products/playspace/instruments/`` directory is updated from the
``instruments`` table by ``scripts/sync_canonical_instruments_from_db.py`` (see
``sync-playspace-instruments`` GitHub workflow). Expected layout:

* ``<instrument_key>.active.instrument.json`` - the row with ``is_active`` for that
  key (``created_at`` tie-break, matching ``get_active_instrument``). Used as the
  on-disk fallback when the database has no active copy.
* ``<instrument_key>__v<version>.instrument.json`` - one export per
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
INSTRUMENT_NAME = "Playspace Play Value and Usability Audit Tool"

_ACTIVE_INSTRUMENT_PATH = Path(__file__).parent / "instruments" / f"{INSTRUMENT_KEY}.active.instrument.json"


def _unwrap_localized_instrument_payload(payload: object) -> dict[str, Any]:
	"""Return the inner instrument object when the file uses a locale wrapper."""

	if not isinstance(payload, dict):
		raise ValueError("Expected the Playspace instrument payload to be a JSON object.")

	if "instrument_key" not in payload and isinstance(payload.get("en"), dict):
		inner = payload.get("en")
		if not isinstance(inner, dict):
			raise ValueError("Expected the localized Playspace instrument payload to be a JSON object.")
		return dict(inner)

	return dict(payload)


def _read_required_string(payload: dict[str, Any], field_name: str) -> str:
	"""Return one required non-empty string field from an instrument payload."""

	value = payload.get(field_name)
	if not isinstance(value, str) or not value.strip():
		raise ValueError(f"Active Playspace instrument payload is missing {field_name!r}.")
	return value.strip()


@lru_cache(maxsize=1)
def get_active_instrument_payload() -> dict[str, Any]:
	"""Load the on-disk active Playspace instrument JSON."""

	with _ACTIVE_INSTRUMENT_PATH.open("r", encoding="utf-8") as instrument_file:
		payload = _unwrap_localized_instrument_payload(json.load(instrument_file))

	instrument_key = _read_required_string(payload, "instrument_key")
	if instrument_key != INSTRUMENT_KEY:
		raise ValueError(f"Active Playspace instrument payload metadata does not match {INSTRUMENT_KEY!r}.")

	_read_required_string(payload, "instrument_version")
	return dict(payload)


@lru_cache(maxsize=1)
def get_active_instrument_content() -> dict[str, Any]:
	"""Load the on-disk active instrument as a ``locale -> payload`` map.

	``get_active_instrument_payload`` unwraps to the English payload alone, which
	is what runtime rendering wants. Callers that write the file back into the
	``instruments`` table need every locale it carries instead, since publishing
	an English-only map over a multi-locale row would drop the other locales.
	"""

	with _ACTIVE_INSTRUMENT_PATH.open("r", encoding="utf-8") as instrument_file:
		raw_payload = json.load(instrument_file)

	if not isinstance(raw_payload, dict):
		raise ValueError("Expected the Playspace instrument payload to be a JSON object.")

	content: dict[str, Any] = {"en": raw_payload} if "instrument_key" in raw_payload else dict(raw_payload)
	if not content:
		raise ValueError("Active Playspace instrument payload contains no locale payloads.")

	for locale, payload in content.items():
		if not isinstance(payload, dict):
			raise ValueError(f"Active Playspace instrument locale {locale!r} must be a JSON object.")
		if _read_required_string(payload, "instrument_key") != INSTRUMENT_KEY:
			raise ValueError(
				f"Active Playspace instrument locale {locale!r} metadata does not match {INSTRUMENT_KEY!r}."
			)
		_read_required_string(payload, "instrument_version")

	return content


@lru_cache(maxsize=1)
def get_active_instrument_version() -> str:
	"""Return the version string embedded in the on-disk active instrument JSON."""

	return _read_required_string(get_active_instrument_payload(), "instrument_version")


@lru_cache(maxsize=1)
def get_canonical_instrument_payload() -> dict[str, Any]:
	"""Load the backend-owned fallback Playspace instrument JSON."""

	return get_active_instrument_payload()


@lru_cache(maxsize=1)
def get_canonical_instrument_response() -> PlayspaceInstrumentResponse:
	"""Return the validated typed Playspace instrument response model."""

	return PlayspaceInstrumentResponse.model_validate(get_canonical_instrument_payload())


@lru_cache(maxsize=1)
def get_active_instrument_response() -> PlayspaceInstrumentResponse:
	"""Return the validated active Playspace instrument (used for seeding and runtime when DB is empty)."""

	return PlayspaceInstrumentResponse.model_validate(get_active_instrument_payload())

"""YEE instrument request/response schemas.

Instrument version and admin instrument/site-copy models served by
`app/products/yee/routes/instrument.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class YeeInstrumentVersionResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: uuid.UUID
	instrument_key: str
	instrument_version: str
	is_active: bool
	content: dict[str, Any]
	created_at: datetime
	updated_at: datetime


class YeeInstrumentCreateRequest(BaseModel):
	instrument_key: str = Field(default="yee")
	instrument_version: str
	content: dict[str, Any]


class YeeInstrumentActivateRequest(BaseModel):
	is_active: bool


class YeeInstrumentValidateRequest(BaseModel):
	content: dict[str, Any]


class ScoringCompatibilityReport(BaseModel):
	"""Whether an instrument content can be fully scored by the active engine.

	``ok`` is false when the content is missing scored questions the engine
	reads, in which case publishing is blocked (unless explicitly overridden).
	"""

	ok: bool
	scoring_version: str
	required_item_count: int
	present_item_count: int
	missing_items: list[str] = Field(default_factory=list)
	missing_choices: list[str] = Field(default_factory=list)


class SiteCopyVersionResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: uuid.UUID
	instrument_key: str
	instrument_version: str
	is_active: bool
	content: dict[str, Any]
	created_at: datetime
	updated_at: datetime


class SiteCopyCreateRequest(BaseModel):
	instrument_version: str
	content: dict[str, Any]

"""YEE instrument request/response schemas.

Instrument version and admin instrument/site-copy models served by
`app/products/yee/routes/instrument.py`.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


InstrumentLifecycle = Literal["active", "draft", "archived"]
InstrumentSchemaGeneration = Literal["legacy", "authoring_v2"]
InstrumentCompatibilityStatus = Literal["legacy", "copy_only", "migration_required", "invalid"]


class YeeInstrumentVersionSummaryResponse(BaseModel):
	model_config = ConfigDict(from_attributes=True)

	id: uuid.UUID
	instrument_key: str
	instrument_version: str
	parent_instrument_id: uuid.UUID | None = None
	is_active: bool
	lifecycle: InstrumentLifecycle
	usage_count: int
	schema_generation: InstrumentSchemaGeneration
	compatibility_status: InstrumentCompatibilityStatus
	created_at: datetime
	updated_at: datetime


class YeeInstrumentVersionResponse(YeeInstrumentVersionSummaryResponse):
	content: dict[str, Any]


class YeeInstrumentCreateRequest(BaseModel):
	instrument_key: str = Field(default="yee")
	instrument_version: str = Field(min_length=1, max_length=50)
	parent_instrument_id: uuid.UUID | None = None
	content: dict[str, Any]


class YeeInstrumentActivateRequest(BaseModel):
	is_active: bool


class YeeInstrumentValidateRequest(BaseModel):
	content: dict[str, Any]


class YeeInstrumentForkRequest(BaseModel):
	instrument_version: str = Field(min_length=1, max_length=50)


class YeeInstrumentDraftUpdateRequest(BaseModel):
	expected_updated_at: datetime
	instrument_version: str = Field(min_length=1, max_length=50)
	content: dict[str, Any]


class YeeInstrumentPublishRequest(BaseModel):
	expected_updated_at: datetime


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


class InstrumentValidationReason(BaseModel):
	code: str
	message: str
	question_id: str | None = None
	item_id: str | None = None


class YeeInstrumentDraftValidationResponse(BaseModel):
	valid: bool
	activation_ready: bool
	schema_generation: InstrumentSchemaGeneration
	scoring_compatibility: ScoringCompatibilityReport
	reasons: list[InstrumentValidationReason] = Field(default_factory=list)


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

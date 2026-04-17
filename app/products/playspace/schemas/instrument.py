"""
Playspace instrument enums and typed response models.
"""

from __future__ import annotations

from enum import Enum

from pydantic import Field, model_validator

from app.products.playspace.schemas.base import ApiModel


class ExecutionMode(str, Enum):
	"""High-level form scope selected by a place participant."""

	AUDIT = "audit"
	SURVEY = "survey"
	BOTH = "both"


class ConstructKey(str, Enum):
	"""Construct bucket used for totals and exports."""

	USABILITY = "usability"
	PLAY_VALUE = "play_value"


class ScaleKey(str, Enum):
	"""Supported scoring columns in the PVUA workbook."""

	PROVISION = "provision"
	DIVERSITY = "diversity"
	SOCIABILITY = "sociability"
	CHALLENGE = "challenge"


class PreAuditInputType(str, Enum):
	"""Supported mobile inputs for pre-audit prompts."""

	SINGLE_SELECT = "single_select"
	MULTI_SELECT = "multi_select"
	AUTO_TIMESTAMP = "auto_timestamp"


class PreAuditPageKey(str, Enum):
	"""Supported setup screens that can render pre-audit prompts."""

	AUDIT_INFO = "audit_info"
	SPACE_SETUP = "space_setup"


class InstrumentQuestionType(str, Enum):
	"""Supported section-question kinds in the Playspace instrument."""

	SCALED = "scaled"
	CHECKLIST = "checklist"


class InstrumentChoiceOptionResponse(ApiModel):
	"""Reusable choice option used by execution modes and pre-audit prompts."""

	key: str
	label: str
	description: str | None = None


class InstrumentScaleOptionResponse(ApiModel):
	"""One selectable option within a question scoring scale."""

	key: str
	label: str
	addition_value: float
	boost_value: float
	allows_follow_up_scales: bool
	is_not_applicable: bool


class InstrumentScaleDefinitionResponse(ApiModel):
	"""Reusable guidance block for one scale family."""

	key: ScaleKey
	title: str
	prompt: str
	description: str
	options: list[InstrumentScaleOptionResponse]


class InstrumentQuestionScaleResponse(ApiModel):
	"""Scale instance attached to one audit question."""

	key: ScaleKey
	title: str
	prompt: str
	options: list[InstrumentScaleOptionResponse]


class InstrumentQuestionDisplayConditionResponse(ApiModel):
	"""Simple parent-answer condition controlling question visibility."""

	question_key: str
	response_key: str = "provision"
	any_of_option_keys: list[str] = Field(default_factory=list)


class InstrumentPreAuditQuestionResponse(ApiModel):
	"""One structured question shown before the main audit sections."""

	key: str
	label: str
	description: str | None = None
	input_type: PreAuditInputType
	required: bool
	options: list[InstrumentChoiceOptionResponse]
	page_key: PreAuditPageKey = PreAuditPageKey.SPACE_SETUP
	visible_modes: list[ExecutionMode] = Field(
		default_factory=lambda: [
			ExecutionMode.AUDIT,
			ExecutionMode.SURVEY,
			ExecutionMode.BOTH,
		]
	)
	group_key: str | None = None


class InstrumentQuestionResponse(ApiModel):
	"""One Playspace audit question with visibility and scoring metadata."""

	question_key: str
	mode: ExecutionMode
	constructs: list[ConstructKey]
	domains: list[str]
	section_key: str
	prompt: str
	question_type: InstrumentQuestionType = InstrumentQuestionType.SCALED
	scales: list[InstrumentQuestionScaleResponse] = Field(default_factory=list)
	options: list[InstrumentChoiceOptionResponse] = Field(default_factory=list)
	required: bool = True
	display_if: InstrumentQuestionDisplayConditionResponse | None = None

	@model_validator(mode="after")
	def validate_question_shape(self) -> InstrumentQuestionResponse:
		"""Ensure each question kind carries the expected answer metadata."""

		if self.question_type is InstrumentQuestionType.SCALED and len(self.scales) == 0:
			raise ValueError("Scaled questions must define at least one scoring scale.")

		if self.question_type is InstrumentQuestionType.CHECKLIST and len(self.options) == 0:
			raise ValueError("Checklist questions must define at least one selectable option.")

		return self


class InstrumentSectionResponse(ApiModel):
	"""One audit section with its question list."""

	section_key: str
	title: str
	description: str | None = None
	instruction: str
	notes_prompt: str | None = None
	questions: list[InstrumentQuestionResponse]


class PlayspaceInstrumentResponse(ApiModel):
	"""Canonical full Playspace instrument returned by the backend."""

	instrument_key: str
	instrument_name: str
	instrument_version: str
	current_sheet: str
	source_files: list[str]
	preamble: list[str]
	execution_modes: list[InstrumentChoiceOptionResponse]
	pre_audit_questions: list[InstrumentPreAuditQuestionResponse]
	scale_guidance: list[InstrumentScaleDefinitionResponse]
	sections: list[InstrumentSectionResponse]

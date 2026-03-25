"""
Playspace instrument enums and typed response models.
"""

from __future__ import annotations

from enum import Enum

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

    QUANTITY = "quantity"
    DIVERSITY = "diversity"
    SOCIABILITY = "sociability"
    CHALLENGE = "challenge"


class PreAuditInputType(str, Enum):
    """Supported mobile inputs for pre-audit prompts."""

    SINGLE_SELECT = "single_select"
    MULTI_SELECT = "multi_select"
    AUTO_TIMESTAMP = "auto_timestamp"


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


class InstrumentPreAuditQuestionResponse(ApiModel):
    """One structured question shown before the main audit sections."""

    key: str
    label: str
    description: str | None = None
    input_type: PreAuditInputType
    required: bool
    options: list[InstrumentChoiceOptionResponse]


class InstrumentQuestionResponse(ApiModel):
    """One Playspace audit question with visibility and scoring metadata."""

    question_key: str
    mode: ExecutionMode
    constructs: list[ConstructKey]
    domains: list[str]
    section_key: str
    prompt: str
    scales: list[InstrumentQuestionScaleResponse]


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

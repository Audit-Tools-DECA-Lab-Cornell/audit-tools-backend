"""
Canonical scoring metadata derived from the Playspace instrument payload.

The workbook-specific scoring values already live in the canonical instrument JSON.
This module projects that payload into small dataclasses that are convenient for
progress, scoring, and seed-data helpers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache

from app.products.playspace.instrument import get_canonical_instrument_response
from app.products.playspace.schemas.instrument import (
	InstrumentChoiceOptionResponse,
	InstrumentQuestionDisplayConditionResponse,
	InstrumentQuestionResponse,
	InstrumentScaleOptionResponse,
	InstrumentSectionResponse,
	PlayspaceInstrumentResponse,
	ScaleKey,
)


@dataclass(frozen=True)
class ScoringDisplayCondition:
	"""Simple question-to-question visibility condition."""

	question_key: str
	response_key: str
	any_of_option_keys: list[str]


@dataclass(frozen=True)
class ScoringChoiceOption:
	"""One selectable option for a non-scored question."""

	key: str
	label: str


@dataclass(frozen=True)
class ScoringScaleOption:
	"""One selectable option within a scoring scale."""

	key: str
	addition_value: float
	boost_value: float
	allows_follow_up_scales: bool


@dataclass(frozen=True)
class ScoringScale:
	"""One scoring scale for a question."""

	key: str
	options: list[ScoringScaleOption]


@dataclass(frozen=True)
class ScoringQuestion:
	"""Per-question runtime metadata for progress, scoring, and seeding."""

	question_key: str
	mode: str
	constructs: list[str]
	domains: list[str]
	question_type: str
	required: bool
	display_if: ScoringDisplayCondition | None
	options: list[ScoringChoiceOption] = field(default_factory=list)
	scales: list[ScoringScale] = field(default_factory=list)


@dataclass(frozen=True)
class ScoringSection:
	"""One instrument section with its runtime questions."""

	section_key: str
	questions: list[ScoringQuestion] = field(default_factory=list)


def _build_display_condition(
	condition: InstrumentQuestionDisplayConditionResponse | None,
) -> ScoringDisplayCondition | None:
	"""Convert the typed API display condition into the scoring-runtime shape."""

	if condition is None:
		return None

	return ScoringDisplayCondition(
		question_key=condition.question_key,
		response_key=condition.response_key,
		any_of_option_keys=list(condition.any_of_option_keys),
	)


def _build_choice_option(option: InstrumentChoiceOptionResponse) -> ScoringChoiceOption:
	"""Convert one checklist-style option into the runtime shape."""

	return ScoringChoiceOption(
		key=option.key,
		label=option.label,
	)


def _build_scale_option(option: InstrumentScaleOptionResponse) -> ScoringScaleOption:
	"""Convert one scale option into the compact runtime shape."""

	return ScoringScaleOption(
		key=option.key,
		addition_value=float(option.addition_value),
		boost_value=float(option.boost_value),
		allows_follow_up_scales=bool(option.allows_follow_up_scales),
	)


def _build_scoring_question(question: InstrumentQuestionResponse) -> ScoringQuestion:
	"""Project one canonical instrument question into runtime scoring metadata."""

	return ScoringQuestion(
		question_key=question.question_key,
		mode=question.mode.value,
		constructs=[construct.value for construct in question.constructs],
		domains=list(question.domains),
		question_type=question.question_type.value,
		required=question.required,
		display_if=_build_display_condition(question.display_if),
		options=[_build_choice_option(option) for option in question.options],
		scales=[
			ScoringScale(
				key=(scale.key.value if isinstance(scale.key, ScaleKey) else str(scale.key)),
				options=[_build_scale_option(option) for option in scale.options],
			)
			for scale in question.scales
		],
	)


def _build_scoring_section(section: InstrumentSectionResponse) -> ScoringSection:
	"""Project one canonical instrument section into runtime scoring metadata."""

	return ScoringSection(
		section_key=section.section_key,
		questions=[_build_scoring_question(question) for question in section.questions],
	)


def build_scoring_sections_from_instrument(
	instrument: PlayspaceInstrumentResponse,
) -> list[ScoringSection]:
	"""Project one validated Playspace instrument into runtime scoring sections."""

	return [_build_scoring_section(section) for section in instrument.sections]


@lru_cache(maxsize=1)
def get_scoring_sections() -> list[ScoringSection]:
	"""Build and cache runtime scoring metadata from the on-disk canonical instrument."""

	return build_scoring_sections_from_instrument(get_canonical_instrument_response())


# Backward-compatible constant for older call sites that still import the list directly.
SCORING_SECTIONS: list[ScoringSection] = get_scoring_sections()

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictFloat, StrictInt
from pydantic.alias_generators import to_camel


class AuthoringModel(BaseModel):
	model_config = ConfigDict(
		alias_generator=to_camel,
		extra="forbid",
		frozen=True,
		populate_by_name=True,
		serialize_by_alias=True,
	)


class AuthoringOption(AuthoringModel):
	id: str
	label: str
	score: StrictInt


class AuthoringPrimary(AuthoringModel):
	type: Literal["single_select"] = "single_select"
	options: list[AuthoringOption]


class AuthoringFollowUp(AuthoringModel):
	trigger_option_ids: list[str]
	required_when_shown: bool = True
	prompt: str
	options: list[AuthoringOption]


class AuthoringScoring(AuthoringModel):
	method: Literal["option_score", "presence_condition_product"]
	domain: str


class AuthoringResponseBinding(AuthoringModel):
	presence_item_id: str
	choice_id: str
	condition_item_id: str | None


class AuthoringQuestion(AuthoringModel):
	id: str
	prompt: str
	primary: AuthoringPrimary
	follow_up: AuthoringFollowUp | None
	scoring: AuthoringScoring
	response_binding: AuthoringResponseBinding | None


class AuthoringSection(AuthoringModel):
	id: str
	title: str
	instructions: str
	comment_prompt: str
	questions: list[AuthoringQuestion]


class AuthoringInstrumentV2(AuthoringModel):
	schema_version: Literal[2] = Field(alias="schemaVersion")
	sections: list[AuthoringSection]


class ConversionFinding(AuthoringModel):
	code: str
	severity: Literal["warning", "error"]
	message: str
	question_id: str | None = None
	item_id: str | None = None
	choice_id: str | None = None
	answer_id: str | None = None
	category: str | None = None
	expected_score: StrictInt | StrictFloat | None = None
	observed_score: StrictInt | StrictFloat | None = None


def conversion_finding(
	code: str,
	message: str,
	*,
	severity: Literal["warning", "error"] = "warning",
	question_id: str | None = None,
	item_id: str | None = None,
	choice_id: str | None = None,
) -> ConversionFinding:
	return ConversionFinding(
		code=code,
		severity=severity,
		message=message,
		question_id=question_id,
		item_id=item_id,
		choice_id=choice_id,
	)

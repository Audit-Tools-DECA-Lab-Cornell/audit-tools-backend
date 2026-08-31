from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import assert_never

from pydantic import ValidationError

from app.products.yee.schemas.instrument_authoring import (
	AuthoringInstrumentV2,
	AuthoringOption,
	AuthoringQuestion,
)
from app.products.yee.services.scoring_spec import (
	DOMAIN_ORDER,
	SCHEMA_V1_SCORING_CONTRACT,
	SCORING_VERSION,
	AnswerScore,
	PairedItemSpec,
	PresenceItemSpec,
	ScoreItemSpec,
	ScoringContract,
)
from app.products.yee.services.scoring_types import JsonValue
from app.yee_instrument_schema import YeeInstrumentResponse


@dataclass(frozen=True, slots=True)
class ScoringContractResolutionError(Exception):
	code: str
	message: str
	question_id: str | None = None
	field: str | None = None

	def __str__(self) -> str:
		location = f" at {self.field}" if self.field else ""
		question = f" for question {self.question_id}" if self.question_id else ""
		return f"{self.code}{question}{location}: {self.message}"


def _resolution_error(
	code: str,
	message: str,
	*,
	question_id: str | None = None,
	field: str | None = None,
) -> ScoringContractResolutionError:
	return ScoringContractResolutionError(code, message, question_id, field)


def _parse_authoring(
	content: YeeInstrumentResponse | Mapping[str, JsonValue],
) -> AuthoringInstrumentV2 | None:
	match content:
		case YeeInstrumentResponse(authoring=authoring):
			return authoring
		case Mapping() as raw_content:
			raw_authoring = raw_content.get("authoring")
			if raw_authoring is None:
				return None
			try:
				return AuthoringInstrumentV2.model_validate(raw_authoring)
			except ValidationError as exc:
				first_error = exc.errors(include_url=False)[0]
				field = ".".join(str(part) for part in first_error["loc"])
				raise _resolution_error(
					"invalid_authoring_v2",
					first_error["msg"],
					field=field or "authoring",
				) from exc
		case unreachable:
			assert_never(unreachable)


def _answer_scores(
	options: list[AuthoringOption],
	*,
	question_id: str,
	field: str,
) -> tuple[AnswerScore, ...]:
	if not options:
		raise _resolution_error(
			"missing_scored_options",
			"At least one scored option is required",
			question_id=question_id,
			field=field,
		)
	answer_scores: list[AnswerScore] = []
	seen_ids: set[str] = set()
	for option in options:
		if not option.id.strip():
			raise _resolution_error(
				"invalid_option_id",
				"Scored option ids cannot be blank",
				question_id=question_id,
				field=f"{field}.id",
			)
		if option.id in seen_ids:
			raise _resolution_error(
				"duplicate_option_id",
				f"Scored option id {option.id!r} is duplicated",
				question_id=question_id,
				field=f"{field}.id",
			)
		seen_ids.add(option.id)
		answer_scores.append(AnswerScore(option.id, option.score))
	return tuple(answer_scores)


def _binding_parts(question: AuthoringQuestion) -> tuple[str, str, str | None]:
	binding = question.response_binding
	if binding is None:
		raise _resolution_error(
			"missing_response_binding",
			"Scored questions require an immutable response binding",
			question_id=question.id,
			field="responseBinding",
		)
	if not binding.presence_item_id.strip() or not binding.choice_id.strip():
		raise _resolution_error(
			"invalid_response_binding",
			"Presence item and choice ids cannot be blank",
			question_id=question.id,
			field="responseBinding",
		)
	return binding.presence_item_id, binding.choice_id, binding.condition_item_id


def _question_spec(question: AuthoringQuestion) -> ScoreItemSpec:
	if not question.id.strip():
		raise _resolution_error("invalid_question_id", "Question ids cannot be blank", field="id")
	if question.scoring.domain not in DOMAIN_ORDER:
		raise _resolution_error(
			"invalid_scoring_domain",
			f"Unknown scoring domain {question.scoring.domain!r}",
			question_id=question.id,
			field="scoring.domain",
		)
	item_id, choice_id, condition_item_id = _binding_parts(question)
	primary_scores = _answer_scores(question.primary.options, question_id=question.id, field="primary.options")
	match question.scoring.method:
		case "option_score":
			if condition_item_id is not None:
				raise _resolution_error(
					"unexpected_condition_item_binding",
					"Option-score questions cannot bind a condition item",
					question_id=question.id,
					field="responseBinding.conditionItemId",
				)
			if question.follow_up is not None:
				raise _resolution_error(
					"unexpected_scored_follow_up",
					"Option-score questions cannot define a condition follow-up",
					question_id=question.id,
					field="followUp",
				)
			return PresenceItemSpec(question.id, question.scoring.domain, item_id, choice_id, primary_scores)
		case "presence_condition_product":
			follow_up = question.follow_up
			if follow_up is None:
				raise _resolution_error(
					"missing_scored_follow_up",
					"Presence-condition scoring requires a follow-up scale",
					question_id=question.id,
					field="followUp",
				)
			if condition_item_id is None or not condition_item_id.strip():
				raise _resolution_error(
					"missing_condition_item_binding",
					"Presence-condition scoring requires a condition item binding",
					question_id=question.id,
					field="responseBinding.conditionItemId",
				)
			trigger_ids = set(follow_up.trigger_option_ids)
			primary_ids = {option.id for option in question.primary.options}
			if not trigger_ids or not trigger_ids <= primary_ids:
				raise _resolution_error(
					"invalid_follow_up_triggers",
					"Follow-up triggers must reference primary option ids",
					question_id=question.id,
					field="followUp.triggerOptionIds",
				)
			condition_scores = _answer_scores(
				follow_up.options,
				question_id=question.id,
				field="followUp.options",
			)
			return PairedItemSpec(
				question.id,
				question.scoring.domain,
				item_id,
				condition_item_id,
				choice_id,
				primary_scores,
				condition_scores,
			)
		case unreachable:
			assert_never(unreachable)


def scoring_contract_from_instrument(
	content: YeeInstrumentResponse | Mapping[str, JsonValue],
) -> ScoringContract:
	authoring = _parse_authoring(content)
	if authoring is None:
		return SCHEMA_V1_SCORING_CONTRACT
	item_specs: list[ScoreItemSpec] = []
	seen_question_ids: set[str] = set()
	for section in authoring.sections:
		for question in section.questions:
			if question.id in seen_question_ids:
				raise _resolution_error(
					"duplicate_question_id",
					f"Scored question id {question.id!r} is duplicated",
					question_id=question.id,
					field="id",
				)
			seen_question_ids.add(question.id)
			item_specs.append(_question_spec(question))
	return ScoringContract(tuple(item_specs), SCORING_VERSION)

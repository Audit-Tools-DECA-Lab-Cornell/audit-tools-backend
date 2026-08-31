from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass
from typing import Any, NoReturn

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument
from app.products.yee.schemas.instrument_authoring import (
	AuthoringInstrumentV2,
	AuthoringQuestion,
	ConversionFinding,
)
from app.products.yee.services.instrument_authoring import legacy_to_authoring
from app.products.yee.services.instrument_projection import authoring_to_projection
from app.products.yee.services.scoring_contract import required_scoring_items, validate_scoring_compatibility
from app.products.yee.services.scoring_spec import ITEM_SPECS
from app.yee_instrument_schema import YeeInstrumentResponse


@dataclass(frozen=True, slots=True)
class ActivationReason:
	code: str
	message: str
	question_id: str | None = None
	item_id: str | None = None


@dataclass(frozen=True, slots=True)
class ActivationValidation:
	projected_content: YeeInstrumentResponse
	reasons: tuple[ActivationReason, ...]

	@property
	def ok(self) -> bool:
		return not self.reasons


def _raise_activation_conflict(*reasons: ActivationReason) -> NoReturn:
	raise HTTPException(
		status_code=409,
		detail={
			"code": "structural_activation_blocked",
			"reasons": [asdict(reason) for reason in reasons],
		},
	)


async def validated_activation_content(
	session: AsyncSession,
	content: dict[str, Any],
	parent_instrument_id: uuid.UUID | None,
) -> dict[str, Any]:
	candidate = YeeInstrumentResponse.model_validate(content)
	if candidate.authoring is None:
		return content
	if parent_instrument_id is None:
		_raise_activation_conflict(
			ActivationReason("parent_instrument_required", "Authoring schema v2 activation requires a parent")
		)
	parent = await session.get(Instrument, parent_instrument_id)
	if parent is None or parent.instrument_key != "yee":
		_raise_activation_conflict(
			ActivationReason("parent_instrument_invalid", "The YEE parent instrument does not exist")
		)
	try:
		parent_content = YeeInstrumentResponse.model_validate(parent.content)
	except ValidationError:
		_raise_activation_conflict(
			ActivationReason("parent_content_invalid", "The parent instrument content is invalid")
		)
	result = validate_copy_only_activation(candidate, parent_content)
	if not result.ok:
		_raise_activation_conflict(*result.reasons)
	return result.projected_content.model_dump()


def mobile_affirmative(label: str) -> bool:
	normalized = label.lower()
	return normalized.startswith("yes") or "yes," in normalized or normalized == "yes"


def web_affirmative(label: str) -> bool:
	return " ".join(label.split()).lower().startswith("yes")


def validate_copy_only_activation(
	candidate: YeeInstrumentResponse,
	parent: YeeInstrumentResponse,
) -> ActivationValidation:
	authoring = candidate.authoring
	if authoring is None:
		return ActivationValidation(
			candidate,
			(ActivationReason("missing_authoring", "Authoring schema v2 content is required"),),
		)
	reference = parent.authoring or legacy_to_authoring(parent).authoring
	projection = authoring_to_projection(authoring, parent)
	reasons = _projection_reasons(projection.findings)
	reasons.extend(_structure_reasons(authoring, reference))
	reasons.extend(_compatibility_reasons(parent))
	reasons.extend(_compatibility_reasons(projection.content))
	projected = candidate.model_copy(
		update={
			"scoring_items": projection.content.scoring_items,
			"authoring": authoring,
		},
		deep=True,
	)
	return ActivationValidation(projected, tuple(reasons))


def _projection_reasons(findings: tuple[ConversionFinding, ...]) -> list[ActivationReason]:
	reasons: list[ActivationReason] = []
	for finding in findings:
		if finding.severity != "error":
			continue
		reasons.append(
			ActivationReason(
				"projection_error",
				f"Projection failed: {finding.code}",
				question_id=finding.question_id,
				item_id=finding.item_id,
			)
		)
	return reasons


def _ordered_questions(authoring: AuthoringInstrumentV2) -> list[AuthoringQuestion]:
	return [question for section in authoring.sections for question in section.questions]


def _binding_signature(question: AuthoringQuestion) -> tuple[str, str | None, str | None, str | None]:
	binding = question.response_binding
	if binding is None:
		return question.id, None, None, None
	return question.id, binding.presence_item_id, binding.choice_id, binding.condition_item_id


def _option_score_signature(question: AuthoringQuestion) -> tuple[tuple[str, int | float], ...]:
	return tuple((option.id, option.score) for option in question.primary.options)


def _follow_up_score_signature(question: AuthoringQuestion) -> tuple[tuple[str, int | float], ...] | None:
	return (
		None
		if question.follow_up is None
		else tuple((option.id, option.score) for option in question.follow_up.options)
	)


def _structure_reasons(
	candidate: AuthoringInstrumentV2,
	reference: AuthoringInstrumentV2,
) -> list[ActivationReason]:
	candidate_questions = _ordered_questions(candidate)
	reference_questions = _ordered_questions(reference)
	reasons: list[ActivationReason] = []
	if [_binding_signature(question) for question in candidate_questions] != [
		_binding_signature(question) for question in reference_questions
	]:
		reasons.append(ActivationReason("ordered_bindings_changed", "Question bindings or order changed"))
	if tuple(question.id for question in candidate_questions) != tuple(spec.key for spec in ITEM_SPECS):
		reasons.append(ActivationReason("item_spec_coverage_changed", "ITEM_SPECS coverage changed"))
	reference_by_id = {question.id: question for question in reference_questions}
	for question in candidate_questions:
		parent_question = reference_by_id.get(question.id)
		if parent_question is None:
			continue
		if question.scoring != parent_question.scoring or (question.follow_up is None) != (
			parent_question.follow_up is None
		):
			reasons.append(
				ActivationReason("item_behavior_changed", "Item kind, pairing, or scoring method changed", question.id)
			)
		if _option_score_signature(question) != _option_score_signature(parent_question) or _follow_up_score_signature(
			question
		) != _follow_up_score_signature(parent_question):
			reasons.append(ActivationReason("option_scores_changed", "Option score mapping changed", question.id))
		if _trigger_signature(question) != _trigger_signature(parent_question):
			reasons.append(
				ActivationReason("trigger_options_changed", "Follow-up trigger options changed", question.id)
			)
		if _requiredness(question) != _requiredness(parent_question):
			reasons.append(ActivationReason("requiredness_changed", "Follow-up requiredness changed", question.id))
		if _classification_changed(question, parent_question):
			reasons.append(
				ActivationReason(
					"affirmative_classification_changed",
					"A primary label changed deployed follow-up behavior",
					question.id,
				)
			)
	return reasons


def _trigger_signature(question: AuthoringQuestion) -> tuple[str, ...] | None:
	return None if question.follow_up is None else tuple(question.follow_up.trigger_option_ids)


def _requiredness(question: AuthoringQuestion) -> bool | None:
	return None if question.follow_up is None else question.follow_up.required_when_shown


def _classification_changed(candidate: AuthoringQuestion, parent: AuthoringQuestion) -> bool:
	parent_labels = {option.id: option.label for option in parent.primary.options}
	for option in candidate.primary.options:
		parent_label = parent_labels.get(option.id)
		if parent_label is None:
			continue
		if mobile_affirmative(option.label) != mobile_affirmative(parent_label):
			return True
		if web_affirmative(option.label) != web_affirmative(parent_label):
			return True
	return False


def _compatibility_reasons(content: YeeInstrumentResponse) -> list[ActivationReason]:
	report = validate_scoring_compatibility(content.model_dump())
	reasons: list[ActivationReason] = []
	items = {item.item_id: item for item in content.scoring_items}
	for item_id in report.missing_items:
		reasons.append(ActivationReason("missing_scoring_item", "Scored item is missing", item_id=item_id))
	for item_id, choice_ids in required_scoring_items().items():
		item = items.get(item_id)
		if item is None:
			continue
		for choice_id in sorted(choice_ids):
			if choice_id not in item.choices:
				reasons.append(
					ActivationReason(
						"missing_scoring_choice",
						f"Scored choice {choice_id} is missing",
						item_id=item_id,
					)
				)
	return reasons

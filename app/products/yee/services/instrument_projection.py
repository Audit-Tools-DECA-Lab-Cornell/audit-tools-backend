from __future__ import annotations

from dataclasses import dataclass

from app.products.yee.schemas import instrument_authoring as authoring_schema
from app.yee_instrument_schema import YeeInstrumentChoice, YeeInstrumentItem, YeeInstrumentResponse


@dataclass(frozen=True, slots=True)
class ProjectionResult:
	content: YeeInstrumentResponse
	findings: tuple[authoring_schema.ConversionFinding, ...]


def authoring_to_projection(
	authoring: authoring_schema.AuthoringInstrumentV2 | None,
	parent: YeeInstrumentResponse,
) -> ProjectionResult:
	if authoring is None:
		return ProjectionResult(
			parent.model_copy(deep=True),
			(
				authoring_schema.conversion_finding(
					"missing_authoring", "Authoring content is missing", severity="error"
				),
			),
		)
	parent_items = {item.item_id: item for item in parent.scoring_items}
	conflicted_items, findings = _projection_conflicts(authoring, parent_items)
	items = {item_id: item.model_copy(deep=True) for item_id, item in parent_items.items()}
	for section in authoring.sections:
		for question in section.questions:
			binding = question.response_binding
			if binding is None or binding.presence_item_id not in items:
				findings.append(
					authoring_schema.conversion_finding(
						"missing_projection_binding",
						"Projection binding is missing",
						severity="error",
						question_id=question.id,
					)
				)
				continue
			if binding.presence_item_id in conflicted_items:
				continue
			presence = items[binding.presence_item_id]
			choices = dict(presence.choices)
			choices[binding.choice_id] = _updated_choice(choices.get(binding.choice_id), question.prompt)
			items[binding.presence_item_id] = presence.model_copy(update={"choices": choices})
			_update_answers(items, binding.presence_item_id, question.primary.options)
			if binding.condition_item_id is None or question.follow_up is None:
				continue
			if binding.condition_item_id in conflicted_items:
				continue
			condition = items.get(binding.condition_item_id)
			if condition is None:
				findings.append(
					authoring_schema.conversion_finding(
						"missing_projection_condition",
						"Projection condition is missing",
						severity="error",
						question_id=question.id,
					)
				)
				continue
			condition_choices = dict(condition.choices)
			condition_choices[binding.choice_id] = _updated_choice(
				condition_choices.get(binding.choice_id), question.prompt
			)
			condition = condition.model_copy(update={"choices": condition_choices})
			condition = condition.model_copy(update={"question_text": question.follow_up.prompt})
			items[binding.condition_item_id] = condition
			_update_answers(items, binding.condition_item_id, question.follow_up.options)
	projected_items = [items[item.item_id] for item in parent.scoring_items]
	content = parent.model_copy(update={"scoring_items": projected_items, "authoring": authoring}, deep=True)
	return ProjectionResult(content, tuple(findings))


def _updated_choice(choice: YeeInstrumentChoice | None, display: str) -> YeeInstrumentChoice:
	return YeeInstrumentChoice(Display=display) if choice is None else choice.model_copy(update={"Display": display})


def _update_answers(
	items: dict[str, YeeInstrumentItem],
	item_id: str,
	options: list[authoring_schema.AuthoringOption],
) -> None:
	answers = dict(items[item_id].answers)
	for option in options:
		answers[option.id] = _updated_choice(answers.get(option.id), option.label)
	items[item_id] = items[item_id].model_copy(update={"answers": answers})


def _projection_conflicts(
	authoring: authoring_schema.AuthoringInstrumentV2,
	parent_items: dict[str, YeeInstrumentItem],
) -> tuple[set[str], list[authoring_schema.ConversionFinding]]:
	option_uses: dict[str, list[tuple[str, tuple[tuple[str, str], ...]]]] = {}
	prompt_uses: dict[str, list[tuple[str, str]]] = {}
	for section in authoring.sections:
		for question in section.questions:
			binding = question.response_binding
			if binding is None:
				continue
			option_uses.setdefault(binding.presence_item_id, []).append(
				(question.id, _option_signature(question.primary.options))
			)
			if binding.condition_item_id is not None and question.follow_up is not None:
				option_uses.setdefault(binding.condition_item_id, []).append(
					(question.id, _option_signature(question.follow_up.options))
				)
				prompt_uses.setdefault(binding.condition_item_id, []).append((question.id, question.follow_up.prompt))
	conflicted: set[str] = set()
	findings: list[authoring_schema.ConversionFinding] = []
	for item_id, option_item_uses in option_uses.items():
		if len({signature for _, signature in option_item_uses}) <= 1:
			continue
		conflicted.add(item_id)
		parent = parent_items.get(item_id)
		parent_signature = (
			tuple((answer_id, answer.Display or "") for answer_id, answer in parent.answers.items())
			if parent is not None
			else ()
		)
		for question_id, signature in option_item_uses:
			if signature != parent_signature:
				findings.append(_conflict_finding("independent_option_projection", question_id, item_id))
	for prompt_item_id, prompt_item_uses in prompt_uses.items():
		if len({prompt for _, prompt in prompt_item_uses}) <= 1:
			continue
		conflicted.add(prompt_item_id)
		parent_prompt = parent_items[prompt_item_id].question_text if prompt_item_id in parent_items else ""
		for question_id, prompt in prompt_item_uses:
			if prompt != parent_prompt:
				findings.append(_conflict_finding("shared_condition_prompt_difference", question_id, prompt_item_id))
	return conflicted, findings


def _option_signature(options: list[authoring_schema.AuthoringOption]) -> tuple[tuple[str, str], ...]:
	return tuple((option.id, option.label) for option in options)


def _conflict_finding(code: str, question_id: str, item_id: str) -> authoring_schema.ConversionFinding:
	message = (
		"Shared legacy item has independent options"
		if code == "independent_option_projection"
		else "Shared condition prompts differ"
	)
	return authoring_schema.conversion_finding(
		code,
		message,
		severity="error",
		question_id=question_id,
		item_id=item_id,
	)

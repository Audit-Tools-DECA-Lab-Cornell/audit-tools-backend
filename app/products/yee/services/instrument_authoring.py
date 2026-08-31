from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from app.products.yee.schemas import instrument_authoring as authoring_schema
from app.products.yee.services.instrument_projection import ProjectionResult, authoring_to_projection
from app.products.yee.services.scoring_spec import (
	CATEGORY_BY_DOMAIN,
	DOMAIN_ORDER,
	ITEM_SPECS,
	SECTION_BY_DOMAIN,
	AnswerScore,
	PairedItemSpec,
	PresenceItemSpec,
)
from app.products.yee.services.scoring_contract import legacy_score_entry_findings
from app.yee_instrument_schema import YeeInstrumentItem, YeeInstrumentResponse

__all__ = [
	"LegacyConversionResult",
	"ProjectionResult",
	"authoring_to_projection",
	"legacy_to_authoring",
]


@dataclass(frozen=True, slots=True)
class LegacyConversionResult:
	authoring: authoring_schema.AuthoringInstrumentV2
	findings: tuple[authoring_schema.ConversionFinding, ...]


def _options(
	item: YeeInstrumentItem,
	scores: tuple[AnswerScore, ...],
	question_id: str,
	findings: list[authoring_schema.ConversionFinding],
) -> list[authoring_schema.AuthoringOption]:
	options: list[authoring_schema.AuthoringOption] = []
	for answer_score in scores:
		answer = item.answers.get(answer_score.answer_id)
		if answer is None or answer.Display is None:
			findings.append(
				authoring_schema.conversion_finding(
					"missing_answer_label",
					f"Answer {answer_score.answer_id} has no label",
					severity="error",
					question_id=question_id,
					item_id=item.item_id,
				)
			)
			continue
		options.append(
			authoring_schema.AuthoringOption(id=answer_score.answer_id, label=answer.Display, score=answer_score.score)
		)
	return options


def _paired_question(
	content: YeeInstrumentResponse,
	items: dict[str, YeeInstrumentItem],
	spec: PairedItemSpec,
	findings: list[authoring_schema.ConversionFinding],
) -> authoring_schema.AuthoringQuestion | None:
	presence = items.get(spec.presence_item_id)
	if presence is None:
		findings.append(
			authoring_schema.conversion_finding(
				"missing_presence_item", "Presence item is missing", severity="error", question_id=spec.key
			)
		)
		return None
	choice = presence.choices.get(spec.choice_id)
	if choice is None or choice.Display is None:
		findings.append(
			authoring_schema.conversion_finding(
				"missing_presence_choice", "Presence choice is missing", severity="error", question_id=spec.key
			)
		)
		return None
	if presence.item_kind != "presence":
		findings.append(
			authoring_schema.conversion_finding(
				"item_kind_mismatch", "Presence binding has another item kind", question_id=spec.key
			)
		)
	primary_options = _options(presence, spec.presence_answer_scores, spec.key, findings)
	findings.extend(
		legacy_score_entry_findings(
			content, presence, spec.key, spec.choice_id, spec.presence_answer_scores, ("Score",)
		)
	)
	condition = items.get(spec.condition_item_id)
	follow_up: authoring_schema.AuthoringFollowUp | None = None
	if condition is None:
		findings.append(
			authoring_schema.conversion_finding(
				"missing_condition_item", "Condition item is missing", severity="error", question_id=spec.key
			)
		)
	elif not condition.answers:
		findings.append(
			authoring_schema.conversion_finding(
				"condition_without_options", "Condition item has no options", severity="error", question_id=spec.key
			)
		)
	else:
		condition_choice = condition.choices.get(spec.choice_id)
		if condition.item_kind != "condition":
			findings.append(
				authoring_schema.conversion_finding(
					"item_kind_mismatch", "Condition binding has another item kind", question_id=spec.key
				)
			)
		if condition_choice is None:
			findings.append(
				authoring_schema.conversion_finding(
					"missing_condition_choice", "Condition choice is missing", severity="error", question_id=spec.key
				)
			)
		elif condition_choice.Display != choice.Display:
			findings.append(
				authoring_schema.conversion_finding(
					"condition_prompt_difference", "Condition choice copy differs", question_id=spec.key
				)
			)
		condition_options = _options(condition, spec.condition_answer_scores, spec.key, findings)
		if condition_options:
			triggers = [answer.answer_id for answer in spec.presence_answer_scores if answer.score > 0]
			follow_up = authoring_schema.AuthoringFollowUp(
				trigger_option_ids=triggers,
				required_when_shown=True,
				prompt=condition.question_text or content.condition_prompt,
				options=condition_options,
			)
		findings.extend(
			legacy_score_entry_findings(
				content,
				condition,
				spec.key,
				spec.choice_id,
				spec.condition_answer_scores,
				("Score", CATEGORY_BY_DOMAIN[spec.domain]),
			)
		)
	return authoring_schema.AuthoringQuestion(
		id=spec.key,
		prompt=choice.Display,
		primary=authoring_schema.AuthoringPrimary(options=primary_options),
		follow_up=follow_up,
		scoring=authoring_schema.AuthoringScoring(method="presence_condition_product", domain=spec.domain),
		response_binding=authoring_schema.AuthoringResponseBinding(
			presence_item_id=spec.presence_item_id,
			choice_id=spec.choice_id,
			condition_item_id=spec.condition_item_id,
		),
	)


def _presence_question(
	content: YeeInstrumentResponse,
	items: dict[str, YeeInstrumentItem],
	spec: PresenceItemSpec,
	findings: list[authoring_schema.ConversionFinding],
) -> authoring_schema.AuthoringQuestion | None:
	item = items.get(spec.item_id)
	choice = item.choices.get(spec.choice_id) if item is not None else None
	if item is None or choice is None or choice.Display is None:
		findings.append(
			authoring_schema.conversion_finding(
				"missing_presence_binding", "Presence binding is missing", severity="error", question_id=spec.key
			)
		)
		return None
	options = _options(item, spec.answer_scores, spec.key, findings)
	findings.extend(
		legacy_score_entry_findings(
			content,
			item,
			spec.key,
			spec.choice_id,
			spec.answer_scores,
			("Score", CATEGORY_BY_DOMAIN[spec.domain]),
		)
	)
	return authoring_schema.AuthoringQuestion(
		id=spec.key,
		prompt=choice.Display,
		primary=authoring_schema.AuthoringPrimary(options=options),
		follow_up=None,
		scoring=authoring_schema.AuthoringScoring(method="option_score", domain=spec.domain),
		response_binding=authoring_schema.AuthoringResponseBinding(
			presence_item_id=spec.item_id,
			choice_id=spec.choice_id,
			condition_item_id=None,
		),
	)


def legacy_to_authoring(content: YeeInstrumentResponse) -> LegacyConversionResult:
	items = {item.item_id: item for item in content.scoring_items}
	questions: dict[str, list[authoring_schema.AuthoringQuestion]] = {domain: [] for domain in DOMAIN_ORDER}
	findings: list[authoring_schema.ConversionFinding] = []
	for spec in ITEM_SPECS:
		match spec:
			case PairedItemSpec():
				question = _paired_question(content, items, spec, findings)
			case PresenceItemSpec():
				question = _presence_question(content, items, spec, findings)
			case unreachable:
				assert_never(unreachable)
		if question is not None:
			questions[spec.domain].append(question)
	section_meta = {section.block: section for section in content.sections}
	sections: list[authoring_schema.AuthoringSection] = []
	for domain in DOMAIN_ORDER:
		meta = section_meta.get(SECTION_BY_DOMAIN[domain])
		if meta is None:
			findings.append(
				authoring_schema.conversion_finding(
					"missing_section", "Scored section metadata is missing", item_id=domain
				)
			)
		sections.append(
			authoring_schema.AuthoringSection(
				id=domain,
				title=meta.title if meta is not None else CATEGORY_BY_DOMAIN[domain],
				instructions=meta.intro_text if meta is not None else "",
				comment_prompt=meta.comment_prompt if meta is not None else "",
				questions=questions[domain],
			)
		)
	return LegacyConversionResult(
		authoring_schema.AuthoringInstrumentV2(schemaVersion=2, sections=sections), tuple(findings)
	)

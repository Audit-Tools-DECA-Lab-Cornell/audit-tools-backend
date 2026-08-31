from __future__ import annotations

import json
from pathlib import Path

from app.products.yee.schemas.instrument_authoring import AuthoringQuestion
from app.products.yee.services.instrument_authoring import authoring_to_projection, legacy_to_authoring
from app.yee_instrument_schema import YeeInstrumentResponse

REPO_ROOT = Path(__file__).parents[3]
ACTIVE_PATH = REPO_ROOT / "app/products/yee/instruments/yee.active.instrument.json"
GOLDEN_PATH = Path(__file__).parent / "fixtures/authoring_v2_legacy_golden.json"


def _active_content() -> YeeInstrumentResponse:
	return YeeInstrumentResponse.model_validate_json(ACTIVE_PATH.read_text())


def _golden() -> dict[str, object]:
	return json.loads(GOLDEN_PATH.read_text())


def _questions(content: YeeInstrumentResponse) -> list[AuthoringQuestion]:
	conversion = legacy_to_authoring(content)
	return [question for section in conversion.authoring.sections for question in section.questions]


def _replace_question(content: YeeInstrumentResponse, replacement: AuthoringQuestion) -> YeeInstrumentResponse:
	conversion = legacy_to_authoring(content)
	sections = [
		section.model_copy(
			update={
				"questions": [
					replacement if question.id == replacement.id else question for question in section.questions
				],
			}
		)
		for section in conversion.authoring.sections
	]
	authoring = conversion.authoring.model_copy(update={"sections": sections})
	return content.model_copy(update={"authoring": authoring})


def test_active_snapshot_matches_the_full_golden_corpus() -> None:
	# Given the committed schema-v1 instrument and independent golden identities
	content = _active_content()
	golden = _golden()

	# When it is converted from the engine-authoritative item specs
	conversion = legacy_to_authoring(content)
	questions = [question for section in conversion.authoring.sections for question in section.questions]
	bindings = [
		[binding.presence_item_id, binding.choice_id, binding.condition_item_id]
		for question in questions
		if (binding := question.response_binding) is not None
	]

	# Then all 54 identities, section counts, presence-only rows, and QID16 roles match
	assert bindings == golden["orderedBindings"]
	assert [len(section.questions) for section in conversion.authoring.sections] == golden["sectionCounts"]
	assert [question.id for question in questions if question.follow_up is None] == golden["presenceOnlyQuestionIds"]
	qid16 = next(question for question in questions if question.id == "aestheticsAndCare.q1")
	assert qid16.response_binding is not None
	assert qid16.response_binding.model_dump() == golden["qid16Roles"] | {"choiceId": "1"}


def test_authoritative_scores_labels_and_differences_are_exposed() -> None:
	# Given the active snapshot containing redundant legacy score entries
	content = _active_content()

	# When it is imported into logical authoring
	conversion = legacy_to_authoring(content)
	questions = _questions(content)
	access = next(question for question in questions if question.id == "access.q1")
	reverse_ids = set(_golden()["reverseScoredQuestionIds"])

	# Then labels and scores come from ITEM_SPECS and disagreements remain visible findings
	assert [(option.id, option.label, option.score) for option in access.primary.options] == [
		("1", "Yes", 1),
		("2", "No", 0),
	]
	assert access.follow_up is not None
	assert [(option.id, option.label, option.score) for option in access.follow_up.options] == [
		("1", "Poor", 1),
		("2", "Acceptable", 2),
		("3", "Great", 3),
	]
	assert all(
		next(question for question in questions if question.id == key).primary.options[0].score == 0
		for key in reverse_ids
	)
	assert any(finding.code == "score_entry_difference" for finding in conversion.findings)
	assert any(finding.question_id == "aestheticsAndCare.q5" for finding in conversion.findings)


def test_conversion_handles_reorder_and_zero_option_condition() -> None:
	# Given reordered storage and a malformed condition scale
	content = _active_content()
	items = list(reversed(content.scoring_items))
	items = [item.model_copy(update={"answers": {}}) if item.item_id == "QID1#2" else item for item in items]
	malformed = content.model_copy(update={"scoring_items": items})

	# When the logical adapter resolves bindings by spec and item kind
	conversion = legacy_to_authoring(malformed)
	questions = [question for section in conversion.authoring.sections for question in section.questions]
	access = next(question for question in questions if question.id == "access.q1")

	# Then order remains canonical, the empty condition is omitted, and the problem is structured
	assert questions[0].id == "access.q1"
	assert questions[-1].id == "useAndUsability.q8"
	assert access.follow_up is None
	assert any(
		finding.code == "condition_without_options" and finding.question_id == "access.q1"
		for finding in conversion.findings
	)


def test_projection_preserves_parent_and_updates_bound_prompt_copies() -> None:
	# Given a logical wording edit and unrelated top-level extension content
	content = _active_content().model_copy(update={"future_extension": {"keep": [1, 2]}})
	question = _questions(content)[0]
	replacement = question.model_copy(update={"prompt": "Updated logical prompt"})
	edited = _replace_question(content, replacement)

	# When authoring is projected over its legacy parent
	projection = authoring_to_projection(edited.authoring, content)
	projected = projection.content
	presence = next(item for item in projected.scoring_items if item.item_id == "QID1#1")
	condition = next(item for item in projected.scoring_items if item.item_id == "QID1#2")

	# Then IDs/order/parent extras stay stable and both bound prompt copies change
	assert [item.item_id for item in projected.scoring_items] == [item.item_id for item in content.scoring_items]
	assert projected.model_dump()["future_extension"] == {"keep": [1, 2]}
	assert presence.choices["1"].Display == "Updated logical prompt"
	assert condition.choices["1"].Display == "Updated logical prompt"
	assert presence.choices["2"].Display != "Updated logical prompt"


def test_projection_rejects_independent_options_for_questions_sharing_legacy_item() -> None:
	# Given access.q2 changes an option label shared through legacy item QID1#1
	content = _active_content()
	question = next(question for question in _questions(content) if question.id == "access.q2")
	changed_option = question.primary.options[0].model_copy(update={"label": "Yes from access.q2"})
	changed_primary = question.primary.model_copy(update={"options": [changed_option, *question.primary.options[1:]]})
	edited = _replace_question(content, question.model_copy(update={"primary": changed_primary}))

	# When the authoring document is projected onto its legacy parent
	projection = authoring_to_projection(edited.authoring, content)
	presence = next(item for item in projection.content.scoring_items if item.item_id == "QID1#1")
	findings = [finding for finding in projection.findings if finding.code == "independent_option_projection"]

	# Then access.q2 is rejected and cannot overwrite access.q1's shared answer
	assert [(finding.severity, finding.question_id, finding.item_id) for finding in findings] == [
		("error", "access.q2", "QID1#1")
	]
	assert presence.answers["1"].Display == "Yes"


def test_projection_preserves_nested_choice_and_answer_extensions() -> None:
	# Given projection source choices and answers carrying unknown metadata
	content = _active_content()
	presence = next(item for item in content.scoring_items if item.item_id == "QID1#1")
	choices = dict(presence.choices)
	answers = dict(presence.answers)
	choices["1"] = choices["1"].model_copy(update={"choiceExtension": {"keep": 1}})
	answers["1"] = answers["1"].model_copy(update={"answerExtension": [1, 2]})
	items = [
		presence.model_copy(update={"choices": choices, "answers": answers}) if item is presence else item
		for item in content.scoring_items
	]
	parent = content.model_copy(update={"scoring_items": items})
	conversion = legacy_to_authoring(parent)
	access = conversion.authoring.sections[0]
	questions = []
	for question in access.questions:
		binding = question.response_binding
		if binding is None or binding.presence_item_id != "QID1#1":
			questions.append(question)
			continue
		changed_option = question.primary.options[0].model_copy(update={"label": "Yes, edited"})
		changed_primary = question.primary.model_copy(
			update={"options": [changed_option, *question.primary.options[1:]]}
		)
		questions.append(question.model_copy(update={"prompt": "Edited prompt", "primary": changed_primary}))
	sections = [access.model_copy(update={"questions": questions}), *conversion.authoring.sections[1:]]
	authoring = conversion.authoring.model_copy(update={"sections": sections})

	# When projection updates the existing bound values
	projected = authoring_to_projection(authoring, parent).content
	projected_presence = next(item for item in projected.scoring_items if item.item_id == "QID1#1")

	# Then nested choice and answer extensions survive the logical edits
	assert projected_presence.choices["1"].model_dump()["choiceExtension"] == {"keep": 1}
	assert projected_presence.answers["1"].model_dump()["answerExtension"] == [1, 2]
	assert projected_presence.choices["1"].Display == "Edited prompt"
	assert projected_presence.answers["1"].Display == "Yes, edited"


def test_projection_conflict_keeps_parent_when_first_sibling_changes() -> None:
	# Given access.q1 changes options while access.q2 retains the shared legacy scale
	content = _active_content()
	question = next(question for question in _questions(content) if question.id == "access.q1")
	changed_option = question.primary.options[0].model_copy(update={"label": "Changed by access.q1"})
	changed_primary = question.primary.model_copy(update={"options": [changed_option, *question.primary.options[1:]]})
	edited = _replace_question(content, question.model_copy(update={"primary": changed_primary}))
	parent_item = next(item for item in content.scoring_items if item.item_id == "QID1#1")

	# When the conflicting siblings are projected
	projection = authoring_to_projection(edited.authoring, content)
	projected_item = next(item for item in projection.content.scoring_items if item.item_id == "QID1#1")

	# Then the shared item is unchanged and the edited question receives the error
	assert projected_item.model_dump() == parent_item.model_dump()
	conflicts = [
		(finding.question_id, finding.item_id)
		for finding in projection.findings
		if finding.code == "independent_option_projection"
	]
	assert conflicts == [("access.q1", "QID1#1")]


def test_projection_conflict_is_order_independent() -> None:
	# Given access.q2 changes options and is moved before access.q1 in authoring order
	content = _active_content()
	conversion = legacy_to_authoring(content)
	access = conversion.authoring.sections[0]
	question = next(question for question in access.questions if question.id == "access.q2")
	changed_option = question.primary.options[0].model_copy(update={"label": "Changed by access.q2"})
	changed_primary = question.primary.model_copy(update={"options": [changed_option, *question.primary.options[1:]]})
	changed_question = question.model_copy(update={"primary": changed_primary})
	reordered = [changed_question, *[candidate for candidate in access.questions if candidate.id != "access.q2"]]
	sections = [access.model_copy(update={"questions": reordered}), *conversion.authoring.sections[1:]]
	authoring = conversion.authoring.model_copy(update={"sections": sections})
	parent_item = next(item for item in content.scoring_items if item.item_id == "QID1#1")

	# When projection sees the edited sibling first
	projection = authoring_to_projection(authoring, content)
	projected_item = next(item for item in projection.content.scoring_items if item.item_id == "QID1#1")

	# Then output and structured error match the parent-relative result
	assert projected_item.model_dump() == parent_item.model_dump()
	conflicts = [
		(finding.question_id, finding.item_id)
		for finding in projection.findings
		if finding.code == "independent_option_projection"
	]
	assert conflicts == [("access.q2", "QID1#1")]

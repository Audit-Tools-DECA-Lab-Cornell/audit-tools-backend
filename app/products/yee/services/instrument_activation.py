"""Whether a candidate YEE instrument version may become the active one.

Two publication modes, and the difference between them is the whole point of
this module:

``copy_only``
	The candidate is a wording/presentation change over its parent. Everything
	that affects a score must be identical to the parent: ordered bindings,
	``ITEM_SPECS`` coverage, option scores, follow-up triggers, requiredness, and
	the affirmative classification each client derives from a primary label. Its
	``scoring_items`` are projected from the parent rather than trusted.

``structural``
	The candidate deliberately changes the questions. Parent parity checks are
	meaningless here and are not run — comparing a new instrument to the old one
	would reject it for being what it is. Instead the candidate is validated
	against itself: every question binds somewhere real in its own
	``scoring_items``, its follow-ups trigger on options that exist, and
	``scoring_contract_from_instrument`` builds a contract from it. An audit taken
	under this version is scored by that contract, so it must stand alone.

The mode is declared by the publisher, never inferred. Inferring it would mean a
candidate that fails copy-only parity is silently promoted to "structural" —
which is exactly the review step the parity checks exist to force.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import asdict, dataclass
from typing import Any, NoReturn

from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Instrument
from app.products.yee.schemas.instrument import InstrumentPublicationMode
from app.products.yee.schemas.instrument_authoring import (
	AuthoringInstrumentV2,
	AuthoringQuestion,
	ConversionFinding,
)
from app.products.yee.services.instrument_authoring import legacy_to_authoring
from app.products.yee.services.instrument_projection import authoring_to_projection
from app.products.yee.services.scoring_contract import required_scoring_items, validate_scoring_compatibility
from app.products.yee.services.scoring_resolution import (
	ScoringContractResolutionError,
	scoring_contract_from_instrument,
)
from app.products.yee.services.scoring_spec import ITEM_SPECS
from app.yee_instrument_schema import YeeInstrumentResponse

logger = logging.getLogger(__name__)

#: How a candidate is meant to relate to its parent. Declared, never inferred.
#: Aliases the request-schema type so the wire contract and this gate cannot
#: drift into disagreeing about which modes exist.
ActivationMode = InstrumentPublicationMode

#: Depth cap for the parent walk. A chain longer than this is a data problem, not
#: a legitimate lineage, and must not spin.
MAX_PARENT_CHAIN_DEPTH = 64


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


@dataclass(frozen=True, slots=True)
class RollbackTarget:
	"""The immutable row an activation can be rolled back to.

	Recorded before the write so the reversal target is known even if the
	activation itself is what goes wrong. The hash covers the parent's stored
	content, so a later "is the parent still what we approved?" check is an
	equality test rather than a judgement call.
	"""

	instrument_id: uuid.UUID
	instrument_version: str
	content_sha256: str


def content_digest(content: Any) -> str:
	"""Stable SHA-256 of instrument content, independent of key order."""

	canonical = json.dumps(content, sort_keys=True, separators=(",", ":"), default=str)
	return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


async def _parent_is_immutable(session: AsyncSession, parent: Instrument) -> bool:
	"""Whether the parent is stable enough to be a rollback target.

	Active rows and rows some audit already references cannot be edited, so they
	stay what they were when approved. An unused inactive row is still an editable
	draft: reactivating it later could restore content nobody reviewed.
	"""

	# Imported inside the function: app.products.yee.services.instrument imports
	# this module, so a module-level import would be circular. The count is the
	# same question the delete guard asks, and must not drift from it.
	from app.products.yee.services.instrument import _count_yee_audits_for_instrument_version

	if parent.is_active:
		return True
	usage = await _count_yee_audits_for_instrument_version(
		session,
		parent.instrument_key,
		parent.instrument_version,
	)
	return usage > 0


async def _resolved_parent(
	session: AsyncSession,
	parent_instrument_id: uuid.UUID | None,
	candidate_instrument_id: uuid.UUID | None,
) -> tuple[Instrument, YeeInstrumentResponse]:
	"""The parent row an activation must name, with every lineage rule enforced.

	The parent is the rollback target, so it has to exist, belong to YEE, parse,
	be immutable, and sit on a lineage that terminates. A candidate may not be its
	own parent, and may not sit on a chain that loops back to it.
	"""

	if parent_instrument_id is None:
		_raise_activation_conflict(
			ActivationReason("parent_instrument_required", "Authoring schema v2 activation requires a parent")
		)
	if candidate_instrument_id is not None and parent_instrument_id == candidate_instrument_id:
		_raise_activation_conflict(
			ActivationReason("parent_instrument_self_reference", "An instrument cannot be its own parent")
		)
	parent = await session.get(Instrument, parent_instrument_id)
	if parent is None or parent.instrument_key != "yee":
		_raise_activation_conflict(
			ActivationReason("parent_instrument_invalid", "The YEE parent instrument does not exist")
		)
	if not await _parent_is_immutable(session, parent):
		_raise_activation_conflict(
			ActivationReason(
				"parent_instrument_mutable",
				"The parent is an unused draft and can still be edited, so it cannot be a rollback target",
			)
		)
	await _require_terminating_lineage(session, parent, candidate_instrument_id)
	try:
		parent_content = YeeInstrumentResponse.model_validate(parent.content)
	except ValidationError:
		_raise_activation_conflict(
			ActivationReason("parent_content_invalid", "The parent instrument content is invalid")
		)
	return parent, parent_content


async def _require_terminating_lineage(
	session: AsyncSession,
	parent: Instrument,
	candidate_instrument_id: uuid.UUID | None,
) -> None:
	"""Walk up from the parent, rejecting a chain that loops or never ends."""

	seen: set[uuid.UUID] = {parent.id}
	current = parent
	for _ in range(MAX_PARENT_CHAIN_DEPTH):
		next_id = current.parent_instrument_id
		if next_id is None:
			return
		if next_id == candidate_instrument_id or next_id in seen:
			_raise_activation_conflict(
				ActivationReason("parent_instrument_cycle", "The parent chain loops back on itself")
			)
		seen.add(next_id)
		ancestor = await session.get(Instrument, next_id)
		if ancestor is None:
			# A broken link is not a cycle, and the immediate parent is still a
			# valid rollback target, so the walk simply ends here.
			return
		if ancestor.instrument_key != parent.instrument_key:
			_raise_activation_conflict(
				ActivationReason(
					"parent_instrument_cross_key",
					"The parent chain crosses into another instrument key",
				)
			)
		current = ancestor
	_raise_activation_conflict(
		ActivationReason("parent_instrument_chain_too_deep", "The parent chain does not terminate")
	)


async def validated_activation_content(
	session: AsyncSession,
	content: dict[str, Any],
	parent_instrument_id: uuid.UUID | None,
	*,
	mode: ActivationMode = "copy_only",
	candidate_instrument_id: uuid.UUID | None = None,
) -> dict[str, Any]:
	"""The content to store, or a 409 naming every reason it may not be activated.

	:param mode: Declared by the publisher. ``copy_only`` (the default) holds the
		candidate to score-affecting parity with its parent; ``structural`` skips
		those parent comparisons and validates the candidate against itself.
	:param candidate_instrument_id: The row being activated, when it already
		exists. Needed to reject a self-parent and a lineage that loops back.
	"""

	candidate = YeeInstrumentResponse.model_validate(content)
	if candidate.authoring is None:
		if mode == "structural":
			_raise_activation_conflict(
				ActivationReason(
					"structural_requires_authoring",
					"A structural publication must carry authoring schema v2 content",
				)
			)
		return content

	parent, parent_content = await _resolved_parent(session, parent_instrument_id, candidate_instrument_id)
	rollback_target = RollbackTarget(parent.id, parent.instrument_version, content_digest(parent.content))

	if mode == "structural":
		result = validate_structural_activation(candidate)
	else:
		result = validate_copy_only_activation(candidate, parent_content)
	if not result.ok:
		_raise_activation_conflict(*result.reasons)

	# Ids and hashes only — never instrument copy, never participant data.
	logger.info(
		"yee_instrument_activation_authorized",
		extra={
			"activation_mode": mode,
			"rollback_instrument_id": str(rollback_target.instrument_id),
			"rollback_instrument_version": rollback_target.instrument_version,
			"rollback_content_sha256": rollback_target.content_sha256,
			"candidate_content_sha256": content_digest(result.projected_content.model_dump()),
		},
	)
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


def validate_structural_activation(candidate: YeeInstrumentResponse) -> ActivationValidation:
	"""Whether a deliberately-changed instrument can stand on its own.

	No parent comparison happens here. A structural candidate is allowed to differ
	from its parent in exactly the ways copy-only forbids, so the question is not
	"does this match?" but "can an audit taken under this be stored and scored?".

	That resolves to three things:

	Storage: every question binds to an item and choice that exist in this
	instrument's own ``scoring_items``, and a question with a follow-up also binds
	a condition item.

	Reachability: every follow-up triggers on options its own primary offers, and
	offers something to answer.

	Scorability: ``scoring_contract_from_instrument`` builds a contract covering
	every question. That is the same builder the runtime scorer uses, so a refusal
	here means audits taken under this version could not be scored later.

	Unlike copy-only, the candidate's ``scoring_items`` are taken as authored
	rather than projected from a parent: there is no parent to project from.
	"""

	authoring = candidate.authoring
	if authoring is None:
		return ActivationValidation(
			candidate,
			(ActivationReason("missing_authoring", "Authoring schema v2 content is required"),),
		)

	reasons: list[ActivationReason] = []
	questions = _ordered_questions(authoring)
	if not questions:
		reasons.append(ActivationReason("no_scored_questions", "The instrument has no questions to score"))

	seen_question_ids: set[str] = set()
	items = {item.item_id: item for item in candidate.scoring_items}
	for question in questions:
		if question.id in seen_question_ids:
			reasons.append(ActivationReason("duplicate_question_id", "Question id is used twice", question.id))
		seen_question_ids.add(question.id)
		reasons.extend(_self_binding_reasons(question, items))
		reasons.extend(_self_follow_up_reasons(question))

	try:
		contract = scoring_contract_from_instrument(candidate)
	except ScoringContractResolutionError as error:
		reasons.append(
			ActivationReason(
				"scoring_contract_unresolvable",
				f"A scoring contract cannot be built from this instrument: {error.code}",
				question_id=error.question_id,
			)
		)
	else:
		if len(contract.item_specs) != len(questions):
			reasons.append(
				ActivationReason(
					"scoring_contract_incomplete",
					"The scoring contract does not cover every authored question",
				)
			)

	return ActivationValidation(candidate, tuple(reasons))


def _self_binding_reasons(
	question: AuthoringQuestion,
	items: dict[str, Any],
) -> list[ActivationReason]:
	"""Whether this question's answers have somewhere to live in this instrument."""

	binding = question.response_binding
	if binding is None:
		return [
			ActivationReason(
				"missing_response_binding",
				"The question has nowhere to store an answer",
				question.id,
			)
		]

	reasons: list[ActivationReason] = []
	presence = items.get(binding.presence_item_id)
	if presence is None:
		reasons.append(
			ActivationReason(
				"binding_item_missing",
				"The bound item is not in this instrument",
				question.id,
				item_id=binding.presence_item_id,
			)
		)
	elif binding.choice_id not in presence.choices:
		reasons.append(
			ActivationReason(
				"binding_choice_missing",
				f"The bound choice {binding.choice_id} is not on the item",
				question.id,
				item_id=binding.presence_item_id,
			)
		)

	if question.follow_up is None:
		return reasons
	if binding.condition_item_id is None:
		reasons.append(
			ActivationReason(
				"follow_up_binding_missing",
				"The question has a follow-up but binds no item to store it",
				question.id,
			)
		)
		return reasons
	condition = items.get(binding.condition_item_id)
	if condition is None:
		reasons.append(
			ActivationReason(
				"follow_up_item_missing",
				"The follow-up's bound item is not in this instrument",
				question.id,
				item_id=binding.condition_item_id,
			)
		)
	elif binding.choice_id not in condition.choices:
		reasons.append(
			ActivationReason(
				"follow_up_choice_missing",
				f"The follow-up's bound choice {binding.choice_id} is not on its item",
				question.id,
				item_id=binding.condition_item_id,
			)
		)
	return reasons


def _self_follow_up_reasons(question: AuthoringQuestion) -> list[ActivationReason]:
	"""Whether this question's follow-up can be shown and answered."""

	follow_up = question.follow_up
	if follow_up is None:
		return []

	reasons: list[ActivationReason] = []
	primary_option_ids = {option.id for option in question.primary.options}
	if not follow_up.trigger_option_ids:
		reasons.append(
			ActivationReason(
				"follow_up_never_shown",
				"The follow-up has no trigger, so nothing can reveal it",
				question.id,
			)
		)
	for trigger in follow_up.trigger_option_ids:
		if trigger not in primary_option_ids:
			reasons.append(
				ActivationReason(
					"follow_up_trigger_unknown",
					f"Trigger option {trigger} is not offered by the primary question",
					question.id,
				)
			)
	if not follow_up.options:
		reasons.append(
			ActivationReason(
				"follow_up_has_no_options",
				"The follow-up offers nothing to answer",
				question.id,
			)
		)
	return reasons


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

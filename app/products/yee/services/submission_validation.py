"""Logical completeness of a YEE audit's responses against one instrument.

The unit of completeness is the LOGICAL QUESTION, not the storage item: a
matrix ``scoring_items`` row holds several questions, so "is this item answered"
is not a question anyone can act on. Every check here runs over the authoring
contract — the same view the mobile and web fieldwork clients render — so a
missing answer reports the question id an auditor can actually be sent to.

Two rules, both taken from the instrument rather than assumed by the caller:

1. Every logical question with a response binding needs a primary answer.
2. A follow-up needs an answer only when it is SHOWN (the primary answer is one
   of its ``triggerOptionIds``) *and* the instrument marks it required
   (``requiredWhenShown``). A schema-v1 instrument resolves its paired condition
   follow-ups to required through the legacy adapter; an authoring-v2 question
   may explicitly opt out.

Unknown extra response keys are ignored by construction: this only ever reads
the ``(item_id, choice_id)`` pairs the contract binds, so a historical or
superset payload stays valid. That is deliberate — completeness is judged
against the stamped contract, never against whatever else the payload carries.

Pure and synchronous so both the submit path and the migration inventory can
share one definition of "complete" instead of drifting apart.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.products.yee.schemas.audits import IncompleteAuditResponsesDetail
from app.products.yee.schemas.instrument_authoring import AuthoringInstrumentV2
from app.products.yee.services.instrument_authoring import legacy_to_authoring
from app.yee_instrument_schema import YeeInstrumentResponse

#: Env var that turns the rule on. Absent or anything other than "true" keeps it off.
SUBMIT_COMPLETENESS_ENV_VAR = "YEE_ENFORCE_SUBMIT_COMPLETENESS"


def submit_completeness_enforced() -> bool:
	"""Whether a new final submission is rejected for missing required answers.

	Off by default, and read per call rather than captured at import so an
	operator can flip it without a code change.

	Deploying the rule and enabling it have to be separate events. Enforcement is
	only safe once every supported mobile platform runs a build that can recover
	from the rejection — an older client parks the queued submission and cannot
	fix it — so the rollout order is: ship the recovery client, verify install on
	each platform, raise the floors, then set this.
	"""

	return os.getenv(SUBMIT_COMPLETENESS_ENV_VAR, "").strip().lower() == "true"


@dataclass(frozen=True, slots=True)
class IncompleteResponses:
	"""Which logical questions are unanswered, by stable question id.

	Ids only — never question text, never a response value. This travels into a
	422 body and into logs, and neither may carry instrument copy or participant
	data.
	"""

	missing_primary_question_ids: tuple[str, ...]
	missing_follow_up_question_ids: tuple[str, ...]

	@property
	def is_complete(self) -> bool:
		return not self.missing_primary_question_ids and not self.missing_follow_up_question_ids

	def as_error_detail(self) -> dict[str, Any]:
		"""The structured 422 body clients branch on to guide a correction.

		Built through the typed model so the shape stays pinned to the contract
		both clients and `testing/contracts/incomplete-audit.contract.json`
		validate against.
		"""

		return IncompleteAuditResponsesDetail(
			message="This audit is missing required answers.",
			missing_primary_question_ids=list(self.missing_primary_question_ids),
			missing_follow_up_question_ids=list(self.missing_follow_up_question_ids),
		).model_dump()


def authoring_contract(content: YeeInstrumentResponse) -> AuthoringInstrumentV2:
	"""The logical view of an instrument, adapting schema-v1 when needed."""

	return content.authoring or legacy_to_authoring(content).authoring


def _answer(responses: Mapping[str, Any], item_id: str, choice_id: str) -> str:
	item = responses.get(item_id)
	if not isinstance(item, dict):
		return ""
	value = item.get(choice_id)
	return value.strip() if isinstance(value, str) else ""


def find_incomplete_responses(
	content: YeeInstrumentResponse,
	responses: Mapping[str, Any],
) -> IncompleteResponses:
	"""Logical questions still missing a required answer.

	:param content: The instrument the audit is stamped with. Callers must
		resolve the EXACT stamped version; validating against whatever is active
		now would judge an old audit by a contract it was never taken under.
	:param responses: The stored ``{item_id: {choice_id: answer_id}}`` map.
	"""

	missing_primary: list[str] = []
	missing_follow_up: list[str] = []

	for section in authoring_contract(content).sections:
		for question in section.questions:
			binding = question.response_binding
			if binding is None:
				# An unbound question has nowhere to store an answer, so it
				# cannot be judged complete or incomplete.
				continue

			primary = _answer(responses, binding.presence_item_id, binding.choice_id)
			if not primary:
				missing_primary.append(question.id)
				# No primary answer means no follow-up is shown yet; reporting
				# both would tell the auditor to answer a hidden control.
				continue

			follow_up = question.follow_up
			if follow_up is None or not follow_up.required_when_shown:
				continue
			if primary not in follow_up.trigger_option_ids:
				continue
			condition_item_id = binding.condition_item_id
			if condition_item_id is None:
				continue
			if not _answer(responses, condition_item_id, binding.choice_id):
				missing_follow_up.append(question.id)

	return IncompleteResponses(tuple(missing_primary), tuple(missing_follow_up))

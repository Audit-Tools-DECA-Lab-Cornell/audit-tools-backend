"""Small, valid Playspace instrument content for unit tests.

Tests build the instrument they need here instead of reading a numbered file
under ``app/products/playspace/instruments/``. Those files are exported from the
``instruments`` table by ``scripts/sync_canonical_instruments_from_db.py``, which
deletes every version the database no longer stores, so a test pinned to one
numbered file fails as soon as an instrument is published or retired. The
``<key>.active.instrument.json`` snapshot is always written by that sync and is
the only instrument file a test may read.

The multi-select Sociability contract is written out literally rather than
imported from the service, so a change to the production contract fails these
tests instead of moving both sides together.
"""

from __future__ import annotations

from typing import Any, Literal

from app.products.playspace.schemas.instrument import PlayspaceInstrumentResponse

SociabilityMode = Literal["single", "multiple"]

INSTRUMENT_KEY = "pvua_v5_2"

# Sociability scales in versions before 5.32 may stay single-select; from 5.32
# every Sociability scale must use the multi-select contract below.
LEGACY_SOCIABILITY_VERSION = "5.31"
MULTI_SELECT_SOCIABILITY_VERSION = "5.32"

MULTI_SELECT_SOCIABILITY_PROMPT = "Does this feature/environmental characteristic provide opportunities for a child to"
MULTI_SELECT_SOCIABILITY_KEYS = ["play_alone", "small_group", "large_group"]
MULTI_SELECT_SOCIABILITY_LABELS = [
	"Play on their own",
	"Play together in a small group (1-4 other users)",
	"Play together in a larger group (5 or more other users)",
]

LEGACY_SOCIABILITY_PROMPT = "Can more than one child use this feature/environmental characteristic at the same time?"

SECTION_KEY = "section_1_test"
SCALED_QUESTION_KEY = "q_1_1"
CHECKLIST_QUESTION_KEY = "q_1_2"
# Present only when the content carries Sociability, so rules that apply to
# every assigned Sociability scale are exercised across more than one question.
SECOND_SCALED_QUESTION_KEY = "q_1_3"
# Overall scores are summed from the per-domain totals, so a scaled question
# only counts toward ``overall`` when it belongs to a domain.
DOMAIN = "Test domain"


def scale_option(
	key: str,
	label: str,
	addition_value: float = 0,
	boost_value: float = 0,
	*,
	allows_follow_up_scales: bool = False,
	is_not_applicable: bool = False,
	is_unsure: bool = False,
) -> dict[str, Any]:
	"""One scale answer in the stored instrument shape."""

	return {
		"key": key,
		"label": label,
		"addition_value": addition_value,
		"boost_value": boost_value,
		"allows_follow_up_scales": allows_follow_up_scales,
		"is_not_applicable": is_not_applicable,
		"is_unsure": is_unsure,
	}


def provision_scale() -> dict[str, Any]:
	"""A three-answer Provision scale; Some and A lot unlock the follow-up scales."""

	return {
		"key": "provision",
		"title": "Provision",
		"prompt": "How many?",
		"selection_mode": "single",
		"options": [
			scale_option("no", "No", 0, 1),
			scale_option("some", "Some", 1, 2, allows_follow_up_scales=True),
			scale_option("a_lot", "A lot", 2, 3, allows_follow_up_scales=True),
		],
	}


def multi_select_sociability_options() -> list[dict[str, Any]]:
	"""The three canonical opportunities, each worth one point."""

	return [
		scale_option(key, label, 1, 1)
		for key, label in zip(MULTI_SELECT_SOCIABILITY_KEYS, MULTI_SELECT_SOCIABILITY_LABELS, strict=True)
	]


def legacy_sociability_options() -> list[dict[str, Any]]:
	"""Single-select Sociability answers as authored before 5.32."""

	return [
		scale_option("no", "No", 0, 1),
		scale_option("yes_a_pair", "Yes, a pair", 1, 2),
		scale_option("yes_more_than_two_children", "Yes, more than two children", 2, 3),
	]


def sociability_scale(mode: SociabilityMode) -> dict[str, Any]:
	"""A Sociability scale attached to one question."""

	if mode == "multiple":
		return {
			"key": "sociability",
			"title": "Sociability",
			"prompt": MULTI_SELECT_SOCIABILITY_PROMPT,
			"selection_mode": "multiple",
			"options": multi_select_sociability_options(),
		}
	return {
		"key": "sociability",
		"title": "Sociability",
		"prompt": LEGACY_SOCIABILITY_PROMPT,
		"selection_mode": "single",
		"options": legacy_sociability_options(),
	}


def sociability_guidance(mode: SociabilityMode) -> dict[str, Any]:
	"""The instrument-level Sociability guidance block."""

	return {**sociability_scale(mode), "description": "How many children the feature supports at once."}


def _scaled_question(question_key: str, scales: list[dict[str, Any]]) -> dict[str, Any]:
	return {
		"question_key": question_key,
		"mode": "both",
		"constructs": ["play_value"],
		"domains": [DOMAIN],
		"section_key": SECTION_KEY,
		"prompt": "How many?",
		"question_type": "scaled",
		"scales": scales,
		"options": [],
		"required": True,
		"display_if": None,
		"notes_prompt": None,
	}


def minimal_content(
	*,
	version: str = "9.0",
	sociability: SociabilityMode | None = None,
) -> dict[str, Any]:
	"""One section with a scaled and a checklist question, valid for saving and publishing.

	With ``sociability`` set, the scale guidance and two scaled questions carry a
	Sociability scale in that mode, which publish-time validation requires.
	"""

	scale_guidance: list[dict[str, Any]] = [
		{
			"key": "provision",
			"title": "Provision",
			"prompt": "How many?",
			"description": "Guidance",
			"selection_mode": "single",
			"options": [scale_option("no", "No"), scale_option("some", "Some", 1)],
		}
	]
	first_scales = [provision_scale()]
	questions: list[dict[str, Any]] = [
		_scaled_question(SCALED_QUESTION_KEY, first_scales),
		{
			"question_key": CHECKLIST_QUESTION_KEY,
			"mode": "both",
			"constructs": ["usability"],
			"domains": [],
			"section_key": SECTION_KEY,
			"prompt": "Which of these are present?",
			"question_type": "checklist",
			"scales": [],
			"options": [
				{"key": "no", "label": "None", "description": None},
				{"key": "bench", "label": "Bench", "description": None},
			],
			"required": False,
			"display_if": None,
			"notes_prompt": None,
		},
	]
	if sociability is not None:
		scale_guidance.append(sociability_guidance(sociability))
		first_scales.append(sociability_scale(sociability))
		questions.append(
			_scaled_question(SECOND_SCALED_QUESTION_KEY, [provision_scale(), sociability_scale(sociability)])
		)

	return {
		"en": {
			"instrument_key": INSTRUMENT_KEY,
			"instrument_name": "Test instrument",
			"instrument_version": version,
			"current_sheet": "test",
			"source_files": [],
			"preamble": [],
			"execution_modes": [{"key": "both", "label": "Audit & Survey", "description": None}],
			"pre_audit_questions": [],
			"scale_guidance": scale_guidance,
			"sections": [
				{
					"section_key": SECTION_KEY,
					"title": "Test section",
					"description": None,
					"instruction": "Answer the questions.",
					"notes_prompt": None,
					"questions": questions,
				}
			],
			"legal_documents": [],
		}
	}


def question(content: dict[str, Any], question_key: str) -> dict[str, Any]:
	"""Return one question from the English payload for in-place edits."""

	for section in content["en"]["sections"]:
		for candidate in section["questions"]:
			if candidate["question_key"] == question_key:
				return candidate
	raise AssertionError(f"question {question_key!r} is not in the test instrument")


def scale(content: dict[str, Any], question_key: str, scale_key: str) -> dict[str, Any]:
	"""Return one question's scale from the English payload for in-place edits."""

	for candidate in question(content, question_key)["scales"]:
		if candidate["key"] == scale_key:
			return candidate
	raise AssertionError(f"question {question_key!r} has no {scale_key!r} scale")


def sociability_scales(content: dict[str, Any]) -> list[dict[str, Any]]:
	"""Every Sociability scale assigned to a question, in instrument order."""

	return [
		candidate
		for section in content["en"]["sections"]
		for current in section["questions"]
		for candidate in current["scales"]
		if candidate["key"] == "sociability"
	]


def guidance(content: dict[str, Any], scale_key: str) -> dict[str, Any]:
	"""Return one instrument-level scale guidance block for in-place edits."""

	for candidate in content["en"]["scale_guidance"]:
		if candidate["key"] == scale_key:
			return candidate
	raise AssertionError(f"the test instrument has no {scale_key!r} scale guidance")


def parse(content: dict[str, Any]) -> PlayspaceInstrumentResponse:
	"""Parse the English payload the way the scoring and audit services read it."""

	return PlayspaceInstrumentResponse.model_validate(content["en"])

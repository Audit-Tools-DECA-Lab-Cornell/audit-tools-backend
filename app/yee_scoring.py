"""YEE instrument loading and scoring utilities."""

from __future__ import annotations

import json
import re
from functools import lru_cache
from html import unescape
from pathlib import Path
from typing import Any

from app.products.yee.services.scoring_types import LegacyScoreResult

YEE_QSF_PATH = Path(__file__).resolve().parent / "data" / "yee_instrument.qsf"
TOTAL_CATEGORY_NAME = "Score"

YEE_PREAMBLE = [
	"The YEE instrument is completed through a nine-step auditor flow: visit context, importance weighting, six domain sections, and final comments.",
	"Section question wording comes from the survey definition itself, while visit details and importance-weighting prompts are part of the broader YEE audit workflow used in the web app.",
	"Each saved instrument version is a complete snapshot. When a manager or auditor opens the YEE survey, the currently active version is the one the app uses.",
]

YEE_PRE_AUDIT_QUESTIONS = [
	{
		"id": "auditor_id",
		"title": "Auditor ID",
		"prompt": "Generated auditor ID",
		"description": "Automatically populated from the auditor profile at the start of the audit.",
		"options": [],
		"required": True,
		"auto_generated": True,
	},
	{
		"id": "audit_date",
		"title": "Date of the audit",
		"prompt": "Audit date",
		"description": "Recorded when the auditor begins the audit and editable in the visit details step.",
		"options": [],
		"required": True,
		"auto_generated": True,
	},
	{
		"id": "visit_frequency",
		"title": "Visit frequency",
		"prompt": "How often have you been to / visited this space in the last 6 months?",
		"description": "Single-select visit context question shown before the scored YEE sections begin.",
		"options": [
			{"value": "never-before", "label": "I have never been here before"},
			{"value": "every-or-almost-every-day", "label": "Every day or Almost every day"},
			{"value": "once-or-twice-a-week", "label": "One or twice a week"},
			{"value": "once-or-twice-a-month", "label": "Once or twice a month"},
			{"value": "few-times-less-than-monthly", "label": "Only a few times (less than once a month)"},
			{"value": "not-in-last-6-months", "label": "I have not been here in the last 6 months"},
		],
		"required": True,
	},
	{
		"id": "season",
		"title": "Season",
		"prompt": "What is the current season?",
		"description": "Single-select context question used in raw data and reporting.",
		"options": [
			{"value": "spring", "label": "Spring"},
			{"value": "summer", "label": "Summer"},
			{"value": "autumn", "label": "Autumn"},
			{"value": "winter", "label": "Winter"},
		],
		"required": True,
	},
	{
		"id": "weather",
		"title": "Weather",
		"prompt": "What is the weather like today?",
		"description": "Multi-select visit context question shown before the scored YEE sections begin.",
		"options": [
			{"value": "sunny-mostly-sunny", "label": "Sunny / Mostly sunny"},
			{"value": "mostly-cloudy-overcast", "label": "Mostly cloudy / Overcast"},
			{"value": "rainy-drizzling", "label": "Rainy / drizzling"},
			{"value": "windy", "label": "Windy"},
			{"value": "snowy-flurries", "label": "Snowy / Flurries"},
			{"value": "stormy", "label": "Stormy"},
			{"value": "feels-hot", "label": "Feels hot / very hot"},
			{"value": "feels-cold", "label": "Feels cold / very cold"},
		],
		"multi_select": True,
		"required": True,
	},
	{
		"id": "importance_weighting",
		"title": "Importance weighting",
		"prompt": "Please start by telling us how important each of the following issues are to you.",
		"description": "Auditors assign a weight to each YEE domain before answering the section questions. These weights drive the youth-weighted score outputs in reports.",
		"options": [
			{"value": "3", "label": "Very important to me"},
			{"value": "2", "label": "Somewhat important to me"},
			{"value": "1", "label": "Not really important to me"},
		],
		"required": True,
	},
]

YEE_SCALE_GUIDANCE = [
	{
		"id": "provision",
		"title": "Provision",
		"prompt": "To what degree is this feature/environmental characteristic present or considered?",
		"description": "Provision refers to the presence or quantity of an environmental feature or characteristic.",
		"rules": [
			{"value": "0", "label": "No", "add": 0, "boost": 0, "follow_up_behavior": "Blocks follow-up"},
			{"value": "1", "label": "Some", "add": 1, "boost": 1, "follow_up_behavior": "Unlocks follow-up"},
			{"value": "2", "label": "A lot", "add": 2, "boost": 2, "follow_up_behavior": "Unlocks follow-up"},
			{
				"value": "na",
				"label": "Not applicable",
				"add": 0,
				"boost": 0,
				"follow_up_behavior": "Blocks follow-up",
				"tag": "N/A",
			},
		],
	},
	{
		"id": "variety",
		"title": "Variety",
		"prompt": "To what extent is there variety in the provision of this feature/environmental characteristic?",
		"description": "Variety evaluates whether the provided feature offers variety in type, form, or opportunity rather than all options being the same.",
		"rules": [
			{
				"value": "na",
				"label": "Not applicable",
				"add": 0,
				"boost": 1,
				"follow_up_behavior": "Blocks follow-up",
				"tag": "N/A",
			},
			{"value": "1", "label": "No Variety", "add": 1, "boost": 1, "follow_up_behavior": "Blocks follow-up"},
			{"value": "2", "label": "Some Variety", "add": 2, "boost": 2, "follow_up_behavior": "Blocks follow-up"},
			{"value": "3", "label": "A lot of Variety", "add": 3, "boost": 3, "follow_up_behavior": "Blocks follow-up"},
		],
	},
	{
		"id": "challenge",
		"title": "Challenge Opportunities",
		"prompt": "To what extent does this feature/environmental characteristic provide different levels of challenge?",
		"description": "Challenge opportunities assess whether the feature provides opportunities with different levels of difficulty.",
		"rules": [
			{
				"value": "na",
				"label": "Not applicable",
				"add": 0,
				"boost": 1,
				"follow_up_behavior": "Blocks follow-up",
				"tag": "N/A",
			},
			{"value": "1", "label": "No Challenge", "add": 1, "boost": 1, "follow_up_behavior": "Blocks follow-up"},
			{"value": "2", "label": "Some Challenge", "add": 2, "boost": 2, "follow_up_behavior": "Blocks follow-up"},
			{
				"value": "3",
				"label": "A lot of Challenge",
				"add": 3,
				"boost": 3,
				"follow_up_behavior": "Blocks follow-up",
			},
		],
	},
	{
		"id": "sociability",
		"title": "Sociability Support",
		"prompt": "Can more than one child or person use this feature/environmental characteristic together?",
		"description": "Sociability support assesses whether the feature can be used by more than one person at once, individually, in small groups, or in larger groups.",
		"rules": [
			{
				"value": "na",
				"label": "Not applicable",
				"add": 0,
				"boost": 1,
				"follow_up_behavior": "Blocks follow-up",
				"tag": "N/A",
			},
			{"value": "1", "label": "No", "add": 1, "boost": 1, "follow_up_behavior": "Blocks follow-up"},
			{"value": "2", "label": "Yes - a pair", "add": 2, "boost": 2, "follow_up_behavior": "Blocks follow-up"},
			{
				"value": "3",
				"label": "Yes - more than two children",
				"add": 3,
				"boost": 3,
				"follow_up_behavior": "Blocks follow-up",
			},
		],
	},
]

YEE_LEGAL_DOCUMENTS = [
	{
		"id": "service-agreement",
		"title": "Service agreement",
		"last_updated": "2026-06-14",
		"document_type": "service_agreement",
		"content": (
			"This YEE account may be used by invited managers and auditors to manage projects, places, "
			"audits, reports, and related field workflows. Managers control project and auditor access "
			"within their own organization, while platform admins have cross-organization reporting access. "
			"Auditors may complete only the audits assigned to them and may not reconfigure organization, "
			"project, place, or instrument settings."
		),
	},
	{
		"id": "privacy-guidance",
		"title": "Privacy guidance",
		"last_updated": "2026-06-14",
		"document_type": "privacy_guidance",
		"content": (
			"Auditor personal details should remain visible only to managers inside the same organization. "
			"Platform administrators may view manager details and aggregate audit records, but auditor names "
			"and email addresses should remain restricted in admin-facing experiences."
		),
	},
]


def _as_str(value: object) -> str | None:
	if value is None:
		return None
	text = str(value).strip()
	return text if text else None


def _normalize_block_title(block_name: str) -> str:
	cleaned = " ".join(block_name.replace("\xa0", " ").split())
	if ":" in cleaned:
		cleaned = cleaned.split(":", 1)[0]
	return cleaned.strip()


def _normalize_instrument_text(value: str | None) -> str:
	if not value:
		return ""
	text = unescape(value).replace("\xa0", " ").strip()
	replacements = {
		"USE & USABILITY": "Use & Usability",
		"USE or USABILITY": "use or usability",
		"USE OR USABILITY": "use or usability",
		"AESTHETICS & CARE": "Aesthetics & Care",
		"AESTHETICS or CARE": "Aesthetics & Care",
		"AESTHETICS OR CARE": "Aesthetics & Care",
		"ACTIVITY SPACES": "Activity Spaces",
		"ACCESS:": "Access:",
		"AMENITIES:": "Amenities:",
		"Click to write the question text": "",
		"If yes, please rate the condition": "If yes, please rate the condition.",
		"Poor": "Poor",
		"Acceptable": "Acceptable",
		"Great": "Great",
	}
	for source, target in replacements.items():
		text = text.replace(source, target)
	text = text.replace("<br>", " ").replace("<br/>", " ").replace("<br />", " ")
	text = re.sub(r"\bex:", "Ex:", text, flags=re.IGNORECASE)
	text = re.sub(r"\s+", " ", text)
	return text.strip()


def _normalize_choice_map(raw_choices: object) -> dict[str, dict[str, str | None]]:
	if not isinstance(raw_choices, dict):
		return {}
	normalized: dict[str, dict[str, str | None]] = {}
	for key, value in raw_choices.items():
		if not isinstance(value, dict):
			continue
		display = _normalize_instrument_text(_as_str(value.get("Display")))
		normalized[str(key)] = {
			**value,
			"Display": display if display else None,
		}
	return normalized


def _default_section_comment_prompt(section_title: str) -> str:
	return f"Are there any comments you want to add about the {section_title} of this space? (optional)"


def _load_qsf() -> dict[str, object]:
	with YEE_QSF_PATH.open("r", encoding="utf-8") as f:
		return json.load(f)


def _get_element(qsf: dict[str, object], element: str) -> dict[str, object]:
	survey_elements = qsf.get("SurveyElements", [])
	for raw_element in survey_elements:
		if isinstance(raw_element, dict) and raw_element.get("Element") == element:
			return raw_element
	raise ValueError(f"Missing '{element}' element in YEE QSF.")


def _parse_scoring_categories(
	qsf: dict[str, object],
) -> tuple[dict[str, str], dict[str, str]]:
	sco = _get_element(qsf, "SCO")
	payload = sco.get("Payload", {})
	categories = payload.get("ScoringCategories", [])
	by_id: dict[str, str] = {}
	by_name: dict[str, str] = {}

	for item in categories:
		if not isinstance(item, dict):
			continue
		raw_id = _as_str(item.get("ID"))
		raw_name = _as_str(item.get("Name"))
		if raw_id is None or raw_name is None:
			continue
		by_id[raw_id] = raw_name
		by_name[raw_name] = raw_id

	return by_id, by_name


def _parse_block_map(qsf: dict[str, object]) -> dict[str, str]:
	block = _get_element(qsf, "BL")
	payload = block.get("Payload", {})
	result: dict[str, str] = {}
	if not isinstance(payload, dict):
		return result

	for _, section_data in payload.items():
		if not isinstance(section_data, dict):
			continue
		description = _as_str(section_data.get("Description"))
		if description is None or description.lower().startswith("trash"):
			continue
		for block_element in section_data.get("BlockElements", []):
			if not isinstance(block_element, dict):
				continue
			question_id = _as_str(block_element.get("QuestionID"))
			if question_id is None:
				continue
			result[question_id] = description
	return result


def _extract_score_entries(
	*,
	item_id: str,
	entries: object,
) -> list[dict[str, object]]:
	rows: list[dict[str, object]] = []
	if not isinstance(entries, list):
		return rows

	for raw_entry in entries:
		if not isinstance(raw_entry, dict):
			continue
		grades = raw_entry.get("Grades")
		if not isinstance(grades, dict):
			continue

		row = {
			"item_id": item_id,
			"choice_id": _as_str(raw_entry.get("ChoiceID")),
			"answer_id": _as_str(raw_entry.get("AnswerID")),
			"scores_by_category_id": {
				str(category_id): int(score)
				for category_id, score in grades.items()
				if _as_str(category_id) is not None
			},
		}
		rows.append(row)
	return rows


@lru_cache(maxsize=1)
def get_yee_instrument_data() -> dict[str, object]:
	"""Load and normalize the YEE QSF into API-ready metadata."""

	qsf = _load_qsf()
	survey_entry = qsf.get("SurveyEntry", {})
	scoring_names_by_id, _ = _parse_scoring_categories(qsf)
	block_by_question_id = _parse_block_map(qsf)
	all_sq = [el for el in qsf.get("SurveyElements", []) if isinstance(el, dict) and el.get("Element") == "SQ"]
	section_metadata_by_block: dict[str, dict[str, str]] = {}

	scoring_items: list[dict[str, object]] = []
	for sq in all_sq:
		payload = sq.get("Payload")
		if not isinstance(payload, dict):
			continue
		base_qid = _as_str(payload.get("QuestionID")) or _as_str(sq.get("PrimaryAttribute"))
		if base_qid is None:
			continue

		block_name = block_by_question_id.get(base_qid)
		if block_name is None:
			continue
		normalized_block_title = _normalize_block_title(block_name)

		if not payload.get("GradingData"):
			question_type = _as_str(payload.get("QuestionType")) or ""
			question_text = _normalize_instrument_text(
				_as_str(payload.get("QuestionText")) or _as_str(payload.get("QuestionDescription")) or ""
			)
			meta = section_metadata_by_block.setdefault(
				block_name,
				{
					"block": block_name,
					"title": normalized_block_title,
					"intro_text": "",
					"comment_prompt": "",
				},
			)
			if question_type == "DB" and question_text:
				meta["intro_text"] = question_text
			elif question_type == "TE" and question_text:
				meta["comment_prompt"] = question_text

		additional_questions = payload.get("AdditionalQuestions", {})
		if isinstance(additional_questions, dict) and additional_questions:
			for _, question_data in additional_questions.items():
				if not isinstance(question_data, dict):
					continue
				item_id = _as_str(question_data.get("QuestionID"))
				if item_id is None:
					continue
				score_entries = _extract_score_entries(
					item_id=item_id,
					entries=question_data.get("GradingData"),
				)
				if not score_entries:
					continue
				scoring_items.append(
					{
						"item_id": item_id,
						"base_question_id": base_qid,
						"block": block_name,
						"block_title": normalized_block_title,
						"question_text": _normalize_instrument_text(
							_as_str(question_data.get("QuestionDescription"))
							or _as_str(payload.get("QuestionDescription"))
							or ""
						),
						"item_kind": (
							"condition"
							if "if yes" in ((_as_str(question_data.get("QuestionDescription")) or "").lower())
							else "presence"
						),
						"choices": _normalize_choice_map(question_data.get("Choices", {})),
						"answers": _normalize_choice_map(question_data.get("Answers", {})),
						"score_entries": score_entries,
					}
				)
			continue

		score_entries = _extract_score_entries(
			item_id=base_qid,
			entries=payload.get("GradingData"),
		)
		if not score_entries:
			continue

		scoring_items.append(
			{
				"item_id": base_qid,
				"base_question_id": base_qid,
				"block": block_name,
				"block_title": normalized_block_title,
				"question_text": _normalize_instrument_text(
					_as_str(payload.get("QuestionDescription")) or _as_str(payload.get("QuestionText")) or ""
				),
				"item_kind": "presence",
				"choices": _normalize_choice_map(payload.get("Choices", {})),
				"answers": _normalize_choice_map(payload.get("Answers", {})),
				"score_entries": score_entries,
			}
		)

	sections = list(section_metadata_by_block.values())
	for section in sections:
		title = section.get("title", "").strip()
		if not title:
			continue
		if title != "Youth Participant Info":
			section["comment_prompt"] = _default_section_comment_prompt(title)
			continue
		comment_prompt = section.get("comment_prompt", "").strip()
		if not comment_prompt or title.lower() not in comment_prompt.lower():
			section["comment_prompt"] = _default_section_comment_prompt(title)

	return {
		"survey_id": _as_str(survey_entry.get("SurveyID")) or "unknown",
		"survey_name": _as_str(survey_entry.get("SurveyName")) or "Youth Enabling Environments Audit Tool",
		"version": _as_str(survey_entry.get("LastModified")) or "unknown",
		"scoring_categories": scoring_names_by_id,
		"sections": sections,
		"scoring_items": scoring_items,
		"preamble": YEE_PREAMBLE,
		"pre_audit_questions": YEE_PRE_AUDIT_QUESTIONS,
		"scale_guidance": YEE_SCALE_GUIDANCE,
		"legal_documents": YEE_LEGAL_DOCUMENTS,
	}


def score_yee_responses(
	responses: dict[str, Any],
	participant_info: dict[str, Any] | None = None,
) -> LegacyScoreResult:
	from app.products.yee.services.scoring_engine import score_yee_responses_with_participant_info

	return score_yee_responses_with_participant_info(responses, participant_info or {})

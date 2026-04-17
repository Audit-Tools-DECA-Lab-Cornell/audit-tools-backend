"""YEE instrument loading and scoring utilities."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

YEE_QSF_PATH = Path(__file__).resolve().parent / "data" / "yee_instrument.qsf"
TOTAL_CATEGORY_NAME = "Score"


def _as_str(value: object) -> str | None:
	if value is None:
		return None
	text = str(value).strip()
	return text if text else None


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
						"question_text": _as_str(question_data.get("QuestionDescription"))
						or _as_str(payload.get("QuestionDescription"))
						or "",
						"choices": question_data.get("Choices", {}),
						"answers": question_data.get("Answers", {}),
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
				"question_text": _as_str(payload.get("QuestionDescription"))
				or _as_str(payload.get("QuestionText"))
				or "",
				"choices": payload.get("Choices", {}),
				"answers": payload.get("Answers", {}),
				"score_entries": score_entries,
			}
		)

	return {
		"survey_id": _as_str(survey_entry.get("SurveyID")) or "unknown",
		"survey_name": _as_str(survey_entry.get("SurveyName")) or "Youth Enabling Environments Audit Tool",
		"version": _as_str(survey_entry.get("LastModified")) or "unknown",
		"scoring_categories": scoring_names_by_id,
		"scoring_items": scoring_items,
	}


def score_yee_responses(responses: dict[str, object]) -> dict[str, object]:
	"""
	Score user responses against QSF GradingData.

	Response format:
	- Single-choice item: {"QID22": "3"}
	- Matrix-like item: {"QID1#2": {"1": "3", "2": "2"}}
	"""

	instrument = get_yee_instrument_data()
	category_names_by_id: dict[str, str] = instrument["scoring_categories"]  # type: ignore[assignment]
	scoring_items: list[dict[str, object]] = instrument["scoring_items"]  # type: ignore[assignment]

	category_totals: dict[str, int] = {name: 0 for name in category_names_by_id.values()}
	section_totals: dict[str, int] = {}
	matched_rows = 0

	score_rows_by_item: dict[str, list[dict[str, object]]] = {}
	section_by_item: dict[str, str] = {}
	for item in scoring_items:
		item_id = str(item["item_id"])
		score_rows = item.get("score_entries", [])
		if isinstance(score_rows, list):
			score_rows_by_item[item_id] = score_rows  # type: ignore[assignment]
		section_by_item[item_id] = str(item["block"])

	for item_id, raw_answer in responses.items():
		rows = score_rows_by_item.get(item_id)
		if not rows:
			continue
		section_name = section_by_item[item_id]
		section_totals.setdefault(section_name, 0)

		def apply_match(*, choice_id: str | None, answer_id: str | None) -> None:
			nonlocal matched_rows
			for row in rows:
				row_choice = _as_str(row.get("choice_id"))
				row_answer = _as_str(row.get("answer_id"))
				if row_choice != choice_id:
					continue
				if row_answer is not None and row_answer != answer_id:
					continue
				matched_rows += 1
				score_map = row.get("scores_by_category_id", {})
				if not isinstance(score_map, dict):
					continue
				row_total = 0
				for category_id, value in score_map.items():
					category_name = category_names_by_id.get(str(category_id))
					if category_name is None:
						continue
					score_value = int(value)
					category_totals[category_name] = category_totals.get(category_name, 0) + score_value
					row_total += score_value if category_name == TOTAL_CATEGORY_NAME else 0
				section_totals[section_name] += row_total
				break

		if isinstance(raw_answer, str):
			apply_match(choice_id=raw_answer, answer_id=None)
			continue

		if isinstance(raw_answer, dict):
			for choice_id_raw, answer_id_raw in raw_answer.items():
				apply_match(
					choice_id=_as_str(choice_id_raw),
					answer_id=_as_str(answer_id_raw),
				)

	total_score = category_totals.get(TOTAL_CATEGORY_NAME, 0)
	section_scores = {section_name: score for section_name, score in section_totals.items()}

	return {
		"total_score": total_score,
		"section_scores": section_scores,
		"category_scores": category_totals,
		"matched_scored_answers": matched_rows,
	}

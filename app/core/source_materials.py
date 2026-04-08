"""
Helpers for extracting lightweight instrument metadata from source documents.

The seed step should make the provided Playspace and YEE source files useful
now, without forcing the final audit/scoring runtime model too early.
"""

from __future__ import annotations

import json
import re
import zipfile
from html import unescape
from pathlib import Path
from xml.etree import ElementTree

XML_NS = {
    "main": "http://schemas.openxmlformats.org/spreadsheetml/2006/main",
    "rel": "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
}

WorkbookRow = dict[str, str]


def _studentjob_root() -> Path:
    """Resolve the shared repository root containing backend, playspace, and yee."""

    return Path(__file__).resolve().parents[3]


def _clean_text(raw_value: str) -> str:
    """Normalize HTML-ish or spreadsheet text into a compact plain string."""

    without_tags = re.sub(r"<[^>]+>", " ", raw_value)
    normalized_whitespace = re.sub(r"\s+", " ", unescape(without_tags)).strip()
    return normalized_whitespace


def _normalize_source_path(path: Path) -> str:
    """Return a repo-relative source path for metadata payloads."""

    return path.relative_to(_studentjob_root()).as_posix()


def _load_json_dict(path: Path) -> dict[str, object]:
    """Read a JSON object from disk with a defensive runtime shape check."""

    parsed_value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(parsed_value, dict):
        raise ValueError(f"Expected a JSON object in {path}.")
    return parsed_value


def _extract_question_texts(node: object, seen: set[str], output: list[str]) -> None:
    """Walk an arbitrary JSON-like structure and collect meaningful question text."""

    if isinstance(node, dict):
        raw_text = node.get("QuestionText")
        if isinstance(raw_text, str):
            cleaned_text = _clean_text(raw_text)
            if (
                cleaned_text
                and cleaned_text not in {"Click to write the question text", "&nbsp;"}
                and cleaned_text not in seen
            ):
                seen.add(cleaned_text)
                output.append(cleaned_text)

        for child in node.values():
            _extract_question_texts(child, seen, output)
        return

    if isinstance(node, list):
        for item in node:
            _extract_question_texts(item, seen, output)


def build_yee_source_metadata() -> dict[str, object]:
    """Extract lightweight YEE instrument metadata from the provided source files."""

    repo_root = _studentjob_root()
    instructions_path = (
        repo_root
        / "yee"
        / "instructions"
        / "Instructions for viewing YEE Audit Tool questions and scoring.txt"
    )
    pdf_path = repo_root / "yee" / "instructions" / "Youth Enabling Environments Audit Tool.pdf"
    qsf_path = repo_root / "yee" / "instructions" / "Youth_Enabling_Environments_Audit_Tool.json"

    if not (instructions_path.exists() and pdf_path.exists() and qsf_path.exists()):
        return {
            "instrument_key": "yee_seed_instrument",
            "instrument_name": "Youth Enabling Environments Audit Tool",
            "instrument_version": "seed-fallback",
            "source_files": [],
            "section_names": [
                "Access",
                "Activity Spaces",
                "Amenities",
                "Experience of the Space",
                "Aesthetics & Care",
                "Use & Usability",
            ],
            "scoring_categories": [
                "Score",
                "Access",
                "Activity",
                "Amenities",
                "Experience",
                "Aesthetics & Care",
                "Use & Usability",
            ],
            "sample_questions": [],
            "scoring_notes": [
                "Seed fallback metadata was used because the YEE source documents were not present locally."
            ],
            "weighting_questions_included": False,
        }

    qsf_data = _load_json_dict(qsf_path)
    survey_entry = qsf_data.get("SurveyEntry")
    survey_elements = qsf_data.get("SurveyElements")

    if not isinstance(survey_entry, dict) or not isinstance(survey_elements, list):
        raise ValueError("The YEE source JSON does not have the expected structure.")

    section_names: list[str] = []
    scoring_categories: list[str] = []

    for element in survey_elements:
        if not isinstance(element, dict):
            continue

        primary_attribute = element.get("PrimaryAttribute")
        payload = element.get("Payload")
        if primary_attribute == "Survey Blocks" and isinstance(payload, dict):
            for block in payload.values():
                if not isinstance(block, dict):
                    continue
                raw_description = block.get("Description")
                if not isinstance(raw_description, str):
                    continue
                cleaned_description = _clean_text(raw_description)
                if not cleaned_description or "trash" in cleaned_description.lower():
                    continue
                if cleaned_description not in section_names:
                    section_names.append(cleaned_description)

        if primary_attribute == "Scoring" and isinstance(payload, dict):
            raw_categories = payload.get("ScoringCategories")
            if isinstance(raw_categories, list):
                for category in raw_categories:
                    if not isinstance(category, dict):
                        continue
                    name = category.get("Name")
                    if not isinstance(name, str):
                        continue
                    cleaned_name = _clean_text(name)
                    if cleaned_name and cleaned_name not in scoring_categories:
                        scoring_categories.append(cleaned_name)

    question_texts: list[str] = []
    _extract_question_texts(qsf_data, set(), question_texts)

    instructions_lines = [
        _clean_text(line)
        for line in instructions_path.read_text(encoding="utf-8").splitlines()
        if _clean_text(line)
    ]

    return {
        "instrument_key": str(survey_entry.get("SurveyID", "yee_seed_instrument")),
        "instrument_name": _clean_text(
            str(survey_entry.get("SurveyName", "Youth Enabling Environments Audit Tool"))
        ),
        "instrument_version": _clean_text(
            str(survey_entry.get("LastModified", survey_entry.get("SurveyCreationDate", "unknown")))
        ),
        "source_files": [
            _normalize_source_path(instructions_path),
            _normalize_source_path(pdf_path),
            _normalize_source_path(qsf_path),
        ],
        "section_names": section_names,
        "scoring_categories": scoring_categories,
        "sample_questions": question_texts[:8],
        "scoring_notes": instructions_lines,
        "weighting_questions_included": False,
    }


def _load_shared_strings(archive: zipfile.ZipFile) -> list[str]:
    """Read Excel shared strings, if present."""

    if "xl/sharedStrings.xml" not in archive.namelist():
        return []

    root = ElementTree.fromstring(archive.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for string_item in root.findall("main:si", XML_NS):
        text = "".join(node.text or "" for node in string_item.iterfind(".//main:t", XML_NS))
        strings.append(_clean_text(text))
    return strings


def _read_workbook_rows(
    workbook_path: Path,
    sheet_target: str,
    max_nonempty_rows: int | None,
) -> list[WorkbookRow]:
    """Read a compact set of non-empty cell values from a workbook sheet."""

    rows: list[WorkbookRow] = []
    with zipfile.ZipFile(workbook_path) as archive:
        shared_strings = _load_shared_strings(archive)
        sheet_root = ElementTree.fromstring(archive.read(f"xl/{sheet_target}"))

        for row in sheet_root.findall(".//main:sheetData/main:row", XML_NS):
            row_cells: dict[str, str] = {}
            for cell in row.findall("main:c", XML_NS):
                ref = cell.attrib.get("r", "")
                value_node = cell.find("main:v", XML_NS)
                if not ref or value_node is None:
                    continue

                raw_value = value_node.text or ""
                cell_type = cell.attrib.get("t")
                if cell_type == "s" and raw_value.isdigit():
                    string_index = int(raw_value)
                    if string_index < len(shared_strings):
                        cell_value = shared_strings[string_index]
                    else:
                        cell_value = raw_value
                else:
                    cell_value = _clean_text(raw_value)

                if cell_value:
                    row_cells[ref] = cell_value

            if row_cells:
                rows.append(row_cells)
            if max_nonempty_rows is not None and len(rows) >= max_nonempty_rows:
                break

    return rows

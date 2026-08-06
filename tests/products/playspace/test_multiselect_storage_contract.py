"""Storage and response-contract tests for structured multi-select scale answers."""

from __future__ import annotations

import importlib.util
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError
from sqlalchemy import CheckConstraint
from sqlalchemy.orm import Session

from app.models import (
	AuditStatus,
	PlayspaceChecklistAnswer,
	PlayspaceQuestionResponse,
	PlayspaceScaleAnswer,
	PlayspaceSubmission,
	PlayspaceSubmissionSection,
)
from app.products.playspace.audit_state import (
	apply_draft_patch_to_relations,
	build_responses_json_from_relations,
	replace_audit_aggregate,
)
from app.products.playspace.schemas.audit import (
	AuditAggregateWriteRequest,
	AuditDraftPatchRequest,
	SectionDraftPatchRequest,
)
from app.products.playspace.schemas.instrument import PlayspaceInstrumentResponse
from app.products.playspace.services.audit_sessions import PlayspaceAuditSessionsMixin


def _build_instrument(*, selection_mode: str = "multiple") -> PlayspaceInstrumentResponse:
	return PlayspaceInstrumentResponse.model_validate(
		{
			"instrument_key": "pvua_v5_2",
			"instrument_name": "PVUA",
			"instrument_version": "5.32",
			"current_sheet": "PVUA v5.32",
			"source_files": [],
			"preamble": [],
			"execution_modes": [],
			"pre_audit_questions": [],
			"scale_guidance": [],
			"sections": [
				{
					"section_key": "section_a",
					"title": "Section A",
					"instruction": "Answer the question.",
					"questions": [
						{
							"question_key": "question_a",
							"mode": "audit",
							"constructs": ["play_value"],
							"domains": ["social"],
							"section_key": "section_a",
							"prompt": "Who can use this feature?",
							"scales": [
								{
									"key": "sociability",
									"title": "Sociability",
									"prompt": "Select all that apply.",
									"selection_mode": selection_mode,
									"options": [
										{
											"key": option_key,
											"label": option_key.replace("_", " ").title(),
											"addition_value": index,
											"boost_value": 1,
											"allows_follow_up_scales": False,
										}
										for index, option_key in enumerate(["play_alone", "small_group", "large_group"])
									],
								}
							],
						}
					],
				}
			],
		}
	)


def _build_audit() -> PlayspaceSubmission:
	now = datetime.now(timezone.utc)
	return PlayspaceSubmission(
		id=uuid.uuid4(),
		project_id=uuid.uuid4(),
		place_id=uuid.uuid4(),
		auditor_profile_id=uuid.uuid4(),
		audit_code=f"AUDIT-{uuid.uuid4()}",
		instrument_key="pvua_v5_2",
		instrument_version="5.32",
		status=AuditStatus.IN_PROGRESS,
		started_at=now,
		responses_json={"meta": {}, "pre_audit": {}, "sections": {}},
		scores_json={},
		created_at=now,
		updated_at=now,
	)


def _patch(value: object) -> AuditDraftPatchRequest:
	return AuditDraftPatchRequest.model_validate(
		{
			"sections": {
				"section_a": {
					"responses": {"question_a": {"sociability": value}},
				}
			}
		}
	)


def _numeric_totals() -> dict[str, object]:
	return {
		"provision_total": 1,
		"provision_total_max": 2,
		"variety_total": 1,
		"variety_total_max": 2,
		"challenge_total": 1,
		"challenge_total_max": 2,
		"sociability_total": 2,
		"sociability_total_max": 3,
		"play_value_total": 4,
		"play_value_total_max": 8,
		"usability_total": 4,
		"usability_total_max": 8,
	}


def test_instrument_scale_defaults_old_json_to_single_selection() -> None:
	payload = _build_instrument().model_dump(mode="json")
	scale_payload = payload["sections"][0]["questions"][0]["scales"][0]
	assert isinstance(scale_payload, dict)
	scale_payload.pop("selection_mode")

	parsed = PlayspaceInstrumentResponse.model_validate(payload)

	assert parsed.sections[0].questions[0].scales[0].selection_mode == "single"


def test_multiselect_scale_round_trip_is_canonical_and_deduplicated() -> None:
	audit = _build_audit()

	with Session() as session:
		session.add(audit)
		apply_draft_patch_to_relations(
			audit=audit,
			patch=_patch(["large_group", "play_alone", "large_group"]),
			instrument=_build_instrument(),
		)

		answer = audit.submission_sections[0].question_responses[0].scale_answers[0]
		assert answer.option_key is None
		assert answer.selected_option_keys == ["play_alone", "large_group"]
		assert build_responses_json_from_relations(audit)["sections"] == {
			"section_a": {
				"responses": {
					"question_a": {"sociability": ["play_alone", "large_group"]},
				}
			}
		}


def test_scale_answer_reuses_legacy_scalar_row_for_multiselect_value() -> None:
	audit = _build_audit()
	section = PlayspaceSubmissionSection(submission_id=audit.id, section_key="section_a")
	question = PlayspaceQuestionResponse(section=section, question_key="question_a")
	answer = PlayspaceScaleAnswer(
		question_response=question,
		scale_key="sociability",
		option_key="small_group",
	)
	question.scale_answers = [answer]
	section.question_responses = [question]
	audit.submission_sections = [section]

	with Session() as session:
		session.add(audit)
		apply_draft_patch_to_relations(
			audit=audit,
			patch=_patch(["large_group", "play_alone"]),
			instrument=_build_instrument(),
		)
		assert question.scale_answers == [answer]
		assert answer.option_key is None
		assert answer.selected_option_keys == ["play_alone", "large_group"]

		with pytest.raises(ValueError, match="requires an array"):
			apply_draft_patch_to_relations(
				audit=audit,
				patch=_patch("small_group"),
				instrument=_build_instrument(),
			)
		assert question.scale_answers == [answer]
		assert answer.option_key is None
		assert answer.selected_option_keys == ["play_alone", "large_group"]


def test_scale_answer_explicit_none_removes_existing_row_after_storage_transitions() -> None:
	audit = _build_audit()

	with Session() as session:
		session.add(audit)
		apply_draft_patch_to_relations(
			audit=audit,
			patch=_patch(["small_group"]),
			instrument=_build_instrument(),
		)
		answer = audit.submission_sections[0].question_responses[0].scale_answers[0]

		apply_draft_patch_to_relations(
			audit=audit,
			patch=_patch(["play_alone", "large_group"]),
			instrument=_build_instrument(),
		)
		assert audit.submission_sections[0].question_responses[0].scale_answers == [answer]

		apply_draft_patch_to_relations(audit=audit, patch=_patch(None), instrument=_build_instrument())
		assert audit.submission_sections[0].question_responses[0].scale_answers == []
		assert build_responses_json_from_relations(audit)["sections"] == {
			"section_a": {"responses": {"question_a": {}}}
		}


def test_scale_answer_alias_clear_then_canonical_value_reuses_existing_row() -> None:
	audit = _build_audit()
	section = PlayspaceSubmissionSection(submission_id=audit.id, section_key="section_a")
	question = PlayspaceQuestionResponse(section=section, question_key="question_a")
	existing_answer = PlayspaceScaleAnswer(
		question_response=question,
		scale_key="variety",
		option_key="some_variety",
	)
	question.scale_answers = [existing_answer]
	section.question_responses = [question]
	audit.submission_sections = [section]
	patch = AuditDraftPatchRequest(
		sections={
			"section_a": SectionDraftPatchRequest(
				responses={
					"question_a": {
						"diversity": None,
						"variety": "some_variety",
					}
				}
			)
		}
	)

	with Session() as session:
		session.add(audit)
		apply_draft_patch_to_relations(audit=audit, patch=patch)

		assert question.scale_answers == [existing_answer]
		assert existing_answer.scale_key == "variety"
		assert existing_answer.option_key == "some_variety"
		assert existing_answer.selected_option_keys is None


def test_scale_answer_canonical_value_then_alias_clear_removes_existing_row() -> None:
	audit = _build_audit()
	section = PlayspaceSubmissionSection(submission_id=audit.id, section_key="section_a")
	question = PlayspaceQuestionResponse(section=section, question_key="question_a")
	existing_answer = PlayspaceScaleAnswer(
		question_response=question,
		scale_key="variety",
		option_key="some_variety",
	)
	question.scale_answers = [existing_answer]
	section.question_responses = [question]
	audit.submission_sections = [section]
	patch = AuditDraftPatchRequest(
		sections={
			"section_a": SectionDraftPatchRequest(
				responses={
					"question_a": {
						"variety": "some_variety",
						"diversity": None,
					}
				}
			)
		}
	)

	with Session() as session:
		session.add(audit)
		apply_draft_patch_to_relations(audit=audit, patch=patch)

		assert question.scale_answers == []


@pytest.mark.parametrize("value", [[], [""], ["   "]])
def test_scale_array_rejects_empty_values(value: list[str]) -> None:
	with pytest.raises(ValidationError):
		_patch(value)


def test_scale_array_rejects_non_string_values() -> None:
	with pytest.raises(ValidationError):
		_patch(["play_alone", 2])


@pytest.mark.parametrize(
	"value,selection_mode",
	[
		(["unknown"], "multiple"),
		(["play_alone"], "single"),
	],
)
def test_scale_array_rejects_unknown_options_and_single_select_scales(
	value: list[str],
	selection_mode: str,
) -> None:
	with pytest.raises(ValueError):
		apply_draft_patch_to_relations(
			audit=_build_audit(),
			patch=_patch(value),
			instrument=_build_instrument(selection_mode=selection_mode),
		)


def test_scalar_payload_is_rejected_for_multiple_scale() -> None:
	with pytest.raises(ValueError, match="requires an array"):
		apply_draft_patch_to_relations(
			audit=_build_audit(),
			patch=_patch("small_group"),
			instrument=_build_instrument(),
		)


def test_scalar_payload_remains_valid_for_legacy_single_scale() -> None:
	audit = _build_audit()
	apply_draft_patch_to_relations(
		audit=audit,
		patch=_patch("small_group"),
		instrument=_build_instrument(selection_mode="single"),
	)

	assert audit.responses_json["sections"] == {
		"section_a": {"responses": {"question_a": {"sociability": "small_group"}}}
	}


def test_submitted_session_serialization_preserves_scale_array() -> None:
	service = PlayspaceAuditSessionsMixin()
	sections = service._build_section_state_response_map(
		responses_json={
			"sections": {
				"section_a": {
					"responses": {
						"question_a": {"sociability": ["play_alone", "large_group"]},
					}
				}
			}
		}
	)

	assert sections["section_a"].responses["question_a"]["sociability"] == ["play_alone", "large_group"]


def test_full_aggregate_prunes_omitted_rows_while_reusing_surviving_natural_keys() -> None:
	audit = _build_audit()
	kept_section = PlayspaceSubmissionSection(submission_id=audit.id, section_key="section_a")
	removed_section = PlayspaceSubmissionSection(submission_id=audit.id, section_key="section_b")
	kept_question = PlayspaceQuestionResponse(section=kept_section, question_key="question_a")
	removed_question = PlayspaceQuestionResponse(section=kept_section, question_key="question_removed")
	kept_scale = PlayspaceScaleAnswer(
		question_response=kept_question,
		scale_key="provision",
		option_key="some",
	)
	removed_multiselect_scale = PlayspaceScaleAnswer(
		question_response=kept_question,
		scale_key="sociability",
		option_key=None,
		selected_option_keys=["play_alone", "large_group"],
	)
	kept_question.scale_answers = [kept_scale, removed_multiselect_scale]
	kept_question.checklist_answer = PlayspaceChecklistAnswer(
		question_response=kept_question,
		selected_option_keys=["cups"],
		other_details={},
	)
	removed_question.scale_answers = [
		PlayspaceScaleAnswer(
			question_response=removed_question,
			scale_key="provision",
			option_key="some",
		)
	]
	kept_section.question_responses = [kept_question, removed_question]
	removed_section.question_responses = [
		PlayspaceQuestionResponse(section=removed_section, question_key="question_section_removed")
	]
	audit.submission_sections = [kept_section, removed_section]

	aggregate = AuditAggregateWriteRequest(
		sections={
			"section_a": SectionDraftPatchRequest(
				responses={"question_a": {"provision": "a_lot"}},
			)
		}
	)

	with Session() as session:
		session.add(audit)
		replace_audit_aggregate(audit=audit, aggregate=aggregate)

		assert audit.submission_sections == [kept_section]
		assert kept_section.question_responses == [kept_question]
		assert kept_question.scale_answers == [kept_scale]
		assert kept_scale.option_key == "a_lot"
		assert kept_scale.selected_option_keys is None
		assert kept_question.checklist_answer is None
		assert build_responses_json_from_relations(audit)["sections"] == {
			"section_a": {"responses": {"question_a": {"provision": "a_lot"}}}
		}


def test_score_totals_response_accepts_versioned_sociability_breakdown() -> None:
	service = PlayspaceAuditSessionsMixin()
	raw_totals = {
		**_numeric_totals(),
		"sociability_breakdown": {
			"model": "multi_select_v1",
			"play_alone": {"total": 1, "max": 2},
			"small_group": {"total": 2, "max": 3},
			"large_group": {"total": 1, "max": 1},
			"captured_question_count": 2,
			"eligible_question_count": 3,
		},
	}

	response = service._build_score_totals_response(raw_totals)

	assert response is not None
	assert response.sociability_total == 2
	assert response.sociability_total_max == 3
	assert response.sociability_breakdown is not None
	assert response.sociability_breakdown.model == "multi_select_v1"
	assert response.sociability_breakdown.large_group.total == 1
	assert response.sociability_breakdown.captured_question_count == 2


def test_scale_answer_model_and_migration_are_additive() -> None:
	assert PlayspaceScaleAnswer.__table__.c.option_key.nullable is True
	assert PlayspaceScaleAnswer.__table__.c.selected_option_keys.nullable is True
	assert getattr(PlayspaceScaleAnswer.__table__.c.selected_option_keys.type, "none_as_null") is True
	scale_answer_table = PlayspaceScaleAnswer.metadata.tables["playspace_scale_answers"]
	constraints = {constraint.name: constraint for constraint in scale_answer_table.constraints}
	value_constraint = constraints["ck_playspace_scale_answers_exactly_one_value"]
	assert isinstance(value_constraint, CheckConstraint)
	assert str(value_constraint.sqltext) == "(option_key IS NOT NULL) <> (selected_option_keys IS NOT NULL)"

	migration_path = Path(__file__).resolve().parents[3] / "alembic/versions/ps_0010_add_scale_selected_option_keys.py"
	spec = importlib.util.spec_from_file_location("ps_0010_add_scale_selected_option_keys", migration_path)
	assert spec is not None and spec.loader is not None
	migration = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(migration)
	assert migration.revision == "ps_0010"
	assert migration.down_revision == "ps_0009"


def test_scale_answer_migration_downgrade_refuses_any_null_option_key(monkeypatch: pytest.MonkeyPatch) -> None:
	migration_path = Path(__file__).resolve().parents[3] / "alembic/versions/ps_0010_add_scale_selected_option_keys.py"
	spec = importlib.util.spec_from_file_location("ps_0010_add_scale_selected_option_keys_guard", migration_path)
	assert spec is not None and spec.loader is not None
	migration = importlib.util.module_from_spec(spec)
	spec.loader.exec_module(migration)

	bind = MagicMock()
	bind.execute.return_value.first.return_value = (1,)
	monkeypatch.setattr(migration, "_has_table", lambda table_name: True)
	monkeypatch.setattr(migration, "_column_metadata", lambda table_name, column_name: {"nullable": True})
	monkeypatch.setattr(migration.op, "get_bind", lambda: bind)

	with pytest.raises(RuntimeError, match="option_key is NULL"):
		migration.downgrade()

	statement = bind.execute.call_args.args[0]
	assert str(statement) == "SELECT 1 FROM playspace_scale_answers WHERE option_key IS NULL LIMIT 1"

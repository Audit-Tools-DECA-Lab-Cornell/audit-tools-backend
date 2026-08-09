"""Regression tests for Playspace canonical audit state synchronization."""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actors import CurrentUserContext, CurrentUserRole
from app.models import (
	AuditorAssignment,
	AuditorProfile,
	AuditStatus,
	Instrument,
	JSONDict,
	PlayspaceChecklistAnswer,
	Place,
	PlayspaceQuestionResponse,
	PlayspaceScaleAnswer,
	PlayspaceSubmission,
	PlayspaceSubmissionSection,
	Project,
)
from app.products.playspace.audit_state import (
	apply_draft_patch_to_relations,
	build_responses_json_from_relations,
	get_aggregate_revision,
	get_execution_mode_value,
	replace_audit_aggregate,
	set_aggregate_revision,
	set_execution_mode_value,
)
from app.products.playspace.schemas.audit import (
	AuditAggregateWriteRequest,
	AuditDraftPatchRequest,
	AuditMetaPatchRequest,
	AuditSubmitRequest,
	PlaceAuditAccessRequest,
	PreAuditPatchRequest,
	SectionDraftPatchRequest,
)
from app.products.playspace.schemas.instrument import ExecutionMode, PlayspaceInstrumentResponse
from app.products.playspace.services.audit import PlayspaceAuditService
import app.products.playspace.services.audit_sessions as audit_sessions_module
from app.products.playspace.services.audit_sessions import PlayspaceAuditSessionsMixin
from app.products.playspace.services.instrument import build_instrument_response_from_row


def _build_audit() -> PlayspaceSubmission:
	"""Create an in-memory Playspace submission shell for aggregate patch tests."""

	now = datetime.now(timezone.utc)
	return PlayspaceSubmission(
		id=uuid.uuid4(),
		project_id=uuid.uuid4(),
		place_id=uuid.uuid4(),
		auditor_profile_id=uuid.uuid4(),
		audit_code=f"AUDIT-{uuid.uuid4()}",
		instrument_key="pvua_v5_2",
		instrument_version="5.2",
		status=AuditStatus.IN_PROGRESS,
		started_at=now,
		responses_json={"meta": {}, "pre_audit": {}, "sections": {}},
		scores_json={},
		created_at=now,
		updated_at=now,
	)


def _build_project() -> Project:
	"""Create an in-memory project for service-flow tests."""

	return Project(
		id=uuid.uuid4(),
		account_id=uuid.uuid4(),
		name="Project Alpha",
	)


def _build_place() -> Place:
	"""Create an in-memory place for service-flow tests."""

	return Place(
		id=uuid.uuid4(),
		name="Playspace Alpha",
		place_type="playground",
	)


def _build_auditor_profile() -> AuditorProfile:
	"""Create an in-memory auditor profile for service-flow tests."""

	return AuditorProfile(
		id=uuid.uuid4(),
		account_id=uuid.uuid4(),
		auditor_code="AUD-001",
		full_name="Auditor One",
	)


def _build_instrument_row(
	*,
	instrument_key: str = "pvua_v5_2",
	instrument_version: str = "5.13",
	is_active: bool = True,
) -> Instrument:
	"""Create an instrument DB row whose content may carry stale embedded metadata."""

	now = datetime.now(timezone.utc)
	return Instrument(
		id=uuid.uuid4(),
		instrument_key=instrument_key,
		instrument_version=instrument_version,
		is_active=is_active,
		content={
			"en": {
				"instrument_key": "pvua_v5_2",
				"instrument_name": "Playspace Play Value and Usability Audit Tool",
				"instrument_version": "5.2",
				"current_sheet": "PVUA v5.2_online version",
				"source_files": [],
				"preamble": [],
				"execution_modes": [],
				"pre_audit_questions": [],
				"scale_guidance": [],
				"sections": [
					{
						"section_key": "section_a",
						"title": "Section A",
						"description": None,
						"instruction": "Instruction",
						"notes_prompt": None,
						"questions": [
							{
								"question_key": f"question_{instrument_version}",
								"mode": "both",
								"constructs": ["play_value"],
								"domains": ["loose_parts"],
								"section_key": "section_a",
								"prompt": "Checklist question",
								"question_type": "checklist",
								"scales": [],
								"options": [{"key": "cups", "label": "Cups", "description": None}],
								"required": True,
								"display_if": None,
								"notes_prompt": None,
							}
						],
					}
				],
				"legal_documents": [],
			}
		},
		created_at=now,
		updated_at=now,
	)


def _build_actor(auditor_profile: AuditorProfile) -> CurrentUserContext:
	"""Create the current-user context that matches the dummy auditor profile."""

	return CurrentUserContext(
		role=CurrentUserRole.AUDITOR,
		account_id=auditor_profile.account_id,
		auditor_code=auditor_profile.auditor_code,
	)


def _build_service_audit(
	*,
	execution_mode: ExecutionMode | None = None,
	revision: int = 1,
	status: AuditStatus = AuditStatus.IN_PROGRESS,
) -> PlayspaceSubmission:
	"""Create a Playspace submission shell with related project, place, and auditor objects."""

	project = _build_project()
	place = _build_place()
	auditor_profile = _build_auditor_profile()
	now = datetime.now(timezone.utc)
	audit = PlayspaceSubmission(
		id=uuid.uuid4(),
		project_id=project.id,
		place_id=place.id,
		auditor_profile_id=auditor_profile.id,
		audit_code=f"AUDIT-{uuid.uuid4()}",
		instrument_key="pvua_v5_2",
		instrument_version="5.2",
		status=status,
		started_at=now,
		responses_json={
			"schema_version": 1,
			"revision": revision,
			"meta": {},
			"pre_audit": {},
			"sections": {},
		},
		scores_json={},
		created_at=now,
		updated_at=now,
	)
	audit.project = project
	audit.place = place
	audit.auditor_profile = auditor_profile

	if execution_mode is not None:
		set_execution_mode_value(audit=audit, execution_mode=execution_mode.value)
		set_aggregate_revision(audit, revision)

	return audit


class _DummyAuditSessionsService(PlayspaceAuditSessionsMixin):
	"""Minimal mixin host used for focused response-shape tests."""


class _DummySession:
	"""Minimal session stub that records added Playspace submission objects."""

	def __init__(self) -> None:
		self.added_audits: list[PlayspaceSubmission] = []

	def add(self, instance: PlayspaceSubmission) -> None:
		"""Record one added submission without touching a database."""

		if instance.id is None:
			instance.id = uuid.uuid4()
		self.added_audits.append(instance)

	async def execute(self, statement: object) -> object:
		"""Return an empty result for helper paths that query optional tables."""

		class _Result:
			def scalar_one_or_none(self) -> None:
				return None

		return _Result()


class _DummyAuditService(PlayspaceAuditService):
	"""Env-free service host for create/resume, draft-save, and submit tests."""

	def __init__(
		self,
		*,
		audit: PlayspaceSubmission | None = None,
		project: Project | None = None,
		place: Place | None = None,
		auditor_profile: AuditorProfile | None = None,
	) -> None:
		self._session = cast(AsyncSession, _DummySession())
		self._audit = audit
		if audit is not None:
			self._project = audit.project
			self._place = audit.place
			self._auditor_profile = audit.auditor_profile
		else:
			self._project = project or _build_project()
			self._place = place or _build_place()
			self._auditor_profile = auditor_profile or _build_auditor_profile()
		self.commit_count = 0
		self.submit_operation_order: list[str] = []

	async def _commit_and_refresh(self, instance: PlayspaceSubmission | AuditorAssignment) -> None:
		"""Track commit calls and refresh timestamps without a real session."""

		self.commit_count += 1
		if isinstance(instance, PlayspaceSubmission):
			instance.updated_at = datetime.now(timezone.utc)
			instance.project = self._project
			instance.place = self._place
			instance.auditor_profile = self._auditor_profile
			self._audit = instance

	async def _require_auditor_profile(
		self,
		*,
		actor: CurrentUserContext,
	) -> AuditorProfile:
		"""Return the preconfigured in-memory auditor profile."""

		return self._auditor_profile

	async def _get_project_place_pair(
		self,
		*,
		project_id: uuid.UUID,
		place_id: uuid.UUID,
	) -> tuple[Project, Place]:
		"""Return the preconfigured in-memory project/place pair."""

		return self._project, self._place

	async def _ensure_auditor_assigned_to_pair(
		self,
		*,
		auditor_profile_id: uuid.UUID,
		project_id: uuid.UUID,
		place_id: uuid.UUID,
	) -> None:
		"""Skip assignment enforcement in env-free service tests."""

	async def _get_existing_audit(
		self,
		*,
		project_id: uuid.UUID,
		place_id: uuid.UUID,
		auditor_profile_id: uuid.UUID,
	) -> PlayspaceSubmission | None:
		"""Return the preconfigured in-memory Playspace submission."""

		return self._audit

	async def _load_accessible_audit(
		self,
		*,
		actor: CurrentUserContext,
		audit_id: uuid.UUID,
	) -> PlayspaceSubmission:
		"""Return the preconfigured in-memory Playspace submission."""

		self.submit_operation_order.append("load_audit")
		if self._audit is None:
			raise AssertionError("Dummy audit must be configured before loading it.")
		return self._audit

	async def _lock_auditor_profile_for_audit(self, *, audit_id: uuid.UUID) -> None:
		"""Record the deletion/submission lock without requiring a database."""

		self.submit_operation_order.append("lock_auditor_profile")


def test_apply_draft_patch_merges_pre_audit_into_canonical_aggregate() -> None:
	"""Pre-audit saves should update the canonical responses_json aggregate."""

	audit = _build_audit()
	audit.responses_json = {
		"meta": {"execution_mode": "audit"},
		"pre_audit": {
			"season": "spring",
			"weather_conditions": ["windy"],
		},
		"sections": {},
	}

	patch = AuditDraftPatchRequest(
		pre_audit=PreAuditPatchRequest(
			place_size="large",
			current_users_0_5="none",
			current_users_6_12="a_few",
			current_users_13_17="a_lot",
			current_users_18_plus="a_few",
			playspace_busyness="very_busy",
			season="summer",
			weather_conditions=["cloudy_overcast", "light_rain"],
			wind_conditions="light_wind",
		)
	)

	apply_draft_patch_to_relations(audit=audit, patch=patch)

	assert audit.responses_json["meta"] == {"execution_mode": "audit"}
	assert audit.responses_json["pre_audit"] == {
		"place_size": "large",
		"current_users_0_5": "none",
		"current_users_6_12": "a_few",
		"current_users_13_17": "a_lot",
		"current_users_18_plus": "a_few",
		"playspace_busyness": "very_busy",
		"season": "summer",
		"weather_conditions": ["cloudy_overcast", "light_rain"],
		"wind_conditions": "light_wind",
	}


def test_apply_draft_patch_merges_section_answers_into_canonical_aggregate() -> None:
	"""Section saves should replace one question answer-set inside responses_json."""

	audit = _build_audit()
	audit.responses_json = {
		"meta": {},
		"pre_audit": {},
		"sections": {
			"section_a": {
				"note": "Before",
				"responses": {
					"question_a": {
						"provision": "some",
					}
				},
			}
		},
	}

	patch = AuditDraftPatchRequest(
		sections={
			"section_a": SectionDraftPatchRequest(
				responses={
					"question_a": {
						"provision": "a_lot",
						"variety": "some_variety",
					}
				},
				note="Updated note",
			)
		}
	)

	apply_draft_patch_to_relations(audit=audit, patch=patch)

	assert audit.responses_json["sections"] == {
		"section_a": {
			"note": "Updated note",
			"responses": {
				"question_a": {
					"provision": "a_lot",
					"variety": "some_variety",
				}
			},
		}
	}


def test_apply_draft_patch_preserves_omitted_fields_and_allows_clearing_note() -> None:
	"""Partial draft patches should preserve aggregate values and allow explicit note clearing."""

	audit = _build_audit()
	audit.responses_json = {
		"meta": {"execution_mode": "survey"},
		"pre_audit": {
			"season": "spring",
			"weather_conditions": ["windy"],
		},
		"sections": {
			"section_a": {
				"note": "Keep me?",
				"responses": {
					"question_a": {
						"provision": "some",
					}
				},
			}
		},
	}

	patch = AuditDraftPatchRequest(
		pre_audit=PreAuditPatchRequest(season="summer"),
		sections={
			"section_a": SectionDraftPatchRequest(
				note=None,
			)
		},
	)

	apply_draft_patch_to_relations(audit=audit, patch=patch)

	assert audit.responses_json["meta"] == {"execution_mode": "survey"}
	assert audit.responses_json["pre_audit"] == {
		"season": "summer",
		"weather_conditions": ["windy"],
	}
	responses_json = cast(JSONDict, audit.responses_json)
	sections = responses_json.get("sections")
	assert isinstance(sections, dict)
	section_a = sections.get("section_a")
	assert isinstance(section_a, dict)
	assert section_a["note"] is None


def test_replace_audit_aggregate_preserves_revision_and_replaces_payload() -> None:
	"""Whole-aggregate writes should preserve server-managed revision and replace the body."""

	audit = _build_audit()
	audit.responses_json = {
		"schema_version": 1,
		"revision": 4,
		"meta": {"execution_mode": "audit"},
		"pre_audit": {"season": "spring"},
		"sections": {
			"section_a": {
				"note": "Before",
				"responses": {"question_a": {"provision": "some"}},
			}
		},
	}

	replace_audit_aggregate(
		audit=audit,
		aggregate=AuditAggregateWriteRequest(
			schema_version=1,
			meta=AuditMetaPatchRequest(execution_mode=ExecutionMode.SURVEY),
			pre_audit=PreAuditPatchRequest(season="winter", weather_conditions=["windy"]),
			sections={
				"section_b": SectionDraftPatchRequest(
					note="After",
					responses={"question_b": {"provision": "a_lot"}},
				)
			},
		),
	)

	assert audit.responses_json == {
		"schema_version": 1,
		"revision": 4,
		"meta": {"execution_mode": "survey"},
		"pre_audit": {
			"place_size": None,
			"current_users_0_5": None,
			"current_users_6_12": None,
			"current_users_13_17": None,
			"current_users_18_plus": None,
			"playspace_busyness": None,
			"season": "winter",
			"weather_conditions": ["windy"],
			"wind_conditions": None,
		},
		"sections": {
			"section_b": {
				"note": "After",
				"responses": {"question_b": {"provision": "a_lot"}},
			}
		},
	}


def test_apply_draft_patch_merges_checklist_question_payload_into_canonical_aggregate() -> None:
	"""Checklist-style follow-up answers should persist in the canonical aggregate."""

	audit = _build_audit()

	patch = AuditDraftPatchRequest(
		sections={
			"section_a": SectionDraftPatchRequest(
				responses={
					"question_checklist": {
						"selected_option_keys": ["cups", "buckets", "other"],
						"other_details": {
							"text": "Loose timber offcuts",
						},
					}
				}
			)
		}
	)

	apply_draft_patch_to_relations(audit=audit, patch=patch)

	assert audit.responses_json["sections"] == {
		"section_a": {
			"responses": {
				"question_checklist": {
					"selected_option_keys": ["cups", "buckets", "other"],
					"other_details": {
						"text": "Loose timber offcuts",
					},
				}
			}
		}
	}


def test_apply_draft_patch_round_trips_checklist_payload_in_normalized_state() -> None:
	"""Checklist answers should persist as arrays/objects through normalized tables."""

	audit = _build_audit()
	patch = AuditDraftPatchRequest(
		sections={
			"section_a": SectionDraftPatchRequest(
				responses={
					"question_checklist": {
						"selected_option_keys": ["cups", "buckets", "other"],
						"other_details": {"text": "Loose timber offcuts"},
						"question_note": "Auditor saw these loose parts in storage.",
					}
				}
			)
		}
	)

	with Session() as session:
		session.add(audit)

		apply_draft_patch_to_relations(audit=audit, patch=patch)

		section = audit.submission_sections[0]
		question_response = section.question_responses[0]
		assert question_response.scale_answers == []
		assert isinstance(question_response.checklist_answer, PlayspaceChecklistAnswer)
		assert question_response.checklist_answer.selected_option_keys == ["cups", "buckets", "other"]
		assert question_response.checklist_answer.other_details == {"text": "Loose timber offcuts"}
		assert question_response.note == "Auditor saw these loose parts in storage."

		assert build_responses_json_from_relations(audit)["sections"] == {
			"section_a": {
				"responses": {
					"question_checklist": {
						"selected_option_keys": ["cups", "buckets", "other"],
						"other_details": {"text": "Loose timber offcuts"},
						"question_note": "Auditor saw these loose parts in storage.",
					}
				}
			}
		}


def test_apply_draft_patch_round_trips_question_note_in_normalized_and_json_state() -> None:
	"""Question-level notes should persist alongside scale answers in both state shapes."""

	audit = _build_audit()

	patch = AuditDraftPatchRequest(
		sections={
			"section_a": SectionDraftPatchRequest(
				responses={
					"question_a": {
						"provision": "a_lot",
						"question_note": "Add more climbable vegetation near the boundary.",
					}
				}
			)
		}
	)

	with Session() as session:
		session.add(audit)

		apply_draft_patch_to_relations(audit=audit, patch=patch)

		section = audit.submission_sections[0]
		assert isinstance(section, PlayspaceSubmissionSection)
		assert section.section_key == "section_a"
		assert len(section.question_responses) == 1

		question_response = section.question_responses[0]
		assert isinstance(question_response, PlayspaceQuestionResponse)
		assert question_response.question_key == "question_a"
		assert question_response.note == "Add more climbable vegetation near the boundary."
		assert [answer.scale_key for answer in question_response.scale_answers] == ["provision"]
		assert [answer.option_key for answer in question_response.scale_answers] == ["a_lot"]

		assert build_responses_json_from_relations(audit)["sections"] == {
			"section_a": {
				"responses": {
					"question_a": {
						"provision": "a_lot",
						"question_note": "Add more climbable vegetation near the boundary.",
					}
				}
			}
		}


def test_apply_draft_patch_round_trips_final_comments_in_normalized_and_json_state() -> None:
	"""Audit-level final comments should persist through normalized state reconstruction."""

	audit = _build_audit()

	patch = AuditDraftPatchRequest(
		meta=AuditMetaPatchRequest(final_comments="Watch drainage near the north entry after heavy rain."),
	)

	with Session() as session:
		session.add(audit)

		apply_draft_patch_to_relations(audit=audit, patch=patch)

		assert audit.submission_context is not None
		assert audit.submission_context.final_comments == "Watch drainage near the north entry after heavy rain."
		assert build_responses_json_from_relations(audit)["meta"] == {
			"final_comments": "Watch drainage near the north entry after heavy rain."
		}


def test_build_responses_json_normalizes_legacy_stringified_checklist_answers() -> None:
	"""Legacy normalized rows should be readable when checklist payloads were stored as strings."""

	audit = _build_audit()
	section = PlayspaceSubmissionSection(submission_id=audit.id, section_key="section_a")
	question_response = PlayspaceQuestionResponse(section=section, question_key="question_checklist")
	question_response.scale_answers = [
		PlayspaceScaleAnswer(
			question_response=question_response,
			scale_key="selected_option_keys",
			option_key="['cups', 'buckets']",
		),
		PlayspaceScaleAnswer(
			question_response=question_response,
			scale_key="other_details",
			option_key="{'text': 'Large foam blocks'}",
		),
	]
	section.question_responses = [question_response]
	audit.submission_sections = [section]

	with Session() as session:
		session.add(audit)

		assert build_responses_json_from_relations(audit)["sections"] == {
			"section_a": {
				"responses": {
					"question_checklist": {
						"selected_option_keys": ["cups", "buckets"],
						"other_details": {"text": "Large foam blocks"},
					}
				}
			}
		}


def test_section_state_response_map_preserves_checklist_question_payloads() -> None:
	"""Session responses should round-trip checklist answers without dropping nested values."""

	service = _DummyAuditSessionsService()

	section_map = service._build_section_state_response_map(
		responses_json={
			"sections": {
				"section_a": {
					"responses": {
						"question_checklist": {
							"selected_option_keys": ["cups", "buckets", "other"],
							"other_details": {
								"text": "Large foam blocks",
							},
						}
					}
				}
			}
		}
	)

	assert section_map["section_a"].responses["question_checklist"] == {
		"selected_option_keys": ["cups", "buckets", "other"],
		"other_details": {
			"text": "Large foam blocks",
		},
	}


def test_section_state_response_map_normalizes_legacy_stringified_checklist_payloads() -> None:
	"""Session responses should repair stringified checklist payloads saved by older submissions."""

	service = _DummyAuditSessionsService()

	section_map = service._build_section_state_response_map(
		responses_json={
			"sections": {
				"section_a": {
					"responses": {
						"question_checklist": {
							"selected_option_keys": "['cups', 'buckets', 'other']",
							"other_details": "{'text': 'Large foam blocks'}",
						}
					}
				}
			}
		}
	)

	assert section_map["section_a"].responses["question_checklist"] == {
		"selected_option_keys": ["cups", "buckets", "other"],
		"other_details": {"text": "Large foam blocks"},
	}


def test_build_instrument_response_from_row_uses_database_metadata() -> None:
	"""Instrument API responses should trust DB row key/version over stale JSON content metadata."""

	instrument = _build_instrument_row(instrument_key="pvua_v5_2", instrument_version="5.13")

	response = build_instrument_response_from_row(instrument)

	assert response is not None
	assert response.instrument_key == "pvua_v5_2"
	assert response.instrument_version == "5.13"


def test_create_or_resume_audit_uses_active_instrument_version(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""New submissions should record the active DB instrument version instead of the static fallback."""

	active_instrument = _build_instrument_row(instrument_version="5.13")

	async def fake_get_active_instrument(_session: object, _instrument_key: str) -> Instrument:
		return active_instrument

	async def fake_get_instrument_version(
		_session: object,
		_instrument_key: str,
		_instrument_version: str,
	) -> Instrument:
		return active_instrument

	monkeypatch.setattr(audit_sessions_module, "get_active_instrument", fake_get_active_instrument)
	monkeypatch.setattr(audit_sessions_module, "get_instrument_version", fake_get_instrument_version)

	service = _DummyAuditService()
	actor = _build_actor(service._auditor_profile)

	session = asyncio.run(
		service.create_or_resume_audit(
			actor=actor,
			place_id=service._place.id,
			payload=PlaceAuditAccessRequest(
				project_id=service._project.id,
				execution_mode=ExecutionMode.AUDIT,
			),
		)
	)

	assert session.instrument_key == "pvua_v5_2"
	assert session.instrument_version == "5.13"
	assert session.instrument.instrument_version == "5.13"
	assert service._audit is not None
	assert service._audit.instrument_version == "5.13"


def test_audit_session_response_recovers_legacy_misstamped_instrument_version(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Legacy submissions stamped 5.2 should use active metadata when responses only match the active version."""

	stored_instrument = _build_instrument_row(instrument_version="5.2")
	active_instrument = _build_instrument_row(instrument_version="5.13")
	audit = _build_service_audit(execution_mode=ExecutionMode.BOTH, revision=2)
	audit.instrument_key = "pvua_v5_2"
	audit.instrument_version = "5.2"
	audit.responses_json = {
		"schema_version": 1,
		"revision": 2,
		"meta": {"execution_mode": "both"},
		"pre_audit": {},
		"sections": {
			"section_a": {
				"responses": {
					"question_5.13": {"selected_option_keys": ["cups"]},
				}
			}
		},
	}
	service = _DummyAuditService(audit=audit)

	async def fake_get_instrument_version(
		_session: object,
		_instrument_key: str,
		instrument_version: str,
	) -> Instrument | None:
		return stored_instrument if instrument_version == "5.2" else None

	async def fake_get_active_instrument(_session: object, _instrument_key: str) -> Instrument:
		return active_instrument

	monkeypatch.setattr(audit_sessions_module, "get_instrument_version", fake_get_instrument_version)
	monkeypatch.setattr(audit_sessions_module, "get_active_instrument", fake_get_active_instrument)

	response = asyncio.run(
		service._build_audit_session_response(
			audit=audit,
			project=audit.project,
			place=audit.place,
		)
	)

	assert response.instrument_version == "5.13"
	assert response.instrument.instrument_version == "5.13"


def test_audit_session_response_uses_submission_instrument_version(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	"""Existing submissions should render with their stored instrument version, not the active version."""

	stored_instrument = _build_instrument_row(instrument_version="5.13")
	active_instrument = _build_instrument_row(instrument_version="5.14")
	audit = _build_service_audit(execution_mode=ExecutionMode.AUDIT, revision=2)
	audit.instrument_key = "pvua_v5_2"
	audit.instrument_version = "5.13"
	service = _DummyAuditService(audit=audit)

	async def fake_get_instrument_version(
		_session: object,
		_instrument_key: str,
		instrument_version: str,
	) -> Instrument | None:
		return stored_instrument if instrument_version == "5.13" else None

	async def fake_get_active_instrument(_session: object, _instrument_key: str) -> Instrument:
		return active_instrument

	monkeypatch.setattr(audit_sessions_module, "get_instrument_version", fake_get_instrument_version)
	monkeypatch.setattr(audit_sessions_module, "get_active_instrument", fake_get_active_instrument)

	response = asyncio.run(
		service._build_audit_session_response(
			audit=audit,
			project=audit.project,
			place=audit.place,
		)
	)

	assert response.instrument_version == "5.13"
	assert response.instrument.instrument_version == "5.13"


def test_patch_audit_draft_updates_execution_mode_in_canonical_aggregate() -> None:
	"""Draft-save flow should persist the patched execution mode in canonical state."""

	audit = _build_service_audit(
		execution_mode=ExecutionMode.AUDIT,
		revision=2,
	)
	service = _DummyAuditService(audit=audit)
	actor = _build_actor(audit.auditor_profile)

	response = asyncio.run(
		service.patch_audit_draft(
			actor=actor,
			audit_id=audit.id,
			payload=AuditDraftPatchRequest(
				expected_revision=2,
				meta=AuditMetaPatchRequest(execution_mode=ExecutionMode.SURVEY),
			),
		)
	)

	assert response.revision == 3
	assert get_execution_mode_value(audit) == ExecutionMode.SURVEY.value
	assert audit.responses_json["meta"] == {"execution_mode": ExecutionMode.SURVEY.value}


def test_patch_audit_draft_updates_final_comments_in_canonical_aggregate() -> None:
	"""Draft-save flow should persist audit-level final comments in canonical state."""

	audit = _build_service_audit(
		execution_mode=ExecutionMode.AUDIT,
		revision=2,
	)
	service = _DummyAuditService(audit=audit)
	actor = _build_actor(audit.auditor_profile)

	response = asyncio.run(
		service.patch_audit_draft(
			actor=actor,
			audit_id=audit.id,
			payload=AuditDraftPatchRequest(
				expected_revision=2,
				meta=AuditMetaPatchRequest(final_comments="Observed heavy wear around the south entrance."),
			),
		)
	)

	assert response.revision == 3
	assert audit.responses_json["meta"] == {
		"execution_mode": ExecutionMode.AUDIT.value,
		"final_comments": "Observed heavy wear around the south entrance.",
	}

	session_response = asyncio.run(
		service._build_audit_session_response(
			audit=audit,
			project=audit.project,
			place=audit.place,
		)
	)
	assert session_response.meta.final_comments == "Observed heavy wear around the south entrance."


def test_patch_audit_draft_rejects_scalar_multiple_scale_with_typed_422(
	monkeypatch: pytest.MonkeyPatch,
) -> None:
	audit = _build_service_audit(execution_mode=ExecutionMode.BOTH, revision=2)
	audit.instrument_version = "5.32"
	service = _DummyAuditService(audit=audit)
	actor = _build_actor(audit.auditor_profile)
	instrument_path = (
		Path(__file__).parents[3]
		/ "app"
		/ "products"
		/ "playspace"
		/ "instruments"
		/ "pvua_v5_2__v5.32.instrument.json"
	)
	instrument = PlayspaceInstrumentResponse.model_validate(json.loads(instrument_path.read_text())["en"])

	async def fake_resolve_instrument(*, audit: PlayspaceSubmission) -> PlayspaceInstrumentResponse:
		return instrument

	monkeypatch.setattr(service, "_resolve_playspace_instrument_for_audit", fake_resolve_instrument)
	with pytest.raises(HTTPException) as exc_info:
		asyncio.run(
			service.patch_audit_draft(
				actor=actor,
				audit_id=audit.id,
				payload=AuditDraftPatchRequest(
					expected_revision=2,
					sections={
						"section_22_playspace_suitability_for_diverse_users": SectionDraftPatchRequest(
							responses={"q_22_1": {"sociability": "small_group"}}
						)
					},
				),
			)
		)

	assert exc_info.value.status_code == 422
	assert "requires an array" in exc_info.value.detail
	assert service.commit_count == 0


def test_create_or_resume_audit_keeps_existing_draft_execution_mode() -> None:
	"""Access requests must not mutate an existing draft's execution mode."""

	audit = _build_service_audit(
		execution_mode=ExecutionMode.AUDIT,
		revision=3,
	)
	service = _DummyAuditService(audit=audit)
	actor = _build_actor(audit.auditor_profile)

	session = asyncio.run(
		service.create_or_resume_audit(
			actor=actor,
			place_id=audit.place_id,
			payload=PlaceAuditAccessRequest(
				project_id=audit.project_id,
				execution_mode=ExecutionMode.SURVEY,
			),
		)
	)

	assert session.selected_execution_mode is ExecutionMode.AUDIT
	assert get_execution_mode_value(audit) == ExecutionMode.AUDIT.value
	assert get_aggregate_revision(audit) == 3
	assert service.commit_count == 0


def test_patch_audit_draft_rejects_stale_revision_without_debug_output(
	capsys: pytest.CaptureFixture[str],
) -> None:
	"""Draft saves should still reject stale revisions and emit no debug noise."""

	audit = _build_service_audit(
		execution_mode=ExecutionMode.AUDIT,
		revision=5,
	)
	service = _DummyAuditService(audit=audit)
	actor = _build_actor(audit.auditor_profile)

	with pytest.raises(HTTPException) as exc_info:
		asyncio.run(
			service.patch_audit_draft(
				actor=actor,
				audit_id=audit.id,
				payload=AuditDraftPatchRequest(
					expected_revision=4,
				),
			)
		)

	assert exc_info.value.status_code == 409
	assert capsys.readouterr().out == ""
	assert service.commit_count == 0


def test_submit_audit_rejects_stale_revision() -> None:
	"""Submit locks its auditor before loading data and rejects stale revisions."""

	audit = _build_service_audit(
		execution_mode=ExecutionMode.AUDIT,
		revision=7,
	)
	service = _DummyAuditService(audit=audit)
	actor = _build_actor(audit.auditor_profile)

	with pytest.raises(HTTPException) as exc_info:
		asyncio.run(
			service.submit_audit(
				actor=actor,
				audit_id=audit.id,
				payload=AuditSubmitRequest(expected_revision=6),
			)
		)

	assert exc_info.value.status_code == 409
	assert service.submit_operation_order == ["lock_auditor_profile", "load_audit"]
	assert service.commit_count == 0


def test_ensure_not_submitted_raises_without_debug_output(
	capsys: pytest.CaptureFixture[str],
) -> None:
	"""Submitted-audit guard should raise 409s without printing audit internals."""

	audit = _build_service_audit(
		execution_mode=ExecutionMode.AUDIT,
		revision=2,
		status=AuditStatus.SUBMITTED,
	)

	with pytest.raises(HTTPException) as exc_info:
		PlayspaceAuditService._ensure_not_submitted(
			audit=audit,
			detail="Submitted audits cannot be edited.",
		)

	assert exc_info.value.status_code == 409
	assert capsys.readouterr().out == ""


def _build_pristine_service_audit(*, started_at: datetime, revision: int = 1) -> PlayspaceSubmission:
	"""Create a pristine in-progress audit anchored at one explicit access-time timestamp."""

	audit = _build_service_audit(revision=revision)
	audit.started_at = started_at
	# Pristine: no execution_mode, no pre_audit content, no section content.
	audit.responses_json = {
		"schema_version": 1,
		"revision": revision,
		"meta": {},
		"pre_audit": {},
		"sections": {},
	}
	audit.execution_mode = None
	return audit


def test_patch_audit_draft_accepts_later_started_at_on_pristine_audit() -> None:
	"""A pristine audit should accept a later mobile execute-time started_at."""

	access_time = datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc)
	execute_time = datetime(2026, 5, 26, 10, 3, 30, tzinfo=timezone.utc)
	audit = _build_pristine_service_audit(started_at=access_time, revision=2)
	service = _DummyAuditService(audit=audit)
	actor = _build_actor(audit.auditor_profile)

	response = asyncio.run(
		service.patch_audit_draft(
			actor=actor,
			audit_id=audit.id,
			payload=AuditDraftPatchRequest(
				expected_revision=2,
				started_at=execute_time,
			),
		)
	)

	assert response.revision == 3
	assert audit.started_at == execute_time


def test_patch_audit_draft_rejects_started_at_when_audit_has_progress() -> None:
	"""Audits that already have content must not accept a started_at correction."""

	access_time = datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc)
	execute_time = datetime(2026, 5, 26, 10, 5, 0, tzinfo=timezone.utc)
	audit = _build_pristine_service_audit(started_at=access_time, revision=2)
	# Mark the audit as no longer pristine - execution mode has been chosen.
	set_execution_mode_value(audit=audit, execution_mode=ExecutionMode.AUDIT.value)
	service = _DummyAuditService(audit=audit)
	actor = _build_actor(audit.auditor_profile)

	with pytest.raises(HTTPException) as exc_info:
		asyncio.run(
			service.patch_audit_draft(
				actor=actor,
				audit_id=audit.id,
				payload=AuditDraftPatchRequest(
					expected_revision=2,
					started_at=execute_time,
				),
			)
		)

	assert exc_info.value.status_code == 400
	assert audit.started_at == access_time


def test_patch_audit_draft_rejects_earlier_started_at() -> None:
	"""A correction earlier than the server's current placeholder must be rejected."""

	access_time = datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc)
	earlier_time = datetime(2026, 5, 26, 9, 55, 0, tzinfo=timezone.utc)
	audit = _build_pristine_service_audit(started_at=access_time, revision=2)
	service = _DummyAuditService(audit=audit)
	actor = _build_actor(audit.auditor_profile)

	with pytest.raises(HTTPException) as exc_info:
		asyncio.run(
			service.patch_audit_draft(
				actor=actor,
				audit_id=audit.id,
				payload=AuditDraftPatchRequest(
					expected_revision=2,
					started_at=earlier_time,
				),
			)
		)

	assert exc_info.value.status_code == 400
	assert audit.started_at == access_time


def test_submit_total_minutes_uses_corrected_started_at() -> None:
	"""After a started_at correction lands on the audit, elapsed_minutes uses the new value."""

	access_time = datetime(2026, 5, 26, 10, 0, 0, tzinfo=timezone.utc)
	execute_time = datetime(2026, 5, 26, 10, 4, 0, tzinfo=timezone.utc)
	submitted_at = datetime(2026, 5, 26, 10, 30, 0, tzinfo=timezone.utc)
	audit = _build_pristine_service_audit(started_at=access_time, revision=2)
	service = _DummyAuditService(audit=audit)
	actor = _build_actor(audit.auditor_profile)

	asyncio.run(
		service.patch_audit_draft(
			actor=actor,
			audit_id=audit.id,
			payload=AuditDraftPatchRequest(
				expected_revision=2,
				started_at=execute_time,
			),
		)
	)

	# Mirror submit_audit's elapsed_minutes formula directly against the corrected value.
	elapsed_minutes = int((submitted_at - audit.started_at).total_seconds() // 60)
	# 30 min (access→submit) would round to 30; 4 min execute→submit window leaves 26 min.
	assert elapsed_minutes == 26
	# Sanity check: the original access-time window would have produced 30.
	assert int((submitted_at - access_time).total_seconds() // 60) == 30

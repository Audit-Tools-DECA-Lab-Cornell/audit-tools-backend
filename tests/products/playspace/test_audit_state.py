"""Regression tests for Playspace canonical audit state synchronization."""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import cast

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actors import CurrentUserContext, CurrentUserRole
from app.models import (
	Audit,
	AuditorAssignment,
	AuditorProfile,
	AuditStatus,
	JSONDict,
	Place,
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
from app.products.playspace.schemas.instrument import ExecutionMode
from app.products.playspace.services.audit import PlayspaceAuditService
from app.products.playspace.services.audit_sessions import PlayspaceAuditSessionsMixin


def _build_audit() -> Audit:
	"""Create an in-memory audit shell for relationship synchronization tests."""

	return Audit(
		id=uuid.uuid4(),
		project_id=uuid.uuid4(),
		place_id=uuid.uuid4(),
		auditor_profile_id=uuid.uuid4(),
		audit_code=f"AUDIT-{uuid.uuid4()}",
		instrument_key="pvua_v5_2",
		instrument_version="5.2",
		status=AuditStatus.IN_PROGRESS,
		started_at=datetime.now(timezone.utc),
		responses_json={"meta": {}, "pre_audit": {}, "sections": {}},
		scores_json={},
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
) -> Audit:
	"""Create an audit shell with related project, place, and auditor objects."""

	project = _build_project()
	place = _build_place()
	auditor_profile = _build_auditor_profile()
	now = datetime.now(timezone.utc)
	audit = Audit(
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
	)
	audit.project = project
	audit.place = place
	audit.auditor_profile = auditor_profile
	audit.updated_at = now

	if execution_mode is not None:
		set_execution_mode_value(audit=audit, execution_mode=execution_mode.value)
		set_aggregate_revision(audit, revision)

	return audit


class _DummyAuditSessionsService(PlayspaceAuditSessionsMixin):
	"""Minimal mixin host used for focused response-shape tests."""


class _DummySession:
	"""Minimal session stub that records added audit objects."""

	def __init__(self) -> None:
		self.added_audits: list[Audit] = []

	def add(self, instance: Audit) -> None:
		"""Record one added audit without touching a database."""

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
		audit: Audit | None = None,
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

	async def _commit_and_refresh(self, instance: Audit | AuditorAssignment) -> None:
		"""Track commit calls and refresh timestamps without a real session."""

		self.commit_count += 1
		if isinstance(instance, Audit):
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
	) -> Audit | None:
		"""Return the preconfigured in-memory audit."""

		return self._audit

	async def _load_accessible_audit(
		self,
		*,
		actor: CurrentUserContext,
		audit_id: uuid.UUID,
	) -> Audit:
		"""Return the preconfigured in-memory audit."""

		if self._audit is None:
			raise AssertionError("Dummy audit must be configured before loading it.")
		return self._audit


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
						"diversity": "some_diversity",
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
					"diversity": "some_diversity",
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


def test_section_state_response_map_preserves_checklist_question_payloads() -> None:
	"""Session responses should round-trip checklist answers without dropping nested values."""

	service = _DummyAuditSessionsService()

	section_map = service._build_section_state_response_map(
		responses_json={
			"sections": {
				"section_a": {
					"responses": {
						"question_checklist": {
							"selected_option_keys": ["cups", "buckets"],
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
		"selected_option_keys": ["cups", "buckets"],
		"other_details": {
			"text": "Large foam blocks",
		},
	}


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
	"""Submit flow should still reject stale expected revisions with HTTP 409."""

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

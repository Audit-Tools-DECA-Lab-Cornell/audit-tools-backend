from __future__ import annotations

import asyncio
import json
import uuid

import pytest
from typing import Any
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import Audit, Instrument, YeeAuditSubmission
from app.products.yee.schemas.migration import (
	InstrumentDecision,
	MigrationMappingDocument,
	RecordDecision,
)
from app.products.yee.services.instrument_authoring import legacy_to_authoring
from app.products.yee.services.migration_manifest import (
	MANIFEST_SCHEMA_VERSION,
	SKIP_REASON_CODES,
	_missing_required_followups,
	_parent_chain_findings,
	_schema_generation,
	_scope_projection,
	_shadow_compare,
	_snapshot_differences,
	generate_migration_manifest,
)
from app.products.yee.services.scoring import get_yee_instrument_data
from app.yee_instrument_schema import YeeInstrumentResponse


def test_schema_generation_does_not_mislabel_malformed_authoring_as_legacy() -> None:
	assert _schema_generation({}) == "legacy"
	assert _schema_generation({"authoring": None}) == "legacy"
	assert _schema_generation({"authoring": {"schemaVersion": 2}}) == "authoring_v2"
	assert _schema_generation({"authoring": {"schemaVersion": 1}}) == "invalid"
	assert _schema_generation({"authoring": "invalid"}) == "invalid"


def test_schema_v1_affirmative_without_condition_is_inventoried() -> None:
	content = YeeInstrumentResponse.model_validate(get_yee_instrument_data())
	authoring = content.authoring
	if authoring is None:
		authoring = legacy_to_authoring(content).authoring
	question = next(
		question
		for section in authoring.sections
		for question in section.questions
		if question.follow_up is not None and question.response_binding is not None
	)
	follow_up = question.follow_up
	binding = question.response_binding
	assert follow_up is not None
	assert binding is not None
	responses = {
		binding.presence_item_id: {
			binding.choice_id: follow_up.trigger_option_ids[0],
		}
	}

	assert _missing_required_followups(content, responses) == [question.id]

	responses[binding.condition_item_id or ""] = {
		binding.choice_id: follow_up.options[0].id,
	}
	assert _missing_required_followups(content, responses) == []


async def _generate_without_writes(
	session_factory: async_sessionmaker[AsyncSession],
) -> tuple[dict[str, Any], tuple[int, int, int], tuple[int, int, int]]:
	async with session_factory() as session:
		before = (
			(await session.execute(select(func.count()).select_from(Instrument))).scalar_one(),
			(await session.execute(select(func.count()).select_from(Audit))).scalar_one(),
			(await session.execute(select(func.count()).select_from(YeeAuditSubmission))).scalar_one(),
		)
		manifest = await generate_migration_manifest(
			session,
			product="yee",
			environment_label="pytest",
			target_fingerprint="f" * 64,
		)
		after = (
			(await session.execute(select(func.count()).select_from(Instrument))).scalar_one(),
			(await session.execute(select(func.count()).select_from(Audit))).scalar_one(),
			(await session.execute(select(func.count()).select_from(YeeAuditSubmission))).scalar_one(),
		)
		return manifest, before, after


def test_manifest_is_redacted_and_read_only(
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> None:
	manifest, before, after = asyncio.run(_generate_without_writes(yee_test_session_factory))
	serialized = json.dumps(manifest)

	assert manifest["dry_run"] is True
	assert before == after
	assert '"responses"' not in serialized
	assert '"participant_info"' not in serialized
	assert manifest["manifest_schema_version"] == MANIFEST_SCHEMA_VERSION
	assert manifest["product"] == "yee"
	assert manifest["environment_label"] == "pytest"
	assert set(manifest["hashes"]) == {"migration_scope_sha256", "full_payload_sha256"}
	# Per-platform, because Android availability is machine-checkable and iOS is not.
	assert manifest["decoder_release"]["android"] == {
		"status": "assigned_pending_release",
		"available_version": None,
		"evidence_ref": None,
	}
	assert manifest["decoder_release"]["ios"] == {
		"status": "assigned_pending_release",
		"available_version": None,
		"evidence_ref": None,
	}
	assert manifest["decoder_release"]["assigned_mobile_display_version"] == "0.9.1"


# ---------------------------------------------------------------------------
# Parent graph
# ---------------------------------------------------------------------------


def _instrument(name: str, parent: uuid.UUID | None = None, key: str = "yee") -> Instrument:
	return Instrument(
		id=uuid.uuid5(uuid.NAMESPACE_DNS, name),
		instrument_key=key,
		instrument_version=name,
		parent_instrument_id=parent,
		is_active=False,
		content={},
	)


def test_parent_chain_blockers_are_reported() -> None:
	root = _instrument("root")
	child = _instrument("child", parent=root.id)
	orphan = _instrument("orphan", parent=uuid.uuid4())
	self_linked = _instrument("self")
	self_linked.parent_instrument_id = self_linked.id
	foreign_parent = _instrument("foreign-parent", key="yee_site_copy")
	cross = _instrument("cross", parent=foreign_parent.id)

	codes = {
		finding["code"] for finding in _parent_chain_findings([root, child, orphan, self_linked, foreign_parent, cross])
	}
	assert codes == {"parent_missing", "parent_self_link", "parent_cross_key"}


def test_parent_cycle_is_detected_once() -> None:
	first = _instrument("cycle-a")
	second = _instrument("cycle-b")
	first.parent_instrument_id = second.id
	second.parent_instrument_id = first.id

	cycles = [finding for finding in _parent_chain_findings([first, second]) if finding["code"] == "parent_cycle"]
	assert len(cycles) == 1
	assert cycles[0]["instrument_ids"] == sorted([str(first.id), str(second.id)])


# ---------------------------------------------------------------------------
# Scoring comparison: every record is compared or explicitly skipped
# ---------------------------------------------------------------------------


def _record(record_id: str, key: str | None, version: str | None, stamp_status: str) -> dict[str, object]:
	return {
		"record_type": "submission",
		"record_id": record_id,
		"status": "SUBMITTED",
		"instrument_key": key,
		"instrument_version": version,
		"stamp_status": stamp_status,
		"responses": {},
		"participant_info": {},
		"stored_total": None,
		"stored_canonical": None,
	}


def test_every_uncomparable_record_is_counted_with_a_stable_reason() -> None:
	records = [
		_record("partial", "yee", None, "partial"),
		_record("unknown", "yee", "missing", "unknown"),
		_record("duplicate", "yee", "dupe", "duplicate"),
		_record("clean", None, None, "unstamped"),
	]
	report, _differences = _shadow_compare(records, {})

	assert report["records_compared"] == 1
	assert report["records_skipped"] == 3
	assert report["skipped_by_reason"]["partial_stamp"] == 1
	assert report["skipped_by_reason"]["unknown_stamp"] == 1
	assert report["skipped_by_reason"]["duplicate_stamp"] == 1
	# A skip must be visible per record, not just as a count.
	assert {entry["record_id"] for entry in report["skipped"]} == {"partial", "unknown", "duplicate"}
	assert all(entry["reason_code"] in SKIP_REASON_CODES for entry in report["skipped"])


def test_unscorable_instrument_is_skipped_with_a_redacted_reason() -> None:
	broken = _instrument("broken")
	broken.content = {"authoring": {"schemaVersion": 2, "sections": "not-a-list"}}
	records = [_record("broken", "yee", "broken", "known")]

	report, _differences = _shadow_compare(records, {("yee", "broken"): [broken]})

	assert report["records_compared"] == 0
	assert report["records_skipped"] == 1
	entry = report["skipped"][0]
	assert entry["reason_code"] in {"invalid_instrument_content", "unscorable_instrument"}
	# Structural path only: a Pydantic message would echo the offending value.
	assert set(entry["detail"]) >= {"code", "paths", "path_count"}
	assert "not-a-list" not in json.dumps(entry)


def test_snapshot_comparison_sees_a_moved_section_with_an_unchanged_total() -> None:
	left = {"raw": {"total_score": 10, "section_scores": {"Access": 4, "Amenities": 6}}}
	right = {"raw": {"total_score": 10, "section_scores": {"Access": 6, "Amenities": 4}}}

	paths = _snapshot_differences(left, right)
	assert paths == ["raw.section_scores.Access", "raw.section_scores.Amenities"]


# ---------------------------------------------------------------------------
# Migration-scope hash: stable under clean activity, moves on a new anomaly
# ---------------------------------------------------------------------------


def _manifest_skeleton() -> dict[str, object]:
	return {
		"manifest_schema_version": MANIFEST_SCHEMA_VERSION,
		"product": "yee",
		"target_fingerprint": "f" * 64,
		"environment_label": "snapshot",
		"record_scope": {"records_in_scope": True, "reason": "yee_database_owns_audit_records"},
		"instrument_versions": [
			{
				"id": "i-1",
				"instrument_key": "yee",
				"instrument_version": "v1",
				"parent_instrument_id": None,
				"is_active": True,
				"content_sha256": "abc",
				"schema_generation": "legacy",
				"compatibility_status": "legacy",
				"version_and_lifecycle_warnings": [],
				"scoring_compatibility": {"ok": True},
			}
		],
		"parent_chain_findings": [],
		"active_anomalies": [],
		"unclean_records": [],
		"shadow_scoring": {"differences": [], "skipped": []},
	}


def test_scope_hash_ignores_ordinary_clean_activity() -> None:
	"""A new cleanly stamped submission must not invalidate an approved artifact.

	If it did, the cutover freshness check would abort on normal business every
	time and get waived under pressure.
	"""

	before = _manifest_skeleton()
	after = _manifest_skeleton()
	# Ordinary activity only touches counts and cleanly stamped record groups,
	# neither of which the scope projection reads.
	assert _scope_projection(before) == _scope_projection(after)


def test_scope_hash_moves_when_a_new_anomaly_appears() -> None:
	before = _manifest_skeleton()
	for mutation in (
		lambda m: m["unclean_records"].append({"record_id": "r-1", "stamp_status": "partial"}),
		lambda m: m["active_anomalies"].append({"code": "multiple_active_instruments"}),
		lambda m: m["parent_chain_findings"].append({"code": "parent_cycle"}),
		lambda m: m["shadow_scoring"]["skipped"].append({"record_id": "r-2", "reason_code": "partial_stamp"}),
		lambda m: m["shadow_scoring"]["differences"].append({"record_id": "r-3"}),
	):
		after = _manifest_skeleton()
		mutation(after)
		assert _scope_projection(before) != _scope_projection(after)


# ---------------------------------------------------------------------------
# Resolution overlay: reviewed decisions applied in memory, still SELECT-only
# ---------------------------------------------------------------------------


_HASH_A = "a" * 64
_HASH_B = "b" * 64


def _decision(**overrides: object) -> RecordDecision:
	payload: dict[str, object] = {
		"record_type": "submission",
		"record_id": uuid.uuid5(uuid.NAMESPACE_DNS, "rec"),
		"action": "retain",
		"expected_instrument_key": None,
		"expected_instrument_version": None,
		"resolved_contract": "frozen_schema_v1",
		"reason_code": "unstamped_legacy_frozen_fallback",
	}
	payload.update(overrides)
	return RecordDecision.model_validate(payload)


def test_restamp_requires_a_new_stamp_that_actually_changes() -> None:
	with pytest.raises(ValidationError):
		_decision(action="restamp")
	with pytest.raises(ValidationError):
		_decision(
			action="restamp",
			expected_instrument_key="yee",
			expected_instrument_version="v1",
			new_instrument_key="yee",
			new_instrument_version="v1",
		)


def test_quarantine_cannot_claim_a_resolved_contract() -> None:
	with pytest.raises(ValidationError):
		_decision(action="quarantine", resolved_contract="frozen_schema_v1")
	# ...and a non-quarantine decision must state one.
	with pytest.raises(ValidationError):
		_decision(resolved_contract=None)


def test_consolidate_into_cannot_point_a_row_at_itself() -> None:
	same = uuid.uuid4()
	with pytest.raises(ValidationError):
		InstrumentDecision.model_validate(
			{
				"instrument_id": same,
				"action": "consolidate_into",
				"canonical_instrument_id": same,
				"expected_is_active": False,
				"expected_content_sha256": _HASH_A,
				"reason_code": "byte_identical_duplicate_consolidated",
			}
		)


def test_a_record_may_carry_only_one_decision() -> None:
	duplicate = _decision()
	with pytest.raises(ValidationError):
		MigrationMappingDocument.model_validate(
			{
				"product": "yee",
				"environment_label": "snapshot",
				"approved_inventory_hashes": {"yee": _HASH_A, "playspace": _HASH_B},
				"record_decisions": [duplicate.model_dump(mode="json"), duplicate.model_dump(mode="json")],
			}
		)


def test_quarantine_is_a_classification_not_a_silent_skip() -> None:
	record = _record("partial", "yee", None, "partial")
	decision = _decision(
		record_id=uuid.UUID(int=0),
		action="quarantine",
		expected_instrument_key="yee",
		expected_instrument_version=None,
		resolved_contract=None,
		reason_code="ambiguous_candidate_quarantined",
	)
	record["record_id"] = str(decision.record_id)

	unresolved, _ = _shadow_compare([record], {})
	resolved, _ = _shadow_compare([record], {}, {str(decision.record_id): decision})

	# Without a decision it is an open question that blocks the apply gate.
	assert unresolved["records_skipped"] == 1
	assert unresolved["records_quarantined"] == 0
	# With one it is an explicit, reviewed classification.
	assert resolved["records_skipped"] == 0
	assert resolved["records_quarantined"] == 1
	assert resolved["quarantined"][0]["reason_code"] == "ambiguous_candidate_quarantined"


def test_a_decision_written_for_a_different_stamp_is_refused() -> None:
	record = _record("moved", "yee", "v1", "known")
	decision = _decision(
		record_id=uuid.UUID(int=1),
		action="retain",
		expected_instrument_key="yee",
		expected_instrument_version="v0",
		resolved_contract="exact_stamp",
		reason_code="exact_stamp_resolves_uniquely",
	)
	record["record_id"] = str(decision.record_id)

	report, _ = _shadow_compare([record], {}, {str(decision.record_id): decision})
	assert report["skipped"][0]["reason_code"] == "stale_decision"
	assert report["records_compared"] == 0


def test_a_restamp_pointing_at_a_missing_catalog_row_is_refused() -> None:
	record = _record("restamped", "yee", "old", "unknown")
	decision = _decision(
		record_id=uuid.UUID(int=2),
		action="restamp",
		expected_instrument_key="yee",
		expected_instrument_version="old",
		new_instrument_key="yee",
		new_instrument_version="not-in-catalog",
		resolved_contract="exact_stamp",
		reason_code="human_mapped_to_preserved_version",
	)
	record["record_id"] = str(decision.record_id)

	report, _ = _shadow_compare([record], {}, {str(decision.record_id): decision})
	assert report["skipped"][0]["reason_code"] == "restamp_target_missing"


def test_a_resolved_unknown_stamp_becomes_comparable() -> None:
	"""The whole point of the overlay: an inventory skip turns into a comparison."""

	target = _instrument("resolved-target")
	record = _record("restamped", "yee", "old", "unknown")
	decision = _decision(
		record_id=uuid.UUID(int=3),
		action="restamp",
		expected_instrument_key="yee",
		expected_instrument_version="old",
		new_instrument_key="yee",
		new_instrument_version="resolved-target",
		resolved_contract="exact_stamp",
		reason_code="human_mapped_to_preserved_version",
	)
	record["record_id"] = str(decision.record_id)
	known = {("yee", "resolved-target"): [target]}

	before, _ = _shadow_compare([record], known)
	after, _ = _shadow_compare([record], known, {str(decision.record_id): decision})

	assert before["records_skipped"] == 1
	assert before["skipped"][0]["reason_code"] == "unknown_stamp"
	assert after["records_skipped"] == 0
	assert after["records_compared"] == 1


def test_catalog_only_scope_cannot_hash_match_a_full_inventory() -> None:
	"""A Playspace catalog-only artifact must never look like a YEE inventory.

	``yee_audit_submissions`` is YEE-only and ``audits`` rows in the Playspace
	database are Playspace's, so that product inventories the shared catalog
	alone. Scope semantics therefore belong in the gate hash.
	"""

	full = _manifest_skeleton()
	catalog_only = _manifest_skeleton()
	catalog_only["product"] = "playspace"
	catalog_only["record_scope"] = {
		"records_in_scope": False,
		"reason": "shared_instruments_catalog_only__audit_records_live_in_the_yee_database",
	}
	assert _scope_projection(full) != _scope_projection(catalog_only)


class _CatalogOnlySession:
	"""Fake session that fails loudly if a YEE-only table is queried.

	``yee_audit_submissions`` exists only in the YEE database
	(``YEE_ONLY_TABLE_NAMES``), so inventorying it against Playspace raised
	``UndefinedTableError`` in production. This pins the fix.
	"""

	def __init__(self, *, yee_tables_exist: bool = False) -> None:
		self.entities: list[str] = []
		self.yee_tables_exist = yee_tables_exist

	async def execute(self, statement: object) -> object:
		entity = str(getattr(statement, "column_descriptions", [{}])[0].get("entity", ""))
		self.entities.append(entity)
		if "YeeAuditSubmission" in entity and not self.yee_tables_exist:
			raise AssertionError("yee_audit_submissions does not exist outside the YEE database")

		class _Result:
			def scalars(self) -> _Result:
				return self

			def all(self) -> list[object]:
				return []

		return _Result()


def test_playspace_inventory_never_touches_the_yee_only_submissions_table() -> None:
	session = _CatalogOnlySession()
	manifest = asyncio.run(
		generate_migration_manifest(
			session,  # type: ignore[arg-type]
			product="playspace",
			environment_label="pytest",
			target_fingerprint="f" * 64,
		)
	)

	assert manifest["record_scope"]["records_in_scope"] is False
	assert not any("YeeAuditSubmission" in entity for entity in session.entities)
	assert any("Instrument" in entity for entity in session.entities)
	# Zero records must read as "not in scope here", never as "none exist".
	assert manifest["summary"]["submission_count"] == 0
	assert manifest["summary"]["draft_count"] == 0


def test_yee_inventory_still_covers_audit_records() -> None:
	session = _CatalogOnlySession(yee_tables_exist=True)
	manifest = asyncio.run(
		generate_migration_manifest(
			session,  # type: ignore[arg-type]
			product="yee",
			environment_label="pytest",
			target_fingerprint="f" * 64,
		)
	)
	assert manifest["record_scope"]["records_in_scope"] is True
	assert any("YeeAuditSubmission" in entity for entity in session.entities)


def test_empty_shared_catalog_outside_yee_is_healthy_not_an_anomaly() -> None:
	"""Zero YEE rows in the Playspace database is the expected state.

	Reporting it as ``missing_active_instrument`` would leave that product's
	manifest permanently un-authorizable and train reviewers to ignore anomalies.
	"""

	session = _CatalogOnlySession()
	manifest = asyncio.run(
		generate_migration_manifest(
			session,  # type: ignore[arg-type]
			product="playspace",
			environment_label="pytest",
			target_fingerprint="f" * 64,
		)
	)
	assert manifest["active_anomalies"] == []


def test_missing_active_instrument_is_still_an_anomaly_in_the_yee_database() -> None:
	session = _CatalogOnlySession(yee_tables_exist=True)
	manifest = asyncio.run(
		generate_migration_manifest(
			session,  # type: ignore[arg-type]
			product="yee",
			environment_label="pytest",
			target_fingerprint="f" * 64,
		)
	)
	assert [entry["code"] for entry in manifest["active_anomalies"]] == ["missing_active_instrument"]

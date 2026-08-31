"""Read-only Phase 3 migration inventory for the YEE instrument catalog.

Classifies every instrument row, active flag, parent chain, draft stamp, and
submission stamp in ONE physical product database so a human can adjudicate the
history before any write happens. The generator is SELECT-only by construction:
it opens no transaction of its own, adds nothing to the session, and never
commits.

Two hashes ride on every artifact:

- ``migration_scope_sha256`` is the mutation gate. It covers the catalog, the
  parent graph, the anomaly groups, and the exact records that are NOT cleanly
  stamped. It deliberately excludes record counts and cleanly stamped record
  IDs, so ordinary auditor activity between approval and apply cannot invalidate
  an approved artifact — while any NEW anomaly still changes it.
- ``full_payload_sha256`` covers the whole redacted payload and is evidence
  only. It moves with ordinary activity and must never be used as a gate.

Redaction is a hard requirement: responses, participant information, names,
emails, tokens, and database URLs never enter the payload. Validation and
scoring failures are reported as a stable code plus a structural path, never a
raw exception string, because Pydantic embeds offending input values in its
messages.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any, cast

from pydantic import ValidationError
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Audit, AuditStatus, Instrument, YeeAuditSubmission
from app.products.yee.schemas.migration import MigrationMappingDocument, RecordDecision
from app.products.yee.services.instrument_authoring import legacy_to_authoring
from app.products.yee.services.instrument_drafts import _compatibility_status
from app.products.yee.services.scoring import get_yee_instrument_data, score_yee_responses
from app.products.yee.services.scoring_contract import validate_scoring_compatibility
from app.products.yee.services.scoring_resolution import (
	ScoringContractResolutionError,
	scoring_contract_from_instrument,
)
from app.products.yee.services.scoring_spec import SCHEMA_V1_SCORING_CONTRACT
from app.products.yee.services.scoring_types import JsonValue
from app.products.yee.services.submission_validation import find_incomplete_responses
from app.yee_instrument_schema import YeeInstrumentResponse

MANIFEST_SCHEMA_VERSION = 1

ASSIGNED_FIRST_AUTHORING_V2_MOBILE_DISPLAY_VERSION = "0.9.1"

#: Every reason a record can be left out of the scoring comparison. Stable
#: strings: the authorization gate counts by these, and Step 2's resolution
#: overlay has to resolve each one explicitly rather than let it vanish.
SKIP_REASON_CODES = (
	"partial_stamp",
	"unknown_stamp",
	"duplicate_stamp",
	"invalid_instrument_content",
	"unscorable_instrument",
	"stale_decision",
	"restamp_target_missing",
)


class ResolutionOverlayError(RuntimeError):
	"""The mapping document does not match the database it was applied to.

	Raised before any comparison so a stale or mis-targeted document can never
	produce an authorization manifest.
	"""


def _canonical_json(payload: object) -> str:
	"""Deterministic JSON for hashing: sorted keys, no incidental whitespace."""

	return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def _sha256(payload: object) -> str:
	return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def _schema_generation(content: object) -> str:
	if not isinstance(content, dict):
		return "invalid"
	if "authoring" not in content or content["authoring"] is None:
		return "legacy"
	authoring = content.get("authoring")
	return "authoring_v2" if isinstance(authoring, dict) and authoring.get("schemaVersion") == 2 else "invalid"


def _redacted_error(code: str, error: Exception) -> dict[str, Any]:
	"""A structural description of a failure, never the raw message.

	``ValidationError`` and ``ScoringContractResolutionError`` both embed the
	offending value in ``str(error)``. Only the location survives here.
	"""

	match error:
		case ValidationError():
			paths = sorted(
				{".".join(str(part) for part in item["loc"]) or "<root>" for item in error.errors(include_url=False)}
			)
			return {"code": code, "paths": paths[:20], "path_count": len(paths)}
		case ScoringContractResolutionError():
			return {
				"code": code,
				"detail_code": error.code,
				"paths": [error.field or "<root>"],
				"question_id": error.question_id,
				"path_count": 1,
			}
		case _:
			return {"code": code, "paths": ["<unknown>"], "path_count": 1}


def _stamp_status(
	key: str | None,
	version: str | None,
	known_stamps: Mapping[tuple[str, str], list[Instrument]],
) -> str:
	if key is None and version is None:
		return "unstamped"
	if key is None or version is None:
		return "partial"
	rows = known_stamps.get((key, version), [])
	if not rows:
		return "unknown"
	return "duplicate" if len(rows) > 1 else "known"


def _missing_required_followups(
	content: YeeInstrumentResponse,
	responses: Mapping[str, Any],
) -> list[str]:
	"""Shown-but-unanswered required follow-ups, for inventory counting only.

	Delegates to the shared submit-path validator so the inventory can never
	report a record as complete that the submit rule would reject, or vice versa.
	"""

	return list(find_incomplete_responses(content, responses).missing_follow_up_question_ids)


def _record_payload(
	*,
	record_type: str,
	record_id: object,
	status: str,
	instrument_key: str | None,
	instrument_version: str | None,
	responses: Mapping[str, Any],
	participant_info: Mapping[str, Any],
	stored_total: int | float | None,
	stored_canonical: object,
) -> dict[str, Any]:
	return {
		"record_type": record_type,
		"record_id": str(record_id),
		"status": status,
		"instrument_key": instrument_key,
		"instrument_version": instrument_version,
		"responses": responses,
		"participant_info": participant_info,
		"stored_total": stored_total,
		"stored_canonical": stored_canonical,
	}


def _draft_payload(row: Audit) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
	raw = row.responses_json if isinstance(row.responses_json, dict) else {}
	participant_info = raw.get("participant_info")
	responses = raw.get("responses")
	if isinstance(participant_info, dict) and isinstance(responses, dict):
		return participant_info, responses
	return {}, raw


def _stored_canonical(scores_json: object) -> object:
	"""The stored canonical snapshot, however this row nests it."""

	if not isinstance(scores_json, dict):
		return None
	nested = scores_json.get("canonical_score")
	if isinstance(nested, dict):
		return nested
	return scores_json if "raw" in scores_json else None


# ---------------------------------------------------------------------------
# Catalog: parent graph
# ---------------------------------------------------------------------------


def _parent_chain_findings(instruments: Sequence[Instrument]) -> list[dict[str, Any]]:
	"""Self-links, dangling parents, cross-key parents, and cycles.

	Every one of these is a catalog blocker: Phase 3 records the activation
	candidate's parent as its rollback target, so an unresolvable parent graph
	means there is no provable way back.
	"""

	by_id = {row.id: row for row in instruments}
	findings: list[dict[str, Any]] = []

	for row in instruments:
		parent_id = row.parent_instrument_id
		if parent_id is None:
			continue
		if parent_id == row.id:
			findings.append({"code": "parent_self_link", "instrument_id": str(row.id)})
			continue
		parent = by_id.get(parent_id)
		if parent is None:
			findings.append(
				{
					"code": "parent_missing",
					"instrument_id": str(row.id),
					"parent_instrument_id": str(parent_id),
				}
			)
			continue
		if parent.instrument_key != row.instrument_key:
			findings.append(
				{
					"code": "parent_cross_key",
					"instrument_id": str(row.id),
					"parent_instrument_id": str(parent_id),
					"parent_instrument_key": parent.instrument_key,
				}
			)

	# Cycle detection over the parent edges, reported once per cycle.
	seen_cycles: set[frozenset[str]] = set()
	for row in instruments:
		path: list[Instrument] = []
		visited: set[Any] = set()
		cursor: Instrument | None = row
		while cursor is not None and cursor.id not in visited:
			visited.add(cursor.id)
			path.append(cursor)
			parent_id = cursor.parent_instrument_id
			cursor = None if parent_id is None or parent_id == cursor.id else by_id.get(parent_id)
		if cursor is None:
			continue
		start = next(index for index, item in enumerate(path) if item.id == cursor.id)
		members = frozenset(str(item.id) for item in path[start:])
		if members in seen_cycles:
			continue
		seen_cycles.add(members)
		findings.append({"code": "parent_cycle", "instrument_ids": sorted(members)})

	return findings


# ---------------------------------------------------------------------------
# Records: grouping and scoring comparison
# ---------------------------------------------------------------------------


def _group_records(
	records: list[dict[str, Any]],
	known_stamps: Mapping[tuple[str, str], list[Instrument]],
	content_by_stamp: Mapping[tuple[str, str], YeeInstrumentResponse],
	legacy_content: YeeInstrumentResponse,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
	"""Group records by (type, status, stamp) and flag the ones needing a human.

	Returns the groups, the human-mapping entries, and the flat list of
	not-cleanly-stamped records that the migration-scope hash covers.
	"""

	groups: dict[tuple[str, str, str | None, str | None, str], dict[str, Any]] = {}
	human_mapping: list[dict[str, Any]] = []
	unclean_records: list[dict[str, Any]] = []
	for record in records:
		key = cast(str | None, record["instrument_key"])
		version = cast(str | None, record["instrument_version"])
		stamp_status = _stamp_status(key, version, known_stamps)
		record["stamp_status"] = stamp_status
		group_key = (
			cast(str, record["record_type"]),
			cast(str, record["status"]),
			key,
			version,
			stamp_status,
		)
		group = groups.setdefault(
			group_key,
			{
				"record_type": record["record_type"],
				"status": record["status"],
				"instrument_key": key,
				"instrument_version": version,
				"stamp_status": stamp_status,
				"count": 0,
				"record_ids": [],
				"shown_required_followups_missing": 0,
				"records_with_missing_followups": [],
			},
		)
		group["count"] += 1
		group["record_ids"].append(record["record_id"])
		content = (
			legacy_content
			if stamp_status == "unstamped"
			else content_by_stamp.get((key, version))
			if key is not None and version is not None
			else None
		)
		if content is not None:
			missing = _missing_required_followups(content, cast(Mapping[str, Any], record["responses"]))
			if missing:
				group["shown_required_followups_missing"] += len(missing)
				group["records_with_missing_followups"].append(
					{"record_id": record["record_id"], "question_ids": missing}
				)
		if stamp_status in {"partial", "unknown", "duplicate"}:
			unclean_records.append(
				{
					"record_type": record["record_type"],
					"record_id": record["record_id"],
					"stamp_status": stamp_status,
					"instrument_key": key,
					"instrument_version": version,
				}
			)
			human_mapping.append(
				{
					"record_type": record["record_type"],
					"record_id": record["record_id"],
					"reason": f"{stamp_status}_instrument_stamp",
					"instrument_key": key,
					"instrument_version": version,
				}
			)
	return list(groups.values()), human_mapping, unclean_records


def _snapshot_differences(left: object, right: object, path: str = "") -> list[str]:
	"""Dotted paths where two canonical score snapshots disagree.

	A total-only comparison cannot see a section or domain vector that moved
	while the total stayed put, and `section_scores_json` is protected history.
	"""

	if isinstance(left, Mapping) and isinstance(right, Mapping):
		differences: list[str] = []
		for key in sorted(set(left) | set(right)):
			child = f"{path}.{key}" if path else str(key)
			if key not in left or key not in right:
				differences.append(child)
				continue
			differences.extend(_snapshot_differences(left[key], right[key], child))
		return differences
	if isinstance(left, (int, float)) and isinstance(right, (int, float)) and not isinstance(left, bool):
		return [] if float(left) == float(right) else [path or "<root>"]
	return [] if left == right else [path or "<root>"]


def _shadow_compare(
	records: list[dict[str, Any]],
	known_stamps: Mapping[tuple[str, str], list[Instrument]],
	decisions_by_id: Mapping[str, RecordDecision] | None = None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
	"""Compare each record's legacy, versioned, and stored score snapshots.

	Without a mapping this is the inventory pass: every record is either compared
	or skipped with a stable reason code, and a skip is an open question.

	With a mapping (the resolution overlay) each reviewed decision is applied in
	memory first — a restamp scores against its new stamp, a retained record
	against its resolved contract, and a quarantined record is classified as
	quarantined rather than skipped. The authorization gate requires
	``records_skipped == 0``, so an undecided record still blocks an apply.
	"""

	decisions = decisions_by_id or {}
	differences: list[dict[str, Any]] = []
	skipped: list[dict[str, Any]] = []
	quarantined: list[dict[str, Any]] = []
	skipped_by_reason: dict[str, int] = {code: 0 for code in SKIP_REASON_CODES}
	compared = 0

	def skip(record: Mapping[str, Any], reason: str, detail: dict[str, Any] | None = None) -> None:
		skipped_by_reason[reason] = skipped_by_reason.get(reason, 0) + 1
		entry = {
			"record_type": record["record_type"],
			"record_id": record["record_id"],
			"reason_code": reason,
		}
		if detail is not None:
			entry["detail"] = detail
		skipped.append(entry)

	for record in records:
		key = cast(str | None, record["instrument_key"])
		version = cast(str | None, record["instrument_version"])
		stamp_status = cast(str, record.get("stamp_status", "unstamped"))
		decision = decisions.get(cast(str, record["record_id"]))

		if decision is not None:
			# Exact current-state match: a record that moved since review must
			# abort rather than be scored against a decision written for a
			# different stamp.
			if (decision.expected_instrument_key, decision.expected_instrument_version) != (key, version):
				skip(record, "stale_decision")
				continue
			if decision.action == "quarantine":
				quarantined.append(
					{
						"record_type": record["record_type"],
						"record_id": record["record_id"],
						"reason_code": decision.reason_code,
						"evidence_ref": decision.evidence_ref,
					}
				)
				continue
			key, version = decision.effective_stamp
			stamp_status = "unstamped" if key is None and version is None else "resolved"
			if decision.resolved_contract == "frozen_schema_v1":
				stamp_status = "unstamped"

		if stamp_status == "partial":
			skip(record, "partial_stamp")
			continue
		if stamp_status == "duplicate":
			skip(record, "duplicate_stamp")
			continue
		if stamp_status == "unknown":
			skip(record, "unknown_stamp")
			continue

		rows = known_stamps.get((key, version), []) if key is not None and version is not None else []
		if stamp_status == "resolved" and not rows:
			# The reviewed decision points at a catalog row that is not there.
			skip(record, "restamp_target_missing")
			continue
		try:
			contract = (
				SCHEMA_V1_SCORING_CONTRACT
				if stamp_status == "unstamped"
				else scoring_contract_from_instrument(cast(Mapping[str, JsonValue], rows[0].content))
			)
		except ValidationError as error:
			skip(record, "invalid_instrument_content", _redacted_error("invalid_instrument_content", error))
			continue
		except ScoringContractResolutionError as error:
			skip(record, "unscorable_instrument", _redacted_error("unscorable_instrument", error))
			continue

		responses = cast(Mapping[str, JsonValue], record["responses"])
		participant_info = cast(Mapping[str, JsonValue], record["participant_info"])
		legacy_score = score_yee_responses(responses, participant_info, contract=SCHEMA_V1_SCORING_CONTRACT)
		versioned_score = score_yee_responses(responses, participant_info, contract=contract)
		compared += 1

		legacy_canonical = legacy_score["canonical_score"]
		versioned_canonical = versioned_score["canonical_score"]
		stored_canonical = record["stored_canonical"]
		stored_total = record["stored_total"]
		versioned_total = versioned_canonical["raw"]["total_score"]

		legacy_vs_versioned = _snapshot_differences(legacy_canonical, versioned_canonical)
		stored_vs_versioned = (
			_snapshot_differences(stored_canonical, versioned_canonical)
			if isinstance(stored_canonical, Mapping)
			else []
		)
		stored_total_differs = stored_total is not None and float(stored_total) != float(versioned_total)

		if legacy_vs_versioned or stored_vs_versioned or stored_total_differs:
			differences.append(
				{
					"record_type": record["record_type"],
					"record_id": record["record_id"],
					"instrument_key": key,
					"instrument_version": version,
					"stamp_status": stamp_status,
					"legacy_total": legacy_canonical["raw"]["total_score"],
					"versioned_total": versioned_total,
					"stored_total": stored_total,
					"stored_snapshot_present": isinstance(stored_canonical, Mapping),
					"legacy_vs_versioned_paths": legacy_vs_versioned[:50],
					"legacy_vs_versioned_path_count": len(legacy_vs_versioned),
					"stored_vs_versioned_paths": stored_vs_versioned[:50],
					"stored_vs_versioned_path_count": len(stored_vs_versioned),
				}
			)

	return {
		"records_compared": compared,
		"records_skipped": len(skipped),
		"skipped_by_reason": skipped_by_reason,
		"skipped": skipped,
		"records_quarantined": len(quarantined),
		"quarantined": quarantined,
		"difference_count": len(differences),
		"zero_unexplained_differences": len(differences) == 0,
		"differences": differences,
	}, differences


# ---------------------------------------------------------------------------
# Hashing
# ---------------------------------------------------------------------------


def _scope_projection(manifest: Mapping[str, Any]) -> dict[str, Any]:
	"""The subset of the manifest that gates a production mutation.

	Ordinary clean auditor activity — a new draft, a new submission against a
	known version — must not move this value, or the freshness check in the
	cutover runbook would abort on normal business and get waived under
	pressure. A new ANOMALY still moves it, which is the point.
	"""

	return {
		"manifest_schema_version": manifest["manifest_schema_version"],
		"product": manifest["product"],
		# Target identity is a gate input. Operator labels are free text and can
		# be mistyped; this fingerprint is derived from the connection actually
		# opened, so dev and production are structurally distinguishable.
		"target_fingerprint": manifest["target_fingerprint"],
		"environment_label": manifest["environment_label"],
		# Scope semantics are part of the gate: an artifact that inventoried only
		# the shared catalog must never hash-match one that inventoried records.
		"record_scope": manifest["record_scope"],
		"instrument_versions": [
			{
				"id": row["id"],
				"instrument_key": row["instrument_key"],
				"instrument_version": row["instrument_version"],
				"parent_instrument_id": row["parent_instrument_id"],
				"is_active": row["is_active"],
				"content_sha256": row["content_sha256"],
				"schema_generation": row["schema_generation"],
				"compatibility_status": row["compatibility_status"],
				"version_and_lifecycle_warnings": row["version_and_lifecycle_warnings"],
				"scoring_compatibility_ok": row["scoring_compatibility"]["ok"],
			}
			for row in manifest["instrument_versions"]
		],
		"parent_chain_findings": manifest["parent_chain_findings"],
		"active_anomalies": manifest["active_anomalies"],
		"unclean_records": manifest["unclean_records"],
		"shadow_differences": manifest["shadow_scoring"]["differences"],
		"shadow_skipped": manifest["shadow_scoring"]["skipped"],
	}


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def generate_migration_manifest(
	session: AsyncSession,
	*,
	product: str,
	environment_label: str,
	target_fingerprint: str,
	mapping: MigrationMappingDocument | None = None,
) -> dict[str, Any]:
	"""Inventory one physical product database. Never writes.

	Runs in one of two modes. Without ``mapping`` this is the **inventory** pass
	that a human reviews. With ``mapping`` it is the SELECT-only **resolution
	overlay**: the reviewed decisions are applied in memory and the result is the
	authorization manifest that Step 2's repair command demands before it will
	write anything. Neither mode opens a transaction or mutates a row.

	:param product: Which physical database this session is bound to (``yee`` or
		``playspace``). ``instruments`` is a shared table, so YEE catalog rows can
		exist in either database and both must be inventoried before the mirrored
		integrity migrations run.
	:param environment_label: Operator-supplied, non-secret label for the target
		(for example ``prod-snapshot-2026-08-28``). Never a database URL.
	:param target_fingerprint: SHA-256 of the redacted ``host/database`` label for
		the connection actually used. It enters the migration-scope hash so two
		tiers of the same product can never produce the same gate value: without
		it, a dev catalog and a production catalog in identical states hash the
		same, and an approved dev artifact would satisfy a production apply.
	:param mapping: The reviewed resolution document for this product.
	:raises ResolutionOverlayError: when the mapping targets another product or
		was authored against a different inventory than the one on disk now.
	"""

	if mapping is not None and mapping.product != product:
		raise ResolutionOverlayError(
			f"Mapping document targets product {mapping.product!r} but this run is {product!r}."
		)

	# ``instruments`` is shared, so a YEE catalog row can exist in either physical
	# database and both must be inventoried before the mirrored integrity
	# migrations run. The audit RECORD tables are a different story: only the YEE
	# database has ``yee_audit_submissions`` (YEE_ONLY_TABLE_NAMES), and although
	# ``audits`` is shared, a null ``instrument_key`` there means a Playspace
	# audit, not an unstamped YEE one. Inventorying records outside the YEE
	# database would crash on the missing table and manufacture false "unstamped
	# YEE record" anomalies out of Playspace rows.
	records_in_scope = product == "yee"

	instruments = list(
		(
			await session.execute(
				select(Instrument)
				.where(Instrument.instrument_key == "yee")
				.order_by(Instrument.created_at, Instrument.id)
			)
		)
		.scalars()
		.all()
	)
	drafts: list[Audit] = []
	submissions: list[YeeAuditSubmission] = []
	if records_in_scope:
		drafts = list(
			(
				await session.execute(
					select(Audit).where(
						Audit.status.in_([AuditStatus.IN_PROGRESS, AuditStatus.PAUSED]),
						or_(
							Audit.instrument_key == "yee",
							Audit.instrument_key.like("yee%"),
							Audit.instrument_key.is_(None),
						),
					)
				)
			)
			.scalars()
			.all()
		)
		submissions = list((await session.execute(select(YeeAuditSubmission))).scalars().all())

	known_stamps: dict[tuple[str, str], list[Instrument]] = defaultdict(list)
	for row in instruments:
		known_stamps[(row.instrument_key, row.instrument_version)].append(row)

	content_by_stamp: dict[tuple[str, str], YeeInstrumentResponse] = {}
	instrument_reports: list[dict[str, Any]] = []
	human_mapping: list[dict[str, Any]] = []
	label_groups: dict[str, list[Instrument]] = defaultdict(list)
	for row in instruments:
		label_groups[row.instrument_version.strip().casefold()].append(row)
	for row in instruments:
		warnings: list[dict[str, Any]] = []
		try:
			content = YeeInstrumentResponse.model_validate(row.content)
			content_by_stamp[(row.instrument_key, row.instrument_version)] = content
			conversion = legacy_to_authoring(content) if content.authoring is None else None
			warnings = (
				[] if conversion is None else [finding.model_dump(by_alias=True) for finding in conversion.findings]
			)
		except ValidationError as error:
			warnings = [{"severity": "error", **_redacted_error("invalid_instrument_content", error)}]
		report = validate_scoring_compatibility(row.content if isinstance(row.content, dict) else {})
		compatibility = await _compatibility_status(session, row)
		label_warnings: list[str] = []
		if row.instrument_version != row.instrument_version.strip():
			label_warnings.append("version_label_has_surrounding_whitespace")
		if not row.instrument_version.strip():
			label_warnings.append("version_label_is_blank")
		if len(label_groups[row.instrument_version.strip().casefold()]) > 1:
			label_warnings.append("version_label_is_case_insensitively_duplicated")
		if len(known_stamps[(row.instrument_key, row.instrument_version)]) > 1:
			label_warnings.append("exact_instrument_stamp_is_duplicated")
		if row.is_active and compatibility == "migration_required":
			label_warnings.append("structural_authoring_v2_is_active")
		instrument_report = {
			"id": str(row.id),
			"instrument_key": row.instrument_key,
			"instrument_version": row.instrument_version,
			"parent_instrument_id": str(row.parent_instrument_id) if row.parent_instrument_id else None,
			"is_active": row.is_active,
			"content_sha256": _sha256(row.content),
			"schema_generation": _schema_generation(row.content),
			"compatibility_status": compatibility,
			"version_and_lifecycle_warnings": label_warnings,
			"scoring_compatibility": report.model_dump(),
			"legacy_to_logical_findings": warnings,
		}
		instrument_reports.append(instrument_report)
		if warnings or label_warnings or not report.ok or report.missing_choices:
			human_mapping.append(
				{
					"record_type": "instrument",
					"record_id": str(row.id),
					"reason": "instrument_review_required",
					"finding_codes": [warning.get("code") for warning in warnings] + label_warnings,
				}
			)

	parent_chain_findings = _parent_chain_findings(instruments)
	for finding in parent_chain_findings:
		human_mapping.append(
			{
				"record_type": "instrument",
				"record_id": finding.get("instrument_id") or ",".join(finding.get("instrument_ids", [])),
				"reason": "parent_chain_blocker",
				"finding_codes": [finding["code"]],
			}
		)

	records = [
		*[
			_record_payload(
				record_type="draft",
				record_id=row.id,
				status=row.status.value,
				instrument_key=row.instrument_key,
				instrument_version=row.instrument_version,
				responses=_draft_payload(row)[1],
				participant_info=_draft_payload(row)[0],
				stored_total=row.summary_score,
				stored_canonical=_stored_canonical(row.scores_json),
			)
			for row in drafts
		],
		*[
			_record_payload(
				record_type="submission",
				record_id=row.id,
				status="SUBMITTED",
				instrument_key=row.instrument_key,
				instrument_version=row.instrument_version,
				responses=row.responses_json,
				participant_info=row.participant_info_json,
				stored_total=row.total_score,
				stored_canonical=_stored_canonical(row.scores_json),
			)
			for row in submissions
		],
	]
	legacy_content = YeeInstrumentResponse.model_validate(get_yee_instrument_data())
	record_groups, record_mappings, unclean_records = _group_records(
		records,
		known_stamps,
		content_by_stamp,
		legacy_content,
	)
	human_mapping.extend(record_mappings)
	shadow_report, shadow_mappings = _shadow_compare(
		records,
		known_stamps,
		None if mapping is None else mapping.record_decisions_by_id(),
	)
	human_mapping.extend({"reason": "shadow_scoring_difference", **mapping} for mapping in shadow_mappings)
	human_mapping.extend(
		{
			"record_type": entry["record_type"],
			"record_id": entry["record_id"],
			"reason": "shadow_scoring_skipped",
			"finding_codes": [entry["reason_code"]],
		}
		for entry in shadow_report["skipped"]
	)
	active_rows = [row for row in instruments if row.is_active]
	active_anomalies: list[dict[str, Any]] = []
	if records_in_scope:
		# The YEE database must carry exactly one active YEE instrument.
		if len(active_rows) != 1:
			active_anomalies.append(
				{
					"code": "missing_active_instrument" if not active_rows else "multiple_active_instruments",
					"active_count": len(active_rows),
					"instrument_ids": [str(row.id) for row in active_rows],
				}
			)
	else:
		# The shared catalog in another product's database should hold NO YEE
		# rows at all. Zero is the healthy state and must not be reported as a
		# missing instrument, or this product's manifest could never reach a
		# clean authorization and reviewers would learn to ignore anomalies.
		# Any row here still needs a human: it is an orphan the mirrored
		# ``ps_0012`` integrity index will be applied on top of.
		if instruments:
			active_anomalies.append(
				{
					"code": "unexpected_yee_catalog_rows_outside_yee_database",
					"row_count": len(instruments),
					"active_count": len(active_rows),
					"instrument_ids": [str(row.id) for row in instruments],
				}
			)
		if len(active_rows) > 1:
			active_anomalies.append(
				{
					"code": "multiple_active_instruments",
					"active_count": len(active_rows),
					"instrument_ids": [str(row.id) for row in active_rows],
				}
			)

	manifest: dict[str, Any] = {
		"manifest_schema_version": MANIFEST_SCHEMA_VERSION,
		"mode": "inventory" if mapping is None else "authorization",
		"product": product,
		"environment_label": environment_label,
		"target_fingerprint": target_fingerprint,
		"dry_run": True,
		"generated_at": datetime.now(timezone.utc).isoformat(),
		"decoder_release": {
			"android": {
				"status": "assigned_pending_release",
				"available_version": None,
				"evidence_ref": None,
			},
			"ios": {
				"status": "assigned_pending_release",
				"available_version": None,
				"evidence_ref": None,
			},
			"assigned_mobile_display_version": ASSIGNED_FIRST_AUTHORING_V2_MOBILE_DISPLAY_VERSION,
			"minimum_supported_version_change": None,
		},
		"record_scope": {
			"records_in_scope": records_in_scope,
			"reason": (
				"yee_database_owns_audit_records"
				if records_in_scope
				else "shared_instruments_catalog_only__audit_records_live_in_the_yee_database"
			),
		},
		"instrument_versions": instrument_reports,
		"parent_chain_findings": parent_chain_findings,
		"active_anomalies": active_anomalies,
		"audit_records_by_stamp": record_groups,
		"unclean_records": unclean_records,
		"shadow_scoring": shadow_report,
		"records_requiring_human_mapping": human_mapping,
		"summary": {
			"instrument_version_count": len(instruments),
			"active_instrument_count": len(active_rows),
			"draft_count": len(drafts),
			"submission_count": len(submissions),
			"unclean_record_count": len(unclean_records),
			"human_mapping_count": len(human_mapping),
		},
	}

	scope = _scope_projection(manifest)
	scope_hash = _sha256(scope)
	evidence = {key: value for key, value in manifest.items() if key != "generated_at"}
	manifest["hashes"] = {
		"migration_scope_sha256": scope_hash,
		"full_payload_sha256": _sha256(evidence),
	}

	if mapping is not None:
		expected = mapping.expected_inventory_hash()
		if expected != scope_hash:
			# Refuse before emitting anything an operator could mistake for an
			# authorization: the catalog moved since this document was reviewed.
			raise ResolutionOverlayError(
				"Mapping document was authored against a different inventory. "
				f"Expected migration_scope_sha256 {expected}, found {scope_hash}. "
				"Re-run the inventory, re-review, and re-author the mapping."
			)
		undecided = [
			entry["record_id"] for entry in shadow_report["skipped"] if entry["reason_code"] != "stale_decision"
		]
		manifest["authorization"] = {
			"authorizes_apply": shadow_report["records_skipped"] == 0 and shadow_report["zero_unexplained_differences"],
			"records_skipped": shadow_report["records_skipped"],
			"records_quarantined": shadow_report["records_quarantined"],
			"zero_unexplained_differences": shadow_report["zero_unexplained_differences"],
			"undecided_record_ids": undecided[:200],
			"undecided_record_count": len(undecided),
			"bound_hashes": {
				"inventory_scope_sha256": scope_hash,
				"mapping_sha256": _sha256(mapping.model_dump(mode="json")),
				"approved_inventory_hashes": mapping.approved_inventory_hashes.model_dump(),
			},
		}
		manifest["hashes"]["authorization_scope_sha256"] = _sha256(
			{
				"scope": scope,
				"authorization": manifest["authorization"],
			}
		)
	return manifest

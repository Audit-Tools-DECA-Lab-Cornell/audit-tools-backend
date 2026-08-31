"""The reviewed resolution/mapping document for the Phase 3 history repair.

This is the ONLY input that may change a historical instrument stamp. It is
authored by hand from a reviewed inventory manifest, kept outside git for real
data, and carries no participant data — only IDs, stamps, and evidence codes.

Two properties make it safe to act on:

- **It is bound to the evidence it was written against.** Every document repeats
  the migration-scope hash of BOTH product inventories it was derived from, so a
  document written against a stale picture of the catalog is refused rather than
  applied.
- **Every decision states the state it expects to find.** A restamp names the
  exact old key/version it is replacing and a catalog decision names the content
  hash and active flag it expects, so a row that moved since review aborts the
  run instead of being overwritten.

Quarantine is a first-class decision here, not an absence of one. A record with
no decision leaves `records_skipped` non-zero, and the authorization gate
refuses to authorize an apply while that is true.
"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

#: Why a decision was made. Stable strings so an artifact stays greppable and a
#: reviewer can audit decisions by class rather than by prose.
DecisionReasonCode = Literal[
	"exact_stamp_resolves_uniquely",
	"unstamped_legacy_frozen_fallback",
	"stamp_completed_from_unique_catalog_match",
	"human_mapped_to_preserved_version",
	"byte_identical_duplicate_consolidated",
	"content_differs_requires_human_decision",
	"ambiguous_candidate_quarantined",
	"shadow_difference_under_investigation",
	"catalog_anomaly_quarantined",
]

#: How a record's score is resolved once the decision is applied. Anything else
#: would be a guess, and the adjudication matrix forbids guessing.
ResolvedContract = Literal["exact_stamp", "frozen_schema_v1"]


class ApprovedInventoryHashes(BaseModel):
	"""Migration-scope hashes of the two product inventories this was written against.

	Both are required even for a single-product apply: the mirrored integrity
	migrations touch the shared ``instruments`` table on both branches, so a
	mapping authored without looking at both catalogs is not reviewable.
	"""

	model_config = ConfigDict(extra="forbid")

	yee: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
	playspace: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")


class InstrumentDecision(BaseModel):
	"""What happens to one catalog row, and the state that must still be true."""

	model_config = ConfigDict(extra="forbid")

	instrument_id: uuid.UUID
	action: Literal["retain", "deactivate", "consolidate_into", "quarantine"]
	#: Required for ``consolidate_into``: the surviving row children re-parent to.
	canonical_instrument_id: uuid.UUID | None = None
	#: Exact current-state match. A row that moved since review aborts the run.
	expected_is_active: bool
	expected_content_sha256: str = Field(min_length=64, max_length=64, pattern=r"^[0-9a-f]{64}$")
	reason_code: DecisionReasonCode
	evidence_ref: str | None = None

	@model_validator(mode="after")
	def _check_action_shape(self) -> InstrumentDecision:
		if self.action == "consolidate_into":
			if self.canonical_instrument_id is None:
				raise ValueError("consolidate_into requires canonical_instrument_id")
			if self.canonical_instrument_id == self.instrument_id:
				raise ValueError("consolidate_into cannot point a row at itself")
		elif self.canonical_instrument_id is not None:
			raise ValueError(f"canonical_instrument_id is only valid for consolidate_into, not {self.action}")
		return self


class RecordDecision(BaseModel):
	"""What happens to one draft or submission stamp, and how it is then scored."""

	model_config = ConfigDict(extra="forbid")

	record_type: Literal["draft", "submission"]
	record_id: uuid.UUID
	action: Literal["retain", "restamp", "quarantine"]
	#: Exact old values. ``None`` on both means the record is currently unstamped.
	expected_instrument_key: str | None = None
	expected_instrument_version: str | None = None
	#: Required for ``restamp``; forbidden otherwise.
	new_instrument_key: str | None = None
	new_instrument_version: str | None = None
	#: How to score this record after the decision. Required unless quarantined.
	resolved_contract: ResolvedContract | None = None
	reason_code: DecisionReasonCode
	evidence_ref: str | None = None

	@model_validator(mode="after")
	def _check_action_shape(self) -> RecordDecision:
		has_new_stamp = self.new_instrument_key is not None or self.new_instrument_version is not None
		if self.action == "restamp":
			if self.new_instrument_key is None or self.new_instrument_version is None:
				raise ValueError("restamp requires both new_instrument_key and new_instrument_version")
			if (self.new_instrument_key, self.new_instrument_version) == (
				self.expected_instrument_key,
				self.expected_instrument_version,
			):
				raise ValueError("restamp must change the stamp; use retain instead")
		elif has_new_stamp:
			raise ValueError(f"new stamp values are only valid for restamp, not {self.action}")

		if self.action == "quarantine":
			if self.resolved_contract is not None:
				raise ValueError("a quarantined record has no resolved contract until a human resolves it")
		elif self.resolved_contract is None:
			raise ValueError(f"{self.action} requires resolved_contract")
		return self

	@property
	def effective_stamp(self) -> tuple[str | None, str | None]:
		"""The stamp this record scores against once the decision is applied."""

		if self.action == "restamp":
			return self.new_instrument_key, self.new_instrument_version
		return self.expected_instrument_key, self.expected_instrument_version


class MigrationMappingDocument(BaseModel):
	"""One product's reviewed decisions, bound to the inventories behind them."""

	model_config = ConfigDict(extra="forbid")

	mapping_schema_version: Literal[1] = 1
	product: Literal["yee", "playspace"]
	environment_label: str = Field(min_length=1, max_length=64)
	approved_inventory_hashes: ApprovedInventoryHashes
	instrument_decisions: list[InstrumentDecision] = Field(default_factory=list)
	record_decisions: list[RecordDecision] = Field(default_factory=list)

	@model_validator(mode="after")
	def _check_unique_targets(self) -> MigrationMappingDocument:
		instrument_ids = [decision.instrument_id for decision in self.instrument_decisions]
		if len(set(instrument_ids)) != len(instrument_ids):
			raise ValueError("each instrument may carry at most one decision")
		record_ids = [decision.record_id for decision in self.record_decisions]
		if len(set(record_ids)) != len(record_ids):
			raise ValueError("each record may carry at most one decision")
		return self

	def expected_inventory_hash(self) -> str:
		"""The migration-scope hash this document was authored against."""

		return getattr(self.approved_inventory_hashes, self.product)

	def record_decisions_by_id(self) -> dict[str, RecordDecision]:
		return {str(decision.record_id): decision for decision in self.record_decisions}

	def instrument_decisions_by_id(self) -> dict[str, InstrumentDecision]:
		return {str(decision.instrument_id): decision for decision in self.instrument_decisions}

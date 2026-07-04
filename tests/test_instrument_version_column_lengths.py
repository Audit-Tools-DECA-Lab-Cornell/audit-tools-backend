"""Regression: instrument_version stamp columns must hold any instrument version.

``Instrument.instrument_version`` is the source of truth for version labels.
Every table that stamps a copy of it — the shared ``audits`` table plus
``yee_audit_submissions`` and ``playspace_submissions`` — must be at least as
wide. If a stamp column is narrower, a version label longer than that column
(but valid for ``instruments``) truncates on insert, raising a Postgres
``varchar`` length error when an audit is saved or submitted.

Pure schema assertion (no DB): guards against a stamp column being (re)narrowed
below the instrument version length.
"""

from __future__ import annotations

from typing import Any

from app.models import Audit, Instrument, PlayspaceSubmission, YeeAuditSubmission


def _version_length(model: Any) -> int:
	return model.__table__.c["instrument_version"].type.length


def test_stamp_columns_can_hold_any_instrument_version() -> None:
	source_length = _version_length(Instrument)
	for model in (Audit, YeeAuditSubmission, PlayspaceSubmission):
		stamp_length = _version_length(model)
		assert stamp_length >= source_length, (
			f"{model.__tablename__}.instrument_version is String({stamp_length}), narrower than "
			f"instruments.instrument_version String({source_length}); a long version label would "
			f"truncate when stamped onto an audit/submission"
		)

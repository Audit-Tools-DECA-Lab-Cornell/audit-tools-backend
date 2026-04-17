"""
Migration and parity helpers for Playspace canonical aggregate backfills.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import (
    Audit,
    AuditStatus,
    PlayspaceAuditSection,
    PlayspaceQuestionResponse,
)
from app.products.playspace.audit_state import (
    CURRENT_AUDIT_SCHEMA_VERSION,
    build_legacy_responses_json_from_relations,
    build_responses_json_from_relations,
    get_aggregate_revision,
    set_draft_progress_percent,
    set_execution_mode_value,
)
from app.products.playspace.scoring import build_audit_progress, score_audit


@dataclass(frozen=True)
class AuditAggregateParityResult:
    """Parity outcome for one audit after canonical aggregate migration."""

    audit_id: uuid.UUID
    responses_match: bool
    progress_match: bool
    scores_match: bool
    schema_version: int
    revision: int

    @property
    def is_matching(self) -> bool:
        """Whether the migrated aggregate matches legacy-derived behavior."""

        return self.responses_match and self.progress_match and self.scores_match


def migrate_audit_to_canonical_aggregate(audit: Audit) -> AuditAggregateParityResult:
    """Backfill one audit's canonical aggregate and derived summary projections."""

    legacy_payload = build_legacy_responses_json_from_relations(audit)
    audit.responses_json = {
        "schema_version": CURRENT_AUDIT_SCHEMA_VERSION,
        "revision": get_aggregate_revision(audit),
        "meta": legacy_payload.get("meta", {}),
        "pre_audit": legacy_payload.get("pre_audit", {}),
        "sections": legacy_payload.get("sections", {}),
    }

    legacy_execution_mode = legacy_payload.get("meta", {})
    execution_mode_value = (
        legacy_execution_mode.get("execution_mode")
        if isinstance(legacy_execution_mode, dict)
        else None
    )
    set_execution_mode_value(
        audit=audit,
        execution_mode=(execution_mode_value if isinstance(execution_mode_value, str) else None),
    )

    canonical_payload = build_responses_json_from_relations(audit)
    progress = build_audit_progress(responses_json=canonical_payload)
    draft_progress_percent = _progress_percent(progress)

    if audit.status is AuditStatus.SUBMITTED:
        scored_payload = score_audit(responses_json=canonical_payload)
        audit.scores_json = scored_payload
        set_draft_progress_percent(audit=audit, draft_progress_percent=None)
        audit.summary_score = _combined_construct_total(scored_payload.get("overall"))
    else:
        set_draft_progress_percent(audit=audit, draft_progress_percent=draft_progress_percent)
        audit.scores_json = {
            "draft_progress_percent": draft_progress_percent,
            "progress": progress.model_dump(),
        }
        audit.summary_score = None

    return verify_audit_aggregate_parity(audit)


def verify_audit_aggregate_parity(audit: Audit) -> AuditAggregateParityResult:
    """Compare legacy-derived and canonical-derived outputs for one audit."""

    legacy_payload = build_legacy_responses_json_from_relations(audit)
    canonical_payload = build_responses_json_from_relations(audit)

    legacy_progress = build_audit_progress(responses_json=legacy_payload).model_dump()
    canonical_progress = build_audit_progress(responses_json=canonical_payload).model_dump()

    legacy_scores = _safe_score_payload(legacy_payload)
    canonical_scores = _safe_score_payload(canonical_payload)

    return AuditAggregateParityResult(
        audit_id=audit.id,
        responses_match=_strip_canonical_envelope(canonical_payload) == legacy_payload,
        progress_match=canonical_progress == legacy_progress,
        scores_match=canonical_scores == legacy_scores,
        schema_version=_read_int(
            canonical_payload.get("schema_version"), CURRENT_AUDIT_SCHEMA_VERSION
        ),
        revision=_read_int(canonical_payload.get("revision"), 0),
    )


async def backfill_canonical_aggregates(
    session: AsyncSession,
    *,
    audit_ids: Sequence[uuid.UUID] | None = None,
    dry_run: bool = False,
) -> list[AuditAggregateParityResult]:
    """Backfill the canonical aggregate for selected Playspace audits."""

    statement = select(Audit).options(
        selectinload(Audit.playspace_context),
        selectinload(Audit.playspace_pre_audit_answers),
        selectinload(Audit.playspace_sections)
        .selectinload(PlayspaceAuditSection.question_responses)
        .selectinload(PlayspaceQuestionResponse.scale_answers),
    )
    if audit_ids:
        statement = statement.where(Audit.id.in_(list(audit_ids)))

    audits = (await session.execute(statement)).scalars().all()
    results = [migrate_audit_to_canonical_aggregate(audit) for audit in audits]

    if dry_run:
        await session.rollback()
    else:
        await session.commit()

    return results


def _strip_canonical_envelope(
    canonical_payload: dict[str, object],
) -> dict[str, object]:
    """Drop server-managed envelope fields before legacy-vs-canonical comparison."""

    return {
        "meta": canonical_payload.get("meta", {}),
        "pre_audit": canonical_payload.get("pre_audit", {}),
        "sections": canonical_payload.get("sections", {}),
    }


def _safe_score_payload(
    responses_payload: dict[str, object],
) -> dict[str, object] | None:
    """Score one payload when possible, returning `None` when incomplete."""

    try:
        return score_audit(responses_json=responses_payload)
    except ValueError:
        return None


def _progress_percent(progress: object) -> float:
    """Convert one progress response into a percentage used by list projections."""

    total_visible_questions = getattr(progress, "total_visible_questions", 0)
    answered_visible_questions = getattr(progress, "answered_visible_questions", 0)
    if not isinstance(total_visible_questions, int) or total_visible_questions <= 0:
        return 0.0
    if not isinstance(answered_visible_questions, int):
        return 0.0
    return round((answered_visible_questions / total_visible_questions) * 100, 2)


def _combined_construct_total(overall_payload: object) -> float | None:
    """Collapse overall construct totals into the compact summary score."""

    if not isinstance(overall_payload, dict):
        return None

    play_value_total = overall_payload.get("play_value_total")
    usability_total = overall_payload.get("usability_total")
    if not isinstance(play_value_total, int | float):
        return None
    if not isinstance(usability_total, int | float):
        return None
    return round(float(play_value_total) + float(usability_total), 2)


def _read_int(value: object, default: int) -> int:
    """Read one integer from unknown JSON-like input."""

    return value if isinstance(value, int) else default

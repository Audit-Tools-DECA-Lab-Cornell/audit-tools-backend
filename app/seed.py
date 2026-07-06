"""
Seed shared-core data into the YEE and Playspace databases.

Playspace data is generated from the live scoring metadata so assignments,
responses, draft progress, and submitted scores remain internally consistent.
YEE seed data follows the real draft and final-submission payload shapes so
fresh test databases exercise realistic scoring and reporting states.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from collections.abc import Collection, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import cast
from alembic.config import Config
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from alembic import command
from app.auth_security import hash_password
from app.core.demo_data import DEMO_ACCOUNT_ID
from app.database import normalize_postgres_sqlalchemy_url
from app.db_urls import (
	DatabaseEnvironment,
	ProductKey,
	describe_database_target,
	parse_database_environment,
	resolve_raw_database_url,
)
from app.models import (
	Account,
	AccountType,
	Audit,
	AuditorAccessRequest,
	AuditorAssignment,
	AuditorInvite,
	AuditorProfile,
	AuditStatus,
	BugReport,
	Instrument,
	KnownIssue,
	ManagerInvite,
	ManagerProfile,
	Notification,
	Place,
	PlayspaceChecklistAnswer,
	PlayspacePreSubmissionAnswer,
	PlayspaceQuestionResponse,
	PlayspaceScaleAnswer,
	PlayspaceSubmissionContext,
	PlayspaceSubmissionSection,
	PlayspaceSubmission,
	Project,
	ProjectPlace,
	User,
	YeeAuditSubmission,
)
from app.products.playspace.seed_data import build_playspace_seed_entities
from app.products.yee.services.scoring_spec import (
	DOMAIN_ORDER,
	ITEM_SPECS,
	SCORING_VERSION,
	PairedItemSpec,
)
from app.yee_scoring import get_yee_instrument_data, score_yee_responses

REPO_ROOT = Path(__file__).resolve().parents[1]

YEE_ORGANIZATION_NAME = "Youth Enabling Environments Collaborative"

UNITED_STATES = "United States"
NEW_YORK = "New York"

YEE_MANAGER_PROFILE_PRIMARY_ID = uuid.UUID("77777777-7777-4777-8777-777777777771")
YEE_MANAGER_PROFILE_SECONDARY_ID = uuid.UUID("77777777-7777-4777-8777-777777777772")

YEE_PROJECT_CORE_ID = uuid.UUID("88888888-8888-4888-8888-888888888881")
YEE_PROJECT_FOLLOW_UP_ID = uuid.UUID("88888888-8888-4888-8888-888888888882")
# Third project whose places exist to stress raw/weighted scoring and combined
# reports: a second same-place comparison set plus weight-sensitivity rows.
YEE_PROJECT_PILOT_ID = uuid.UUID("88888888-8888-4888-8888-888888888883")

YEE_PLACE_HUB_ID = uuid.UUID("99999999-9999-4999-8999-999999999991")
YEE_PLACE_PLAZA_ID = uuid.UUID("99999999-9999-4999-8999-999999999992")
YEE_PLACE_LIBRARY_ID = uuid.UUID("99999999-9999-4999-8999-999999999993")
YEE_PLACE_COMMONS_ID = uuid.UUID("99999999-9999-4999-8999-999999999994")
# An assigned-but-unaudited place auditor 1 still has to visit. Kept free of any
# seeded audit/submission so the submit-flow durability test has a clean slot.
YEE_PLACE_GREEN_ID = uuid.UUID("99999999-9999-4999-8999-999999999995")
# Pilot-project places. Riverside carries a second 3-auditor comparison set;
# Market isolates the weighting math (same raw responses, different weights, and
# a zero-weight edge); Garden holds a freshly started minimal draft.
YEE_PLACE_RIVERSIDE_ID = uuid.UUID("99999999-9999-4999-8999-999999999996")
YEE_PLACE_MARKET_ID = uuid.UUID("99999999-9999-4999-8999-999999999997")
YEE_PLACE_GARDEN_ID = uuid.UUID("99999999-9999-4999-8999-999999999998")

YEE_AUDITOR_PROFILE_01_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1")
YEE_AUDITOR_PROFILE_02_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2")
YEE_AUDITOR_PROFILE_03_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3")

YEE_INSTRUMENT_ID = uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1")

YEE_AUDIT_HUB_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1")
YEE_AUDIT_PLAZA_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2")
YEE_AUDIT_LIBRARY_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb3")
YEE_AUDIT_COMMONS_IN_PROGRESS_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb4")
YEE_AUDIT_HUB_AUDITOR_02_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb5")
YEE_AUDIT_HUB_AUDITOR_03_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb6")
YEE_AUDIT_PLAZA_IN_PROGRESS_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb7")
# Pilot-project audit shells (submitted unless noted).
YEE_AUDIT_RIVERSIDE_01_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb8")
YEE_AUDIT_RIVERSIDE_02_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb9")
YEE_AUDIT_RIVERSIDE_03_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbba")
YEE_AUDIT_MARKET_LOW_WEIGHT_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
YEE_AUDIT_MARKET_HIGH_WEIGHT_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbc")
YEE_AUDIT_MARKET_ZERO_WEIGHT_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbd")
YEE_AUDIT_GARDEN_IN_PROGRESS_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbe")

YEE_SUBMISSION_HUB_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc1")
YEE_SUBMISSION_PLAZA_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc2")
YEE_SUBMISSION_LIBRARY_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc3")
YEE_SUBMISSION_HUB_AUDITOR_02_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc4")
YEE_SUBMISSION_HUB_AUDITOR_03_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc5")
YEE_SUBMISSION_RIVERSIDE_01_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc6")
YEE_SUBMISSION_RIVERSIDE_02_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc7")
YEE_SUBMISSION_RIVERSIDE_03_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc8")
YEE_SUBMISSION_MARKET_LOW_WEIGHT_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc9")
YEE_SUBMISSION_MARKET_HIGH_WEIGHT_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccca")
YEE_SUBMISSION_MARKET_ZERO_WEIGHT_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccb")

# Domain weights keyed by the report domain order the dashboard expects.
YEE_SEED_DOMAIN_WEIGHTS: dict[str, int] = {
	"access": 3,
	"activitySpaces": 2,
	"amenities": 2,
	"experienceOfSpace": 3,
	"aestheticsAndCare": 2,
	"useAndUsability": 2,
}


def _utc_datetime(value: str) -> datetime:
	"""Convert an ISO-ish timestamp string into a timezone-aware UTC datetime."""

	return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _demo_password_hash() -> str:
	"""Return the shared demo login password hash used for seeded auth users."""

	return hash_password("DemoPass123!")


async def _clear_core_tables(session: AsyncSession) -> None:
	"""Remove shared-core records (child rows first) before fresh deterministic data.

	Only touches tables that exist in BOTH product databases. Product-specific
	tables are cleared by their own helpers so this is safe to run against either
	database after that product's tables have been cleared.
	"""

	for model in (
		BugReport,
		KnownIssue,
		Notification,
		Audit,
		AuditorAssignment,
		AuditorInvite,
		ManagerInvite,
		AuditorAccessRequest,
		ProjectPlace,
		ManagerProfile,
		Project,
		AuditorProfile,
		Place,
		Instrument,
		Account,
		User,
	):
		await session.execute(delete(model))


async def _clear_playspace_tables(session: AsyncSession) -> None:
	"""Remove Playspace-only records (child rows first). Playspace database only."""

	for model in (
		PlayspaceChecklistAnswer,
		PlayspaceScaleAnswer,
		PlayspaceQuestionResponse,
		PlayspaceSubmissionSection,
		PlayspacePreSubmissionAnswer,
		PlayspaceSubmissionContext,
		PlayspaceSubmission,
	):
		await session.execute(delete(model))


async def _clear_yee_tables(session: AsyncSession) -> None:
	"""Remove YEE-only records. YEE database only."""

	await session.execute(delete(YeeAuditSubmission))


async def _clear_product_tables(session: AsyncSession, product: ProductKey) -> None:
	"""Clear one product database: its product-specific tables, then shared core.

	This never references the other product's tables, so it is safe to run against
	a database where those tables do not physically exist.
	"""

	if product is ProductKey.PLAYSPACE:
		await _clear_playspace_tables(session)
	else:
		await _clear_yee_tables(session)
	await _clear_core_tables(session)


def _run_product_upgrade(product: ProductKey, environment: DatabaseEnvironment) -> None:
	"""Run Alembic for one product/tier in a synchronous context."""

	alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
	# Pass both product and environment so Alembic targets the same tier the seed
	# writes to (mirrors the test harness `-x product=... -x environment=...`).
	alembic_config.cmd_opts = argparse.Namespace(x=[f"product={product.value}", f"environment={environment.value}"])
	# Each product has its own Alembic branch head (label == product value), so the
	# generic "head" is ambiguous; target the product-scoped branch head explicitly.
	command.upgrade(alembic_config, f"{product.value}@head")


async def _upgrade_product_database(product: ProductKey, environment: DatabaseEnvironment) -> None:
	"""Ensure the selected product/tier database schema exists before seeding."""

	await asyncio.to_thread(_run_product_upgrade, product, environment)


def _build_seed_engine(product: ProductKey, environment: DatabaseEnvironment) -> AsyncEngine:
	"""Build a one-off engine bound to the chosen (product, environment) database.

	The seed resolves its target from ``--environment`` directly rather than the
	process-wide engines in ``app.database`` (which are built from the import-time
	``ENVIRONMENT``), so the flag alone decides which tier is wiped and reseeded.
	Mirrors the test harness in ``tests/products/*/conftest.py``.
	"""

	raw_url = resolve_raw_database_url(product, environment)
	normalized_url, connect_args = normalize_postgres_sqlalchemy_url(raw_url)
	return create_async_engine(normalized_url, pool_pre_ping=True, connect_args=connect_args)


async def _insert_seed_entities(session: AsyncSession, entities: list[object]) -> None:
	"""Insert seed entities in stable FK dependency order.

	asyncpg can fail to infer parameter types for large `executemany` inserts when
	enum-typed ORM rows are mixed in one flush batch. Flushing one row at a time
	keeps the dependency order deterministic and avoids that driver edge case.
	"""

	ordered_types: tuple[type[object], ...] = (
		Account,
		User,
		Instrument,
		ManagerProfile,
		AuditorProfile,
		Project,
		Place,
		ProjectPlace,
		AuditorAssignment,
		PlayspaceSubmission,
		# PlayspaceSubmissionContext,
		Audit,
		YeeAuditSubmission,
		# Known issues before bug reports: a report may FK a known issue.
		KnownIssue,
		BugReport,
	)
	inserted_entity_ids: set[int] = set()

	for model_type in ordered_types:
		batch = [
			entity for entity in entities if isinstance(entity, model_type) and id(entity) not in inserted_entity_ids
		]
		if not batch:
			continue
		session.add_all(batch)
		await session.flush()
		inserted_entity_ids.update(id(entity) for entity in batch)

	remaining_entities = [entity for entity in entities if id(entity) not in inserted_entity_ids]
	if remaining_entities:
		session.add_all(remaining_entities)
		await session.flush()


async def _upsert_seed_entities(session: AsyncSession, entities: list[object]) -> None:
	ordered_types: tuple[type[object], ...] = (
		Account,
		User,
		Instrument,
		ManagerProfile,
		AuditorProfile,
		Project,
		Place,
		ProjectPlace,
		AuditorAssignment,
		PlayspaceSubmission,
		Audit,
		YeeAuditSubmission,
		KnownIssue,
		BugReport,
	)
	merged_entity_ids: set[int] = set()

	for model_type in ordered_types:
		batch = [
			entity for entity in entities if isinstance(entity, model_type) and id(entity) not in merged_entity_ids
		]
		if not batch:
			continue
		for entity in batch:
			await session.merge(entity)
			merged_entity_ids.add(id(entity))
		await session.flush()

	remaining_entities = [entity for entity in entities if id(entity) not in merged_entity_ids]
	for entity in remaining_entities:
		await session.merge(entity)
	await session.flush()


def _build_playspace_entities() -> list[object]:
	"""Create deterministic Playspace ORM objects for seeding."""

	return list(build_playspace_seed_entities())


def _uniform_domain_levels(value: float) -> dict[str, float]:
	"""Return a per-domain level map with the same ``value`` for every domain."""

	return {domain: value for domain in DOMAIN_ORDER}


def _domain_levels(**overrides: float) -> dict[str, float]:
	"""Return a per-domain level map, defaulting unspecified domains to 0.0."""

	levels = {domain: 0.0 for domain in DOMAIN_ORDER}
	levels.update(overrides)
	return levels


def _build_yee_domain_scored_responses(
	domain_levels: Mapping[str, float],
	*,
	include_domains: Collection[str] | None = None,
) -> dict[str, dict[str, str]]:
	"""Build instrument-valid responses graded to a target fraction per domain.

	Answers are chosen directly against ``ITEM_SPECS`` — the exact spec the
	scoring engine grades — so each domain lands near ``level * domain_max``. A
	single global "quality" knob collapsed onto a handful of identical totals
	because it ranked answers by unrelated QSF metadata; targeting the engine
	instead yields a genuine raw spread and independent per-domain variation
	(mixed-domain profiles) for realistic reports.

	``include_domains`` limits answered sections to model a partially completed
	draft (an auditor part-way through the six domain sections).
	"""

	responses: dict[str, dict[str, str]] = {}

	def _put(item_id: str, choice_id: str, answer_id: str) -> None:
		responses.setdefault(item_id, {})[choice_id] = answer_id

	for spec in ITEM_SPECS:
		if include_domains is not None and spec.domain not in include_domains:
			continue
		fraction = max(0.0, min(1.0, float(domain_levels.get(spec.domain, 0.0))))
		if isinstance(spec, PairedItemSpec):
			# Paired item score = presence(0/1) * condition(1..3), max 3. Presence
			# answer "2" scores 0 and blocks the follow-up condition entirely.
			target = round(fraction * spec.max_score)
			if target <= 0:
				_put(spec.presence_item_id, spec.choice_id, "2")
			else:
				_put(spec.presence_item_id, spec.choice_id, "1")
				_put(spec.condition_item_id, spec.choice_id, str(min(3, max(1, target))))
		else:
			target = round(fraction * spec.max_score)
			best = min(spec.answer_scores, key=lambda answer: (abs(answer.score - target), answer.score))
			_put(spec.item_id, spec.choice_id, best.answer_id)
	return responses


def _build_yee_participant_info(
	*,
	auditor_code: str,
	place_id: uuid.UUID,
	place_name: str,
	audit_date: date,
	started_at: datetime,
	total_minutes: int,
	domain_weights: dict[str, int] | None = None,
	comments: str = "",
	section_comments: dict[str, str] | None = None,
	weighting_comments: dict[str, str] | None = None,
	seed_scenario: str | None = None,
) -> dict[str, object]:
	participant_info: dict[str, object] = {
		"auditor_id": auditor_code,
		"place_id": str(place_id),
		"place_name": place_name,
		"audit_date": audit_date.isoformat(),
		"start_time": started_at.strftime("%H:%M"),
		"finish_time": (started_at + timedelta(minutes=total_minutes)).strftime("%H:%M"),
		"total_minutes": total_minutes,
		"visit_frequency": "Weekly",
		"season": "Spring",
		"weather": "Clear",
		"domain_weights": dict(domain_weights or YEE_SEED_DOMAIN_WEIGHTS),
		"comments": comments,
		"section_comments": dict(section_comments or {}),
		"weighting_comments": dict(weighting_comments or {}),
	}
	if seed_scenario is not None:
		participant_info["seed_scenario"] = seed_scenario
	return participant_info


def _score_cache(score: dict[str, object]) -> dict[str, object]:
	return {
		"total_score": score["total_score"],
		"section_scores": score["section_scores"],
		"category_scores": score["category_scores"],
		"matched_scored_answers": score["matched_scored_answers"],
		"canonical_score": score["canonical_score"],
	}


def _assert_yee_audit_submission_match(audit: Audit, submission: YeeAuditSubmission) -> None:
	assert audit.status == AuditStatus.SUBMITTED
	assert audit.auditor_profile_id == submission.auditor_id
	assert audit.place_id == submission.place_id
	assert audit.submitted_at == submission.submitted_at
	assert audit.summary_score == float(submission.total_score)
	assert audit.responses_json == submission.responses_json
	assert audit.scores_json["canonical_score"] == submission.scores_json


def build_realistic_yee_draft(
	*,
	audit_id: uuid.UUID,
	project_id: uuid.UUID,
	place_id: uuid.UUID,
	place_name: str,
	auditor_id: uuid.UUID,
	auditor_code: str,
	audit_code: str,
	started_at: datetime,
	total_minutes: int,
	domain_levels: Mapping[str, float],
	include_domains: Collection[str] | None = None,
	domain_weights: dict[str, int] | None = None,
	comments: str = "",
	section_comments: dict[str, str] | None = None,
	weighting_comments: dict[str, str] | None = None,
	seed_scenario: str | None = None,
) -> Audit:
	responses = _build_yee_domain_scored_responses(domain_levels, include_domains=include_domains)
	participant_info = _build_yee_participant_info(
		auditor_code=auditor_code,
		place_id=place_id,
		place_name=place_name,
		audit_date=started_at.date(),
		started_at=started_at,
		total_minutes=total_minutes,
		domain_weights=domain_weights,
		comments=comments,
		section_comments=section_comments,
		weighting_comments=weighting_comments,
		seed_scenario=seed_scenario,
	)
	score = score_yee_responses(cast(dict[str, object], responses), participant_info)
	return Audit(
		id=audit_id,
		project_id=project_id,
		place_id=place_id,
		auditor_profile_id=auditor_id,
		audit_code=audit_code,
		instrument_key="yee",
		instrument_version="1",
		status=AuditStatus.IN_PROGRESS,
		started_at=started_at,
		submitted_at=None,
		total_minutes=total_minutes,
		summary_score=float(int(cast(int, score["total_score"]))),
		responses_json={
			"participant_info": participant_info,
			"responses": responses,
		},
		scores_json=_score_cache(cast(dict[str, object], score)),
		created_at=started_at,
		updated_at=started_at + timedelta(minutes=total_minutes),
	)


def build_realistic_yee_submission(
	*,
	audit_id: uuid.UUID,
	submission_id: uuid.UUID,
	project_id: uuid.UUID,
	auditor_id: uuid.UUID,
	auditor_code: str,
	place_id: uuid.UUID,
	place_name: str,
	audit_code: str,
	started_at: datetime,
	submitted_at: datetime,
	total_minutes: int,
	domain_levels: Mapping[str, float],
	domain_weights: dict[str, int] | None = None,
	comments: str = "",
	section_comments: dict[str, str] | None = None,
	weighting_comments: dict[str, str] | None = None,
	seed_scenario: str | None = None,
	idempotency_key: str | None = None,
) -> tuple[Audit, YeeAuditSubmission]:
	responses = _build_yee_domain_scored_responses(domain_levels)
	participant_info = _build_yee_participant_info(
		auditor_code=auditor_code,
		place_id=place_id,
		place_name=place_name,
		audit_date=submitted_at.date(),
		started_at=started_at,
		total_minutes=total_minutes,
		domain_weights=domain_weights,
		comments=comments,
		section_comments=section_comments,
		weighting_comments=weighting_comments,
		seed_scenario=seed_scenario,
	)
	score = score_yee_responses(cast(dict[str, object], responses), participant_info)
	total_score = int(cast(int, score["total_score"]))
	audit = Audit(
		id=audit_id,
		project_id=project_id,
		place_id=place_id,
		auditor_profile_id=auditor_id,
		audit_code=audit_code,
		instrument_key="yee",
		instrument_version="1",
		status=AuditStatus.SUBMITTED,
		started_at=started_at,
		submitted_at=submitted_at,
		total_minutes=total_minutes,
		summary_score=float(total_score),
		responses_json=responses,
		scores_json=_score_cache(cast(dict[str, object], score)),
		created_at=started_at,
		updated_at=submitted_at,
	)
	submission = YeeAuditSubmission(
		id=submission_id,
		auditor_id=auditor_id,
		place_id=place_id,
		submitted_at=submitted_at,
		participant_info_json=participant_info,
		responses_json=responses,
		section_scores_json=score["section_scores"],
		scores_json=score["canonical_score"],
		scoring_version=SCORING_VERSION,
		total_score=total_score,
		submit_idempotency_key=idempotency_key,
		instrument_key=audit.instrument_key,
		instrument_version=audit.instrument_version,
	)
	_assert_yee_audit_submission_match(audit, submission)
	return audit, submission


# --- Deterministic multi-account bulk generation ---------------------------
#
# The hand-authored DEMO organization above stays the stable, test-anchored
# account. The factory below adds two further organizations so a freshly seeded
# database resembles a multi-tenant production snapshot: several managers and
# auditors per org, several projects/places, and ~18 assignments per org spread
# deliberately across every audit lifecycle state (not started, draft at three
# completion levels, submitted, and offline "queued" submissions with an
# idempotency key), including a same-place three-auditor comparison set.
#
# Everything is derived from stable string keys via uuid5, and all timestamps
# come from a fixed base, so repeated seeds produce byte-identical rows.

_GEN_NAMESPACE = uuid.UUID("5eed0000-0000-4000-8000-000000000000")
_GEN_BASE_DT = datetime(2026, 4, 1, 8, 0, tzinfo=timezone.utc)

_GEN_ORGS = [
	{"key": "riverbend", "org": "Riverbend Youth Alliance", "city": "Rochester"},
	{"key": "summit", "org": "Summit Community Trust", "city": "Buffalo"},
]

_GEN_PROJECT_THEMES = ["Baseline Survey", "Amenities Follow-up", "Neighborhood Pilot"]
_GEN_PLACE_NOUNS = [
	"Central Commons",
	"Riverside Park",
	"Northgate Plaza",
	"Library Green",
	"School Yard",
	"Skate Plaza",
	"Market Court",
	"Garden Terrace",
	"Civic Square",
]
_GEN_PLACE_TYPES = [
	"community hub",
	"public plaza",
	"library plaza",
	"school commons",
	"skate park",
	"market square",
	"community garden",
]
_GEN_AGE_RANGES = ["18-24", "25-34", "35-44"]
_GEN_GENDERS = ["Woman", "Man", "Non-binary"]
_GEN_ROLES = [
	"student researcher",
	"community facilitator",
	"graduate assistant",
	"youth mentor",
	"field coordinator",
	"program associate",
	"volunteer",
]
_GEN_WEIGHT_PROFILES = [
	{
		"access": 3,
		"activitySpaces": 2,
		"amenities": 2,
		"experienceOfSpace": 3,
		"aestheticsAndCare": 2,
		"useAndUsability": 2,
	},
	{
		"access": 1,
		"activitySpaces": 2,
		"amenities": 3,
		"experienceOfSpace": 2,
		"aestheticsAndCare": 1,
		"useAndUsability": 3,
	},
	{
		"access": 3,
		"activitySpaces": 1,
		"amenities": 1,
		"experienceOfSpace": 2,
		"aestheticsAndCare": 3,
		"useAndUsability": 1,
	},
	{
		"access": 2,
		"activitySpaces": 3,
		"amenities": 2,
		"experienceOfSpace": 1,
		"aestheticsAndCare": 2,
		"useAndUsability": 3,
	},
]
_GEN_STATE_LEVELS = {
	"submitted_high": _domain_levels(
		access=0.9,
		activitySpaces=0.8,
		amenities=1.0,
		experienceOfSpace=0.7,
		aestheticsAndCare=0.85,
		useAndUsability=0.9,
	),
	"submitted_mid": _domain_levels(
		access=0.5,
		activitySpaces=0.6,
		amenities=0.4,
		experienceOfSpace=0.55,
		aestheticsAndCare=0.5,
		useAndUsability=0.45,
	),
	"submitted_low": _domain_levels(
		access=0.25,
		activitySpaces=0.15,
		amenities=0.2,
		experienceOfSpace=0.2,
		aestheticsAndCare=0.1,
		useAndUsability=0.3,
	),
	"submitted_queued": _domain_levels(
		access=0.7,
		activitySpaces=0.5,
		amenities=0.6,
		experienceOfSpace=0.5,
		aestheticsAndCare=0.4,
		useAndUsability=0.65,
	),
	"draft_near": _domain_levels(
		access=0.8,
		activitySpaces=0.7,
		amenities=0.6,
		experienceOfSpace=0.65,
		aestheticsAndCare=0.7,
		useAndUsability=0.5,
	),
	"draft_partial": _domain_levels(access=0.5, activitySpaces=0.45, amenities=0.55),
	"draft_fresh": _domain_levels(access=0.6),
}
_GEN_DRAFT_DOMAINS = {
	"draft_near": tuple(DOMAIN_ORDER[:5]),
	"draft_partial": tuple(DOMAIN_ORDER[:3]),
	"draft_fresh": tuple(DOMAIN_ORDER[:1]),
}
# (auditor_index, project_index, place_index, lifecycle_state). Reused per org.
# Every (auditor, place) pair is unique. project 0 / place 0 collects three
# auditors for a same-place comparison set.
_GEN_SCHEDULE: tuple[tuple[int, int, int, str], ...] = (
	(0, 0, 0, "submitted_high"),
	(1, 0, 0, "submitted_mid"),
	(2, 0, 0, "submitted_low"),
	(0, 0, 1, "draft_near"),
	(3, 0, 1, "not_started"),
	(1, 0, 2, "submitted_queued"),
	(4, 0, 2, "draft_partial"),
	(2, 1, 0, "submitted_high"),
	(5, 1, 0, "not_started"),
	(3, 1, 1, "submitted_mid"),
	(0, 1, 1, "draft_fresh"),
	(4, 1, 2, "submitted_queued"),
	(1, 1, 2, "not_started"),
	(5, 2, 0, "submitted_low"),
	(2, 2, 0, "not_started"),
	(3, 2, 1, "submitted_mid"),
	(4, 2, 2, "submitted_high"),
	(5, 2, 2, "draft_partial"),
)
_GEN_PROJECT_COUNT = 3
_GEN_PLACES_PER_PROJECT = 3
_GEN_MANAGER_COUNT = 3
_GEN_ONBOARDED_AUDITORS = 7


def _gen_uuid(*parts: object) -> uuid.UUID:
	"""Return a stable UUID derived from the given key parts."""

	return uuid.uuid5(_GEN_NAMESPACE, ":".join(str(part) for part in parts))


def _gen_dt(days: int = 0, minutes: int = 0) -> datetime:
	"""Return a deterministic timestamp offset from the generation base."""

	return _GEN_BASE_DT + timedelta(days=days, minutes=minutes)


def _build_generated_yee_account(org_index: int, config: dict[str, str]) -> list[object]:
	"""Build one fully-populated demo organization worth of YEE entities."""

	key = config["key"]
	org_name = config["org"]
	city = config["city"]
	account_id = _gen_uuid("account", key)

	entities: list[object] = [
		Account(
			id=account_id,
			name=org_name,
			email=f"contact@{key}.example.org",
			account_type=AccountType.MANAGER,
			created_at=_gen_dt(days=org_index),
		)
	]

	# Managers: one primary plus secondaries.
	manager_user_ids: list[uuid.UUID] = []
	for m in range(_GEN_MANAGER_COUNT):
		manager_user_id = _gen_uuid("manager-user", key, m)
		manager_user_ids.append(manager_user_id)
		email = f"{key}-manager{m + 1}@example.org"
		entities.append(
			User(
				id=manager_user_id,
				email=email,
				password_hash=_demo_password_hash(),
				account_id=account_id,
				account_type=AccountType.MANAGER,
				name=f"{org_name} Manager {m + 1}",
				email_verified=True,
				email_verified_at=_gen_dt(days=org_index, minutes=10 + m),
				failed_login_attempts=0,
				approved=True,
				approved_at=_gen_dt(days=org_index, minutes=11 + m),
				profile_completed=True,
				profile_completed_at=_gen_dt(days=org_index, minutes=12 + m),
				created_at=_gen_dt(days=org_index, minutes=m),
			)
		)
		entities.append(
			ManagerProfile(
				id=_gen_uuid("manager-profile", key, m),
				account_id=account_id,
				user_id=manager_user_id,
				full_name=f"{org_name} Manager {m + 1}",
				email=email,
				phone=f"+1 585 555 0{org_index}{m}0",
				position="Program Director" if m == 0 else "Field Manager",
				profession_disciplines=["Evaluation"] if m == 0 else ["Community engagement"],
				organization=org_name,
				is_primary=(m == 0),
				created_at=_gen_dt(days=org_index, minutes=13 + m),
			)
		)
	primary_manager_user_id = manager_user_ids[0]

	# Auditors: onboarded auditors carry profiles; the last one is an invited but
	# not-yet-approved signup (no profile, cannot hold audits).
	onboarded: list[dict[str, object]] = []
	for i in range(_GEN_ONBOARDED_AUDITORS):
		auditor_user_id = _gen_uuid("auditor-user", key, i)
		auditor_profile_id = _gen_uuid("auditor-profile", key, i)
		auditor_code = f"AUD{org_index + 2}{i:02d}"
		email = f"{key}-auditor{i + 1}@example.org"
		entities.append(
			User(
				id=auditor_user_id,
				email=email,
				password_hash=_demo_password_hash(),
				account_id=account_id,
				account_type=AccountType.AUDITOR,
				name=f"{org_name} Auditor {i + 1}",
				email_verified=True,
				email_verified_at=_gen_dt(days=org_index, minutes=20 + i),
				failed_login_attempts=0,
				approved=True,
				approved_at=_gen_dt(days=org_index, minutes=21 + i),
				profile_completed=True,
				profile_completed_at=_gen_dt(days=org_index, minutes=22 + i),
				created_at=_gen_dt(days=org_index, minutes=20 + i),
			)
		)
		entities.append(
			AuditorProfile(
				id=auditor_profile_id,
				account_id=account_id,
				user_id=auditor_user_id,
				auditor_code=auditor_code,
				email=email,
				full_name=f"{org_name} Auditor {i + 1}",
				age_range=_GEN_AGE_RANGES[i % len(_GEN_AGE_RANGES)],
				gender=_GEN_GENDERS[i % len(_GEN_GENDERS)],
				country=UNITED_STATES,
				role=_GEN_ROLES[i % len(_GEN_ROLES)],
				created_at=_gen_dt(days=org_index, minutes=23 + i),
			)
		)
		onboarded.append({"profile_id": auditor_profile_id, "code": auditor_code, "user_id": auditor_user_id})

	# One invited-but-pending auditor (signed up, awaiting approval).
	entities.append(
		User(
			id=_gen_uuid("auditor-user", key, "pending"),
			email=f"{key}-pending-auditor@example.org",
			password_hash=_demo_password_hash(),
			account_id=account_id,
			account_type=AccountType.AUDITOR,
			name=f"{org_name} Pending Auditor",
			email_verified=False,
			email_verification_token_hash=f"seed-{key}-pending-auditor-verification",
			email_verification_sent_at=_gen_dt(days=org_index, minutes=40),
			email_verified_at=None,
			failed_login_attempts=0,
			approved=False,
			approved_at=None,
			profile_completed=False,
			profile_completed_at=None,
			created_at=_gen_dt(days=org_index, minutes=40),
		)
	)

	# Projects and their places.
	projects: list[uuid.UUID] = []
	places_by_project: list[list[dict[str, object]]] = []
	for p in range(_GEN_PROJECT_COUNT):
		project_id = _gen_uuid("project", key, p)
		projects.append(project_id)
		entities.append(
			Project(
				id=project_id,
				account_id=account_id,
				created_by_user_id=primary_manager_user_id,
				name=f"{org_name} — {_GEN_PROJECT_THEMES[p % len(_GEN_PROJECT_THEMES)]}",
				overview=f"{_GEN_PROJECT_THEMES[p % len(_GEN_PROJECT_THEMES)]} across youth-serving public spaces.",
				place_types=["community hub", "public plaza"],
				start_date=date(2026, 4, 1),
				end_date=date(2026, 7, 15),
				est_places=_GEN_PLACES_PER_PROJECT,
				est_auditors=_GEN_ONBOARDED_AUDITORS,
				auditor_description="Trained youth researchers and facilitators.",
				created_at=_gen_dt(days=org_index, minutes=50 + p),
			)
		)
		project_places: list[dict[str, object]] = []
		for pl in range(_GEN_PLACES_PER_PROJECT):
			flat = p * _GEN_PLACES_PER_PROJECT + pl
			place_id = _gen_uuid("place", key, p, pl)
			place_name = f"{city} {_GEN_PLACE_NOUNS[flat % len(_GEN_PLACE_NOUNS)]}"
			entities.append(
				Place(
					id=place_id,
					name=place_name,
					city=city,
					province=NEW_YORK,
					country=UNITED_STATES,
					postal_code="14600",
					place_type=_GEN_PLACE_TYPES[flat % len(_GEN_PLACE_TYPES)],
					lat=43.0 + org_index * 0.1 + flat * 0.01,
					lng=-77.6 - org_index * 0.1 - flat * 0.01,
					start_date=date(2026, 4, 2),
					end_date=date(2026, 7, 10),
					est_auditors=3,
					auditor_description=f"{place_name} field site.",
					created_at=_gen_dt(days=org_index, minutes=51 + flat),
				)
			)
			entities.append(ProjectPlace(project_id=project_id, place_id=place_id))
			project_places.append({"id": place_id, "name": place_name})
		places_by_project.append(project_places)

	# Assignments + audits/submissions across the lifecycle schedule.
	for idx, (auditor_index, project_index, place_index, state) in enumerate(_GEN_SCHEDULE):
		auditor = onboarded[auditor_index]
		project_id = projects[project_index]
		place = places_by_project[project_index][place_index]
		place_id = cast(uuid.UUID, place["id"])
		place_name = cast(str, place["name"])
		auditor_profile_id = cast(uuid.UUID, auditor["profile_id"])
		auditor_code = cast(str, auditor["code"])
		weights = _GEN_WEIGHT_PROFILES[idx % len(_GEN_WEIGHT_PROFILES)]
		started_at = _gen_dt(days=org_index + idx, minutes=60)
		total_minutes = 45 + (idx % 5) * 5
		audit_code = f"YEE-{key.upper()}-{idx:02d}"

		entities.append(
			AuditorAssignment(
				id=_gen_uuid("assignment", key, idx),
				auditor_profile_id=auditor_profile_id,
				project_id=project_id,
				place_id=place_id,
				assigned_at=_gen_dt(days=org_index + idx, minutes=30),
			)
		)

		if state == "not_started":
			continue

		if state.startswith("draft"):
			entities.append(
				build_realistic_yee_draft(
					audit_id=_gen_uuid("audit", key, idx),
					project_id=project_id,
					place_id=place_id,
					place_name=place_name,
					auditor_id=auditor_profile_id,
					auditor_code=auditor_code,
					audit_code=audit_code,
					started_at=started_at,
					total_minutes=total_minutes,
					domain_levels=_GEN_STATE_LEVELS[state],
					include_domains=_GEN_DRAFT_DOMAINS[state],
					domain_weights=weights,
					comments=f"{org_name} {state.replace('_', ' ')} at {place_name}.",
					seed_scenario=state,
				)
			)
			continue

		audit, submission = build_realistic_yee_submission(
			audit_id=_gen_uuid("audit", key, idx),
			submission_id=_gen_uuid("submission", key, idx),
			project_id=project_id,
			auditor_id=auditor_profile_id,
			auditor_code=auditor_code,
			place_id=place_id,
			place_name=place_name,
			audit_code=audit_code,
			started_at=started_at,
			submitted_at=started_at + timedelta(minutes=total_minutes),
			total_minutes=total_minutes,
			domain_levels=_GEN_STATE_LEVELS[state],
			domain_weights=weights,
			comments=f"{org_name} {state.replace('_', ' ')} at {place_name}.",
			seed_scenario=state,
			idempotency_key=(f"seed-offline-{key}-{idx}" if state == "submitted_queued" else None),
		)
		entities.append(audit)
		entities.append(submission)

	# Invite matrix: accepted for the first two onboarded auditors, plus pending
	# and expired auditor invites, and accepted/pending manager invites.
	entities.extend(
		[
			AuditorInvite(
				id=_gen_uuid("auditor-invite", key, 0),
				account_id=account_id,
				invited_by_user_id=primary_manager_user_id,
				auditor_id=cast(uuid.UUID, onboarded[0]["profile_id"]),
				email=f"{key}-auditor1@example.org",
				token_hash=f"seed-{key}-invite-auditor-0",
				created_at=_gen_dt(days=org_index, minutes=15),
				expires_at=_gen_dt(days=org_index + 7, minutes=15),
				accepted_at=_gen_dt(days=org_index, minutes=22),
			),
			AuditorInvite(
				id=_gen_uuid("auditor-invite", key, 1),
				account_id=account_id,
				invited_by_user_id=primary_manager_user_id,
				auditor_id=cast(uuid.UUID, onboarded[1]["profile_id"]),
				email=f"{key}-auditor2@example.org",
				token_hash=f"seed-{key}-invite-auditor-1",
				created_at=_gen_dt(days=org_index, minutes=16),
				expires_at=_gen_dt(days=org_index + 7, minutes=16),
				accepted_at=_gen_dt(days=org_index, minutes=23),
			),
			AuditorInvite(
				id=_gen_uuid("auditor-invite", key, "pending"),
				account_id=account_id,
				invited_by_user_id=primary_manager_user_id,
				auditor_id=None,
				email=f"{key}-invite-pending@example.org",
				token_hash=f"seed-{key}-invite-auditor-pending",
				created_at=_gen_dt(days=org_index + 20, minutes=10),
				expires_at=_gen_dt(days=org_index + 200, minutes=10),
				accepted_at=None,
			),
			AuditorInvite(
				id=_gen_uuid("auditor-invite", key, "expired"),
				account_id=account_id,
				invited_by_user_id=primary_manager_user_id,
				auditor_id=None,
				email=f"{key}-invite-expired@example.org",
				token_hash=f"seed-{key}-invite-auditor-expired",
				created_at=_gen_dt(days=org_index, minutes=5),
				expires_at=_gen_dt(days=org_index + 7, minutes=5),
				accepted_at=None,
			),
			ManagerInvite(
				id=_gen_uuid("manager-invite", key, 1),
				account_id=account_id,
				invited_by_user_id=primary_manager_user_id,
				accepted_by_user_id=manager_user_ids[1],
				email=f"{key}-manager2@example.org",
				token_hash=f"seed-{key}-invite-manager-1",
				created_at=_gen_dt(days=org_index, minutes=8),
				expires_at=_gen_dt(days=org_index + 7, minutes=8),
				accepted_at=_gen_dt(days=org_index, minutes=12),
			),
			ManagerInvite(
				id=_gen_uuid("manager-invite", key, "pending"),
				account_id=account_id,
				invited_by_user_id=primary_manager_user_id,
				accepted_by_user_id=None,
				email=f"{key}-invite-manager-pending@example.org",
				token_hash=f"seed-{key}-invite-manager-pending",
				created_at=_gen_dt(days=org_index + 20, minutes=9),
				expires_at=_gen_dt(days=org_index + 200, minutes=9),
				accepted_at=None,
			),
		]
	)

	return entities


def _build_generated_yee_accounts() -> list[object]:
	"""Build all additional demo organizations."""

	entities: list[object] = []
	for org_index, config in enumerate(_GEN_ORGS):
		entities.extend(_build_generated_yee_account(org_index, config))
	return entities


def _build_yee_entities() -> list[object]:
	"""Create deterministic YEE ORM objects for seeding."""

	yee_instrument_content = get_yee_instrument_data()

	# Source-of-truth instrument row so the YEE database mirrors Playspace: the
	# active instrument lives in the `instruments` table, and audits stamp the
	# matching (instrument_key, instrument_version) at creation time.
	canonical_instrument = Instrument(
		id=YEE_INSTRUMENT_ID,
		instrument_key="yee",
		instrument_version="1",
		parent_instrument_id=None,
		is_active=True,
		content=yee_instrument_content,
		created_at=_utc_datetime("2026-02-20T07:55:00Z"),
		updated_at=_utc_datetime("2026-02-20T07:55:00Z"),
	)

	manager_account = Account(
		id=DEMO_ACCOUNT_ID,
		name=YEE_ORGANIZATION_NAME,
		email="manager-demo@yee.local",
		account_type=AccountType.MANAGER,
		created_at=_utc_datetime("2026-02-20T08:00:00Z"),
	)

	users = [
		# Primary manager - Demo Manager (linked to YEE_MANAGER_PROFILE_PRIMARY_ID)
		User(
			id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd7"),
			email="manager-demo@yee.local",
			password_hash=_demo_password_hash(),
			account_id=DEMO_ACCOUNT_ID,
			account_type=AccountType.MANAGER,
			name="Demo Manager",
			email_verified=True,
			email_verified_at=_utc_datetime("2026-02-20T08:04:00Z"),
			failed_login_attempts=0,
			approved=True,
			approved_at=_utc_datetime("2026-02-20T08:05:00Z"),
			profile_completed=True,
			profile_completed_at=_utc_datetime("2026-02-20T08:06:00Z"),
			created_at=_utc_datetime("2026-02-20T08:03:00Z"),
		),
		# Secondary manager - Dr. Farah Khan (linked to YEE_MANAGER_PROFILE_SECONDARY_ID)
		User(
			id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd1"),
			email="farah.khan@example.org",
			password_hash=_demo_password_hash(),
			account_id=DEMO_ACCOUNT_ID,
			account_type=AccountType.MANAGER,
			name="Dr. Farah Khan",
			email_verified=True,
			email_verified_at=_utc_datetime("2026-02-20T08:05:00Z"),
			failed_login_attempts=0,
			approved=True,
			approved_at=_utc_datetime("2026-02-20T08:06:00Z"),
			profile_completed=True,
			profile_completed_at=_utc_datetime("2026-02-20T08:10:00Z"),
			created_at=_utc_datetime("2026-02-20T08:00:00Z"),
		),
		# Secondary manager - Jordan Alvarez
		User(
			id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd6"),
			email="jordan.alvarez@example.org",
			password_hash=_demo_password_hash(),
			account_id=DEMO_ACCOUNT_ID,
			account_type=AccountType.MANAGER,
			name="Jordan Alvarez",
			email_verified=True,
			email_verified_at=_utc_datetime("2026-02-20T08:15:00Z"),
			failed_login_attempts=0,
			approved=True,
			approved_at=_utc_datetime("2026-02-20T08:16:00Z"),
			profile_completed=True,
			profile_completed_at=_utc_datetime("2026-02-20T08:20:00Z"),
			created_at=_utc_datetime("2026-02-20T08:10:00Z"),
		),
		User(
			id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd2"),
			email="admin-demo@yee.local",
			password_hash=_demo_password_hash(),
			account_id=None,
			account_type=AccountType.ADMIN,
			name="Demo Admin",
			email_verified=True,
			email_verified_at=_utc_datetime("2026-02-20T08:15:00Z"),
			failed_login_attempts=0,
			approved=True,
			approved_at=_utc_datetime("2026-02-20T08:16:00Z"),
			profile_completed=True,
			profile_completed_at=_utc_datetime("2026-02-20T08:17:00Z"),
			created_at=_utc_datetime("2026-02-20T08:10:00Z"),
		),
		User(
			id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd3"),
			email="auditor-demo-1@yee.local",
			password_hash=_demo_password_hash(),
			account_id=DEMO_ACCOUNT_ID,
			account_type=AccountType.AUDITOR,
			name="Demo Auditor One",
			email_verified=True,
			email_verified_at=_utc_datetime("2026-02-22T09:10:00Z"),
			failed_login_attempts=0,
			approved=True,
			approved_at=_utc_datetime("2026-02-22T09:11:00Z"),
			profile_completed=True,
			profile_completed_at=_utc_datetime("2026-02-22T09:12:00Z"),
			created_at=_utc_datetime("2026-02-22T09:00:00Z"),
		),
		User(
			id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd4"),
			email="auditor-demo-2@yee.local",
			password_hash=_demo_password_hash(),
			account_id=DEMO_ACCOUNT_ID,
			account_type=AccountType.AUDITOR,
			name="Demo Auditor Two",
			email_verified=True,
			email_verified_at=_utc_datetime("2026-02-22T09:15:00Z"),
			failed_login_attempts=0,
			approved=True,
			approved_at=_utc_datetime("2026-02-22T09:16:00Z"),
			profile_completed=True,
			profile_completed_at=_utc_datetime("2026-02-22T09:17:00Z"),
			created_at=_utc_datetime("2026-02-22T09:05:00Z"),
		),
		User(
			id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd5"),
			email="auditor-demo-3@yee.local",
			password_hash=_demo_password_hash(),
			account_id=DEMO_ACCOUNT_ID,
			account_type=AccountType.AUDITOR,
			name="Demo Auditor Three",
			email_verified=True,
			email_verified_at=_utc_datetime("2026-02-22T09:20:00Z"),
			failed_login_attempts=0,
			approved=True,
			approved_at=_utc_datetime("2026-02-22T09:21:00Z"),
			profile_completed=True,
			profile_completed_at=_utc_datetime("2026-02-22T09:22:00Z"),
			created_at=_utc_datetime("2026-02-22T09:10:00Z"),
		),
		User(
			id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd8"),
			email="pending-auditor@yee.local",
			password_hash=_demo_password_hash(),
			account_id=None,
			account_type=AccountType.AUDITOR,
			name="Pending Auditor",
			email_verified=False,
			email_verification_token_hash="seed-pending-auditor-verification",
			email_verification_sent_at=_utc_datetime("2026-06-28T15:00:00Z"),
			email_verified_at=None,
			failed_login_attempts=0,
			approved=False,
			approved_at=None,
			profile_completed=False,
			profile_completed_at=None,
			created_at=_utc_datetime("2026-06-28T15:00:00Z"),
		),
		User(
			id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd9"),
			email="former-manager@yee.local",
			password_hash=_demo_password_hash(),
			account_id=None,
			account_type=AccountType.MANAGER,
			name="Former Manager",
			email_verified=True,
			email_verified_at=_utc_datetime("2026-03-11T14:00:00Z"),
			failed_login_attempts=0,
			approved=True,
			approved_at=_utc_datetime("2026-03-11T14:05:00Z"),
			profile_completed=True,
			profile_completed_at=_utc_datetime("2026-03-11T14:10:00Z"),
			created_at=_utc_datetime("2026-03-11T13:55:00Z"),
		),
	]

	manager_profiles = [
		ManagerProfile(
			id=YEE_MANAGER_PROFILE_PRIMARY_ID,
			account_id=DEMO_ACCOUNT_ID,
			user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd7"),
			full_name="Demo Manager",
			email="manager-demo@yee.local",
			phone="+1 607 555 0100",
			position="Demo account reviewer",
			profession_disciplines=["Evaluation", "Program management"],
			organization=YEE_ORGANIZATION_NAME,
			is_primary=True,
			created_at=_utc_datetime("2026-02-20T08:06:00Z"),
		),
		ManagerProfile(
			id=YEE_MANAGER_PROFILE_SECONDARY_ID,
			account_id=DEMO_ACCOUNT_ID,
			user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd1"),
			full_name="Dr. Farah Khan",
			email="farah.khan@example.org",
			phone="+1 607 555 0147",
			position="Principal Investigator",
			profession_disciplines=["Public health", "Environmental design"],
			organization=YEE_ORGANIZATION_NAME,
			is_primary=False,
			created_at=_utc_datetime("2026-02-20T08:10:00Z"),
		),
		ManagerProfile(
			id=uuid.UUID("77777777-7777-4777-8777-777777777770"),
			account_id=DEMO_ACCOUNT_ID,
			user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd6"),
			full_name="Jordan Alvarez",
			email="jordan.alvarez@example.org",
			phone=None,
			position="Field Operations Lead",
			profession_disciplines=["Community engagement"],
			organization=YEE_ORGANIZATION_NAME,
			is_primary=False,
			created_at=_utc_datetime("2026-02-20T08:20:00Z"),
		),
	]

	auditor_profiles = [
		AuditorProfile(
			id=YEE_AUDITOR_PROFILE_01_ID,
			account_id=DEMO_ACCOUNT_ID,
			user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd3"),
			auditor_code="AUD001",
			email="auditor-demo-1@yee.local",
			full_name="Demo Auditor One",
			age_range="18-24",
			gender="Woman",
			country=UNITED_STATES,
			role="student researcher",
			created_at=_utc_datetime("2026-02-22T09:20:00Z"),
		),
		AuditorProfile(
			id=YEE_AUDITOR_PROFILE_02_ID,
			account_id=DEMO_ACCOUNT_ID,
			user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd4"),
			auditor_code="AUD002",
			email="auditor-demo-2@yee.local",
			full_name="Demo Auditor Two",
			age_range="25-34",
			gender="Man",
			country=UNITED_STATES,
			role="community facilitator",
			created_at=_utc_datetime("2026-02-22T09:25:00Z"),
		),
		AuditorProfile(
			id=YEE_AUDITOR_PROFILE_03_ID,
			account_id=DEMO_ACCOUNT_ID,
			user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd5"),
			auditor_code="AUD003",
			email="auditor-demo-3@yee.local",
			full_name="Demo Auditor Three",
			age_range="18-24",
			gender="Woman",
			country=UNITED_STATES,
			role="graduate assistant",
			created_at=_utc_datetime("2026-02-22T09:30:00Z"),
		),
	]

	projects = [
		Project(
			id=YEE_PROJECT_CORE_ID,
			account_id=DEMO_ACCOUNT_ID,
			created_by_user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd1"),
			name="Youth Enabling Environments Baseline 2026",
			overview="Baseline assessment of youth-serving public spaces.",
			place_types=["community hub", "public plaza"],
			start_date=date(2026, 2, 24),
			end_date=date(2026, 6, 10),
			est_places=10,
			est_auditors=4,
			auditor_description="Pairs of trained youth researchers and facilitators.",
			created_at=_utc_datetime("2026-02-21T14:00:00Z"),
		),
		Project(
			id=YEE_PROJECT_FOLLOW_UP_ID,
			account_id=DEMO_ACCOUNT_ID,
			created_by_user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd1"),
			name="Community Amenities Follow-up",
			overview="Follow-up sampling focused on usability, amenities, and experience of space.",
			place_types=["library plaza", "school commons"],
			start_date=date(2026, 3, 4),
			end_date=date(2026, 6, 24),
			est_places=6,
			est_auditors=3,
			auditor_description="Smaller team revisits with structured scoring review.",
			created_at=_utc_datetime("2026-03-01T13:00:00Z"),
		),
		Project(
			id=YEE_PROJECT_PILOT_ID,
			account_id=DEMO_ACCOUNT_ID,
			created_by_user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd7"),
			name="Downtown Youth Spaces Pilot",
			overview="Scoring-calibration pilot exercising raw, weighted, and combined report outputs.",
			place_types=["skate park", "market square", "community garden"],
			start_date=date(2026, 3, 12),
			end_date=date(2026, 6, 30),
			est_places=4,
			est_auditors=3,
			auditor_description="Full three-auditor team revisiting shared sites for comparison reporting.",
			created_at=_utc_datetime("2026-03-10T13:00:00Z"),
		),
	]

	places = [
		Place(
			id=YEE_PLACE_HUB_ID,
			name="Westside Youth Hub",
			city="Ithaca",
			province=NEW_YORK,
			country=UNITED_STATES,
			postal_code="14850",
			place_type="community hub",
			lat=42.443,
			lng=-76.5019,
			start_date=date(2026, 2, 26),
			end_date=date(2026, 5, 30),
			est_auditors=3,
			auditor_description="Access and amenities baseline with youth wayfinding observations.",
			created_at=_utc_datetime("2026-02-23T10:00:00Z"),
		),
		Place(
			id=YEE_PLACE_PLAZA_ID,
			name="South Transit Plaza",
			city="Ithaca",
			province=NEW_YORK,
			country=UNITED_STATES,
			postal_code="14850",
			place_type="public plaza",
			lat=42.4398,
			lng=-76.4966,
			start_date=date(2026, 2, 28),
			end_date=date(2026, 6, 1),
			est_auditors=2,
			auditor_description="Transit-adjacent site for access and safety review.",
			created_at=_utc_datetime("2026-02-23T10:10:00Z"),
		),
		Place(
			id=YEE_PLACE_LIBRARY_ID,
			name="Maple Library Plaza",
			city="Ithaca",
			province=NEW_YORK,
			country=UNITED_STATES,
			postal_code="14850",
			place_type="library plaza",
			lat=42.4404,
			lng=-76.4977,
			start_date=date(2026, 3, 6),
			end_date=date(2026, 6, 18),
			est_auditors=2,
			auditor_description="Follow-up on experience and aesthetics near library services.",
			created_at=_utc_datetime("2026-03-02T11:00:00Z"),
		),
		Place(
			id=YEE_PLACE_COMMONS_ID,
			name="North School Commons",
			city="Ithaca",
			province=NEW_YORK,
			country=UNITED_STATES,
			postal_code="14850",
			place_type="school commons",
			lat=42.4461,
			lng=-76.4934,
			start_date=date(2026, 3, 8),
			end_date=date(2026, 6, 24),
			est_auditors=2,
			auditor_description="In-progress site focused on use and usability patterns.",
			created_at=_utc_datetime("2026-03-02T11:10:00Z"),
		),
		Place(
			id=YEE_PLACE_GREEN_ID,
			name="Eastside Community Green",
			city="Ithaca",
			province=NEW_YORK,
			country=UNITED_STATES,
			postal_code="14850",
			place_type="public plaza",
			lat=42.4415,
			lng=-76.4881,
			start_date=date(2026, 3, 10),
			end_date=date(2026, 6, 30),
			est_auditors=2,
			auditor_description="Newly assigned site auditor 1 has not visited yet.",
			created_at=_utc_datetime("2026-03-02T11:20:00Z"),
		),
		Place(
			id=YEE_PLACE_RIVERSIDE_ID,
			name="Riverside Skate Park",
			city="Ithaca",
			province=NEW_YORK,
			country=UNITED_STATES,
			postal_code="14850",
			place_type="skate park",
			lat=42.4487,
			lng=-76.5083,
			start_date=date(2026, 3, 12),
			end_date=date(2026, 6, 20),
			est_auditors=3,
			auditor_description="Second three-auditor comparison set for raw and weighted score spread.",
			created_at=_utc_datetime("2026-03-11T10:00:00Z"),
		),
		Place(
			id=YEE_PLACE_MARKET_ID,
			name="Market Square",
			city="Ithaca",
			province=NEW_YORK,
			country=UNITED_STATES,
			postal_code="14850",
			place_type="market square",
			lat=42.4392,
			lng=-76.4958,
			start_date=date(2026, 3, 14),
			end_date=date(2026, 6, 22),
			est_auditors=3,
			auditor_description="Weighting-calibration site: identical raw responses scored under different weights.",
			created_at=_utc_datetime("2026-03-11T10:10:00Z"),
		),
		Place(
			id=YEE_PLACE_GARDEN_ID,
			name="Harbor Community Garden",
			city="Ithaca",
			province=NEW_YORK,
			country=UNITED_STATES,
			postal_code="14850",
			place_type="community garden",
			lat=42.4451,
			lng=-76.5027,
			start_date=date(2026, 3, 16),
			end_date=date(2026, 6, 28),
			est_auditors=2,
			auditor_description="Freshly started site with a single-section minimal draft.",
			created_at=_utc_datetime("2026-03-11T10:20:00Z"),
		),
	]
	project_places = [
		ProjectPlace(project_id=YEE_PROJECT_CORE_ID, place_id=YEE_PLACE_HUB_ID),
		ProjectPlace(project_id=YEE_PROJECT_CORE_ID, place_id=YEE_PLACE_PLAZA_ID),
		ProjectPlace(project_id=YEE_PROJECT_CORE_ID, place_id=YEE_PLACE_GREEN_ID),
		ProjectPlace(project_id=YEE_PROJECT_FOLLOW_UP_ID, place_id=YEE_PLACE_LIBRARY_ID),
		ProjectPlace(project_id=YEE_PROJECT_FOLLOW_UP_ID, place_id=YEE_PLACE_COMMONS_ID),
		ProjectPlace(project_id=YEE_PROJECT_PILOT_ID, place_id=YEE_PLACE_RIVERSIDE_ID),
		ProjectPlace(project_id=YEE_PROJECT_PILOT_ID, place_id=YEE_PLACE_MARKET_ID),
		ProjectPlace(project_id=YEE_PROJECT_PILOT_ID, place_id=YEE_PLACE_GARDEN_ID),
	]

	auditor_invites = [
		AuditorInvite(
			id=uuid.UUID("d1000000-0000-4000-8000-000000000001"),
			account_id=DEMO_ACCOUNT_ID,
			invited_by_user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd1"),
			auditor_id=YEE_AUDITOR_PROFILE_01_ID,
			email="auditor-demo-1@yee.local",
			token_hash="seed-invite-auditor-1",
			created_at=_utc_datetime("2026-02-21T09:00:00Z"),
			expires_at=_utc_datetime("2026-02-28T09:00:00Z"),
			accepted_at=_utc_datetime("2026-02-22T09:18:00Z"),
		),
		AuditorInvite(
			id=uuid.UUID("d1000000-0000-4000-8000-000000000002"),
			account_id=DEMO_ACCOUNT_ID,
			invited_by_user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd1"),
			auditor_id=YEE_AUDITOR_PROFILE_02_ID,
			email="auditor-demo-2@yee.local",
			token_hash="seed-invite-auditor-2",
			created_at=_utc_datetime("2026-02-21T09:10:00Z"),
			expires_at=_utc_datetime("2026-02-28T09:10:00Z"),
			accepted_at=_utc_datetime("2026-02-22T09:23:00Z"),
		),
		AuditorInvite(
			id=uuid.UUID("d1000000-0000-4000-8000-000000000003"),
			account_id=DEMO_ACCOUNT_ID,
			invited_by_user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd1"),
			auditor_id=YEE_AUDITOR_PROFILE_03_ID,
			email="auditor-demo-3@yee.local",
			token_hash="seed-invite-auditor-3",
			created_at=_utc_datetime("2026-03-01T09:10:00Z"),
			expires_at=_utc_datetime("2026-03-08T09:10:00Z"),
			accepted_at=_utc_datetime("2026-03-02T09:29:00Z"),
		),
		AuditorInvite(
			id=uuid.UUID("d1000000-0000-4000-8000-000000000004"),
			account_id=DEMO_ACCOUNT_ID,
			invited_by_user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd1"),
			auditor_id=None,
			email="pending-field-auditor@example.org",
			token_hash="seed-invite-auditor-pending",
			created_at=_utc_datetime("2026-06-25T10:30:00Z"),
			expires_at=_utc_datetime("2026-12-31T10:30:00Z"),
			accepted_at=None,
		),
		AuditorInvite(
			id=uuid.UUID("d1000000-0000-4000-8000-000000000005"),
			account_id=DEMO_ACCOUNT_ID,
			invited_by_user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd7"),
			auditor_id=None,
			email="expired-field-auditor@example.org",
			token_hash="seed-invite-auditor-expired",
			created_at=_utc_datetime("2026-04-01T10:30:00Z"),
			expires_at=_utc_datetime("2026-04-08T10:30:00Z"),
			accepted_at=None,
		),
	]

	manager_invites = [
		ManagerInvite(
			id=uuid.UUID("d3000000-0000-4000-8000-000000000001"),
			account_id=DEMO_ACCOUNT_ID,
			invited_by_user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd7"),
			accepted_by_user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd6"),
			email="jordan.alvarez@example.org",
			token_hash="seed-invite-manager-accepted",
			created_at=_utc_datetime("2026-02-20T08:12:00Z"),
			expires_at=_utc_datetime("2026-02-27T08:12:00Z"),
			accepted_at=_utc_datetime("2026-02-20T08:18:00Z"),
		),
		ManagerInvite(
			id=uuid.UUID("d3000000-0000-4000-8000-000000000002"),
			account_id=DEMO_ACCOUNT_ID,
			invited_by_user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd7"),
			accepted_by_user_id=None,
			email="morgan.lee@example.org",
			token_hash="seed-invite-manager-pending",
			created_at=_utc_datetime("2026-06-29T09:00:00Z"),
			expires_at=_utc_datetime("2026-12-31T09:00:00Z"),
			accepted_at=None,
		),
	]

	assignments = [
		AuditorAssignment(
			id=uuid.UUID("d2000000-0000-4000-8000-000000000001"),
			auditor_profile_id=YEE_AUDITOR_PROFILE_01_ID,
			project_id=YEE_PROJECT_CORE_ID,
			place_id=YEE_PLACE_HUB_ID,
			assigned_at=_utc_datetime("2026-02-24T08:00:00Z"),
		),
		AuditorAssignment(
			id=uuid.UUID("d2000000-0000-4000-8000-000000000002"),
			auditor_profile_id=YEE_AUDITOR_PROFILE_01_ID,
			project_id=YEE_PROJECT_CORE_ID,
			place_id=YEE_PLACE_PLAZA_ID,
			assigned_at=_utc_datetime("2026-02-26T09:00:00Z"),
		),
		AuditorAssignment(
			id=uuid.UUID("d2000000-0000-4000-8000-000000000006"),
			auditor_profile_id=YEE_AUDITOR_PROFILE_01_ID,
			project_id=YEE_PROJECT_CORE_ID,
			place_id=YEE_PLACE_GREEN_ID,
			assigned_at=_utc_datetime("2026-03-10T08:00:00Z"),
		),
		AuditorAssignment(
			id=uuid.UUID("d2000000-0000-4000-8000-000000000003"),
			auditor_profile_id=YEE_AUDITOR_PROFILE_02_ID,
			project_id=YEE_PROJECT_CORE_ID,
			place_id=YEE_PLACE_PLAZA_ID,
			assigned_at=_utc_datetime("2026-02-24T08:05:00Z"),
		),
		AuditorAssignment(
			id=uuid.UUID("d2000000-0000-4000-8000-000000000007"),
			auditor_profile_id=YEE_AUDITOR_PROFILE_02_ID,
			project_id=YEE_PROJECT_CORE_ID,
			place_id=YEE_PLACE_HUB_ID,
			assigned_at=_utc_datetime("2026-02-24T08:10:00Z"),
		),
		AuditorAssignment(
			id=uuid.UUID("d2000000-0000-4000-8000-000000000008"),
			auditor_profile_id=YEE_AUDITOR_PROFILE_03_ID,
			project_id=YEE_PROJECT_CORE_ID,
			place_id=YEE_PLACE_HUB_ID,
			assigned_at=_utc_datetime("2026-02-24T08:15:00Z"),
		),
		AuditorAssignment(
			id=uuid.UUID("d2000000-0000-4000-8000-000000000004"),
			auditor_profile_id=YEE_AUDITOR_PROFILE_03_ID,
			project_id=YEE_PROJECT_FOLLOW_UP_ID,
			place_id=YEE_PLACE_COMMONS_ID,
			assigned_at=_utc_datetime("2026-03-04T08:30:00Z"),
		),
		AuditorAssignment(
			id=uuid.UUID("d2000000-0000-4000-8000-000000000005"),
			auditor_profile_id=YEE_AUDITOR_PROFILE_03_ID,
			project_id=YEE_PROJECT_FOLLOW_UP_ID,
			place_id=YEE_PLACE_LIBRARY_ID,
			assigned_at=_utc_datetime("2026-03-06T08:30:00Z"),
		),
		# Pilot project: all three auditors on Riverside (comparison set), and the
		# weighting-calibration pairings on Market. Garden is a fresh draft slot.
		AuditorAssignment(
			id=uuid.UUID("d2000000-0000-4000-8000-000000000009"),
			auditor_profile_id=YEE_AUDITOR_PROFILE_01_ID,
			project_id=YEE_PROJECT_PILOT_ID,
			place_id=YEE_PLACE_RIVERSIDE_ID,
			assigned_at=_utc_datetime("2026-03-12T08:00:00Z"),
		),
		AuditorAssignment(
			id=uuid.UUID("d2000000-0000-4000-8000-00000000000a"),
			auditor_profile_id=YEE_AUDITOR_PROFILE_02_ID,
			project_id=YEE_PROJECT_PILOT_ID,
			place_id=YEE_PLACE_RIVERSIDE_ID,
			assigned_at=_utc_datetime("2026-03-12T08:05:00Z"),
		),
		AuditorAssignment(
			id=uuid.UUID("d2000000-0000-4000-8000-00000000000b"),
			auditor_profile_id=YEE_AUDITOR_PROFILE_03_ID,
			project_id=YEE_PROJECT_PILOT_ID,
			place_id=YEE_PLACE_RIVERSIDE_ID,
			assigned_at=_utc_datetime("2026-03-12T08:10:00Z"),
		),
		AuditorAssignment(
			id=uuid.UUID("d2000000-0000-4000-8000-00000000000c"),
			auditor_profile_id=YEE_AUDITOR_PROFILE_01_ID,
			project_id=YEE_PROJECT_PILOT_ID,
			place_id=YEE_PLACE_MARKET_ID,
			assigned_at=_utc_datetime("2026-03-14T08:00:00Z"),
		),
		AuditorAssignment(
			id=uuid.UUID("d2000000-0000-4000-8000-00000000000d"),
			auditor_profile_id=YEE_AUDITOR_PROFILE_02_ID,
			project_id=YEE_PROJECT_PILOT_ID,
			place_id=YEE_PLACE_MARKET_ID,
			assigned_at=_utc_datetime("2026-03-14T08:05:00Z"),
		),
		AuditorAssignment(
			id=uuid.UUID("d2000000-0000-4000-8000-00000000000e"),
			auditor_profile_id=YEE_AUDITOR_PROFILE_03_ID,
			project_id=YEE_PROJECT_PILOT_ID,
			place_id=YEE_PLACE_MARKET_ID,
			assigned_at=_utc_datetime("2026-03-14T08:10:00Z"),
		),
		AuditorAssignment(
			id=uuid.UUID("d2000000-0000-4000-8000-00000000000f"),
			auditor_profile_id=YEE_AUDITOR_PROFILE_02_ID,
			project_id=YEE_PROJECT_PILOT_ID,
			place_id=YEE_PLACE_GARDEN_ID,
			assigned_at=_utc_datetime("2026-03-16T08:00:00Z"),
		),
	]

	medium_weights = {
		"access": 1,
		"activitySpaces": 2,
		"amenities": 3,
		"experienceOfSpace": 2,
		"aestheticsAndCare": 1,
		"useAndUsability": 3,
	}
	low_weights = {
		"access": 3,
		"activitySpaces": 1,
		"amenities": 1,
		"experienceOfSpace": 2,
		"aestheticsAndCare": 3,
		"useAndUsability": 1,
	}
	plaza_weights = {
		"access": 1,
		"activitySpaces": 3,
		"amenities": 2,
		"experienceOfSpace": 1,
		"aestheticsAndCare": 3,
		"useAndUsability": 2,
	}
	missing_weight_edge = {
		"access": 2,
		"activitySpaces": 1,
		"amenities": 2,
		"experienceOfSpace": 3,
		"aestheticsAndCare": 1,
	}
	# Weighting-calibration pair (Market): identical responses, different weights.
	# Auditor 1 weights access/experience highest; auditor 2 flips emphasis to
	# amenities/use so the weighted ranking diverges from the (equal) raw score.
	market_weights_a = {
		"access": 3,
		"activitySpaces": 1,
		"amenities": 1,
		"experienceOfSpace": 3,
		"aestheticsAndCare": 2,
		"useAndUsability": 1,
	}
	market_weights_b = {
		"access": 1,
		"activitySpaces": 2,
		"amenities": 3,
		"experienceOfSpace": 1,
		"aestheticsAndCare": 1,
		"useAndUsability": 3,
	}
	# Zero-weight edge: high raw score but every domain weight 0, so the weighted
	# total collapses to 0.0 while the raw total stays high.
	zero_weights = {domain: 0 for domain in DOMAIN_ORDER}
	# Shared response profile for the Market weighting pair so raw scores match.
	market_shared_levels = _domain_levels(
		access=0.8,
		activitySpaces=0.4,
		amenities=0.6,
		experienceOfSpace=0.5,
		aestheticsAndCare=0.3,
		useAndUsability=0.7,
	)

	submission_pairs = [
		build_realistic_yee_submission(
			audit_id=YEE_AUDIT_HUB_ID,
			submission_id=YEE_SUBMISSION_HUB_ID,
			project_id=YEE_PROJECT_CORE_ID,
			auditor_id=YEE_AUDITOR_PROFILE_01_ID,
			auditor_code="AUD001",
			place_id=YEE_PLACE_HUB_ID,
			place_name="Westside Youth Hub",
			audit_code="YEE-HUB-01-2026-03-02",
			started_at=_utc_datetime("2026-03-02T13:00:00Z"),
			submitted_at=_utc_datetime("2026-03-02T14:05:00Z"),
			total_minutes=65,
			domain_levels=_uniform_domain_levels(1.0),
			comments="Strong transit access and youth-visible programming during the visit.",
			section_comments={"Access": "Clear wayfinding from the bus stop."},
			weighting_comments={"access": "Access was highest priority for the baseline visit."},
			seed_scenario="submitted_high_score",
		),
		build_realistic_yee_submission(
			audit_id=YEE_AUDIT_HUB_AUDITOR_02_ID,
			submission_id=YEE_SUBMISSION_HUB_AUDITOR_02_ID,
			project_id=YEE_PROJECT_CORE_ID,
			auditor_id=YEE_AUDITOR_PROFILE_02_ID,
			auditor_code="AUD002",
			place_id=YEE_PLACE_HUB_ID,
			place_name="Westside Youth Hub",
			audit_code="YEE-HUB-02-2026-03-03",
			started_at=_utc_datetime("2026-03-03T15:10:00Z"),
			submitted_at=_utc_datetime("2026-03-03T16:02:00Z"),
			total_minutes=52,
			domain_levels=_domain_levels(
				access=0.5,
				activitySpaces=0.7,
				amenities=0.4,
				experienceOfSpace=0.6,
				aestheticsAndCare=0.5,
				useAndUsability=0.35,
			),
			domain_weights=medium_weights,
			comments="Activity spaces were active, but amenities were harder to find after school.",
			section_comments={"Amenities": "Water and seating were available but not obvious."},
			seed_scenario="submitted_medium_score_same_place",
			idempotency_key="seed-mobile-offline-hub-aud002",
		),
		build_realistic_yee_submission(
			audit_id=YEE_AUDIT_HUB_AUDITOR_03_ID,
			submission_id=YEE_SUBMISSION_HUB_AUDITOR_03_ID,
			project_id=YEE_PROJECT_CORE_ID,
			auditor_id=YEE_AUDITOR_PROFILE_03_ID,
			auditor_code="AUD003",
			place_id=YEE_PLACE_HUB_ID,
			place_name="Westside Youth Hub",
			audit_code="YEE-HUB-03-2026-03-04",
			started_at=_utc_datetime("2026-03-04T09:20:00Z"),
			submitted_at=_utc_datetime("2026-03-04T10:15:00Z"),
			total_minutes=55,
			domain_levels=_domain_levels(
				access=0.25,
				activitySpaces=0.15,
				amenities=0.2,
				experienceOfSpace=0.2,
				aestheticsAndCare=0.1,
				useAndUsability=0.3,
			),
			domain_weights=low_weights,
			comments="Morning visit captured sparse youth use and several maintenance gaps.",
			section_comments={"Use & Usability": "Few youth were present during the observation window."},
			weighting_comments={"aestheticsAndCare": "Care concerns were emphasized after walkthrough."},
			seed_scenario="submitted_low_score_same_place",
		),
		build_realistic_yee_submission(
			audit_id=YEE_AUDIT_PLAZA_ID,
			submission_id=YEE_SUBMISSION_PLAZA_ID,
			project_id=YEE_PROJECT_CORE_ID,
			auditor_id=YEE_AUDITOR_PROFILE_02_ID,
			auditor_code="AUD002",
			place_id=YEE_PLACE_PLAZA_ID,
			place_name="South Transit Plaza",
			audit_code="YEE-PLAZA-02-2026-03-03",
			started_at=_utc_datetime("2026-03-03T10:15:00Z"),
			submitted_at=_utc_datetime("2026-03-03T11:10:00Z"),
			total_minutes=55,
			domain_levels=_domain_levels(
				access=1.0,
				activitySpaces=0.3,
				amenities=0.9,
				experienceOfSpace=0.5,
				aestheticsAndCare=0.2,
				useAndUsability=0.85,
			),
			domain_weights=plaza_weights,
			comments="Good circulation and social visibility, with some comfort tradeoffs near traffic.",
			section_comments={"Experience": "Noise from the roadway affected comfort."},
			seed_scenario="submitted_mixed_domain_score",
		),
		build_realistic_yee_submission(
			audit_id=YEE_AUDIT_LIBRARY_ID,
			submission_id=YEE_SUBMISSION_LIBRARY_ID,
			project_id=YEE_PROJECT_FOLLOW_UP_ID,
			auditor_id=YEE_AUDITOR_PROFILE_03_ID,
			auditor_code="AUD003",
			place_id=YEE_PLACE_LIBRARY_ID,
			place_name="Maple Library Plaza",
			audit_code="YEE-LIBRARY-03-2026-03-07",
			started_at=_utc_datetime("2026-03-07T12:00:00Z"),
			submitted_at=_utc_datetime("2026-03-07T12:50:00Z"),
			total_minutes=50,
			domain_levels=_uniform_domain_levels(0.55),
			domain_weights=missing_weight_edge,
			comments="Library services supported youth presence, but one weighting domain was left blank.",
			section_comments={"Aesthetics & Care": "Landscaping was well maintained."},
			weighting_comments={"useAndUsability": "Intentionally omitted to exercise missing-weight handling."},
			seed_scenario="submitted_missing_weight_edge",
		),
		# Pilot / Riverside: a second same-place three-auditor comparison set with a
		# full max-to-low raw spread and distinct weighted totals.
		build_realistic_yee_submission(
			audit_id=YEE_AUDIT_RIVERSIDE_01_ID,
			submission_id=YEE_SUBMISSION_RIVERSIDE_01_ID,
			project_id=YEE_PROJECT_PILOT_ID,
			auditor_id=YEE_AUDITOR_PROFILE_01_ID,
			auditor_code="AUD001",
			place_id=YEE_PLACE_RIVERSIDE_ID,
			place_name="Riverside Skate Park",
			audit_code="YEE-RIVERSIDE-01-2026-03-13",
			started_at=_utc_datetime("2026-03-13T13:00:00Z"),
			submitted_at=_utc_datetime("2026-03-13T14:00:00Z"),
			total_minutes=60,
			domain_levels=_uniform_domain_levels(1.0),
			comments="Every domain fully provisioned; used as the max-score anchor for the pilot.",
			seed_scenario="submitted_max_score_edge",
		),
		build_realistic_yee_submission(
			audit_id=YEE_AUDIT_RIVERSIDE_02_ID,
			submission_id=YEE_SUBMISSION_RIVERSIDE_02_ID,
			project_id=YEE_PROJECT_PILOT_ID,
			auditor_id=YEE_AUDITOR_PROFILE_02_ID,
			auditor_code="AUD002",
			place_id=YEE_PLACE_RIVERSIDE_ID,
			place_name="Riverside Skate Park",
			audit_code="YEE-RIVERSIDE-02-2026-03-13",
			started_at=_utc_datetime("2026-03-13T15:00:00Z"),
			submitted_at=_utc_datetime("2026-03-13T15:58:00Z"),
			total_minutes=58,
			domain_levels=_domain_levels(
				access=0.6,
				activitySpaces=0.5,
				amenities=0.7,
				experienceOfSpace=0.4,
				aestheticsAndCare=0.6,
				useAndUsability=0.5,
			),
			domain_weights=medium_weights,
			comments="Solid mid-range visit; strongest on amenities, weakest on experience of space.",
			seed_scenario="submitted_mid_score_same_place",
			idempotency_key="seed-mobile-offline-riverside-aud002",
		),
		build_realistic_yee_submission(
			audit_id=YEE_AUDIT_RIVERSIDE_03_ID,
			submission_id=YEE_SUBMISSION_RIVERSIDE_03_ID,
			project_id=YEE_PROJECT_PILOT_ID,
			auditor_id=YEE_AUDITOR_PROFILE_03_ID,
			auditor_code="AUD003",
			place_id=YEE_PLACE_RIVERSIDE_ID,
			place_name="Riverside Skate Park",
			audit_code="YEE-RIVERSIDE-03-2026-03-14",
			started_at=_utc_datetime("2026-03-14T09:00:00Z"),
			submitted_at=_utc_datetime("2026-03-14T09:47:00Z"),
			total_minutes=47,
			domain_levels=_domain_levels(
				access=0.3,
				activitySpaces=0.4,
				amenities=0.2,
				experienceOfSpace=0.35,
				aestheticsAndCare=0.25,
				useAndUsability=0.2,
			),
			domain_weights=low_weights,
			comments="Sparse provision across most domains during a quiet morning window.",
			seed_scenario="submitted_low_score_same_place",
		),
		# Pilot / Market: weighting-calibration pair. Identical responses, so raw
		# totals match, but different weights make the weighted totals diverge.
		build_realistic_yee_submission(
			audit_id=YEE_AUDIT_MARKET_LOW_WEIGHT_ID,
			submission_id=YEE_SUBMISSION_MARKET_LOW_WEIGHT_ID,
			project_id=YEE_PROJECT_PILOT_ID,
			auditor_id=YEE_AUDITOR_PROFILE_01_ID,
			auditor_code="AUD001",
			place_id=YEE_PLACE_MARKET_ID,
			place_name="Market Square",
			audit_code="YEE-MARKET-01-2026-03-15",
			started_at=_utc_datetime("2026-03-15T11:00:00Z"),
			submitted_at=_utc_datetime("2026-03-15T11:52:00Z"),
			total_minutes=52,
			domain_levels=market_shared_levels,
			domain_weights=market_weights_a,
			comments="Weighting-pair A: access and experience marked most important.",
			weighting_comments={"access": "Access weighted highest by this auditor."},
			seed_scenario="submitted_weight_sensitivity_a",
		),
		build_realistic_yee_submission(
			audit_id=YEE_AUDIT_MARKET_HIGH_WEIGHT_ID,
			submission_id=YEE_SUBMISSION_MARKET_HIGH_WEIGHT_ID,
			project_id=YEE_PROJECT_PILOT_ID,
			auditor_id=YEE_AUDITOR_PROFILE_02_ID,
			auditor_code="AUD002",
			place_id=YEE_PLACE_MARKET_ID,
			place_name="Market Square",
			audit_code="YEE-MARKET-02-2026-03-15",
			started_at=_utc_datetime("2026-03-15T13:00:00Z"),
			submitted_at=_utc_datetime("2026-03-15T13:50:00Z"),
			total_minutes=50,
			domain_levels=market_shared_levels,
			domain_weights=market_weights_b,
			comments="Weighting-pair B: same observations as pair A, but amenities and use weighted highest.",
			weighting_comments={"amenities": "Amenities weighted highest by this auditor."},
			seed_scenario="submitted_weight_sensitivity_b",
		),
		build_realistic_yee_submission(
			audit_id=YEE_AUDIT_MARKET_ZERO_WEIGHT_ID,
			submission_id=YEE_SUBMISSION_MARKET_ZERO_WEIGHT_ID,
			project_id=YEE_PROJECT_PILOT_ID,
			auditor_id=YEE_AUDITOR_PROFILE_03_ID,
			auditor_code="AUD003",
			place_id=YEE_PLACE_MARKET_ID,
			place_name="Market Square",
			audit_code="YEE-MARKET-03-2026-03-16",
			started_at=_utc_datetime("2026-03-16T10:00:00Z"),
			submitted_at=_utc_datetime("2026-03-16T10:44:00Z"),
			total_minutes=44,
			domain_levels=_uniform_domain_levels(0.8),
			domain_weights=zero_weights,
			comments="High raw provision, but no importance weights assigned — weighted total is zero.",
			seed_scenario="submitted_zero_weight_edge",
		),
	]
	audits = [audit for audit, _ in submission_pairs]
	submissions = [submission for _, submission in submission_pairs]
	audits.extend(
		[
			build_realistic_yee_draft(
				audit_id=YEE_AUDIT_COMMONS_IN_PROGRESS_ID,
				project_id=YEE_PROJECT_FOLLOW_UP_ID,
				place_id=YEE_PLACE_COMMONS_ID,
				place_name="North School Commons",
				auditor_id=YEE_AUDITOR_PROFILE_03_ID,
				auditor_code="AUD003",
				audit_code="YEE-COMMONS-03-2026-03-09",
				started_at=_utc_datetime("2026-03-09T09:30:00Z"),
				total_minutes=20,
				domain_levels=_domain_levels(access=0.5, activitySpaces=0.4),
				include_domains={"access", "activitySpaces"},
				domain_weights=medium_weights,
				comments="Paused after the first walkthrough because indoor access was delayed.",
				section_comments={"Activity Spaces": "Outdoor play area partially observed."},
				seed_scenario="draft_partial",
			),
			build_realistic_yee_draft(
				audit_id=YEE_AUDIT_PLAZA_IN_PROGRESS_ID,
				project_id=YEE_PROJECT_CORE_ID,
				place_id=YEE_PLACE_PLAZA_ID,
				place_name="South Transit Plaza",
				auditor_id=YEE_AUDITOR_PROFILE_01_ID,
				auditor_code="AUD001",
				audit_code="YEE-PLAZA-01-2026-03-05",
				started_at=_utc_datetime("2026-03-05T13:35:00Z"),
				total_minutes=41,
				domain_levels=_domain_levels(
					access=0.8,
					activitySpaces=0.7,
					amenities=0.6,
					experienceOfSpace=0.65,
					aestheticsAndCare=0.7,
				),
				include_domains={
					"access",
					"activitySpaces",
					"amenities",
					"experienceOfSpace",
					"aestheticsAndCare",
				},
				domain_weights=plaza_weights,
				comments="Mostly complete draft saved before final youth-count review.",
				section_comments={"Access": "Crosswalk timing needs a second look."},
				weighting_comments={"activitySpaces": "Auditor marked active use as a high-priority domain."},
				seed_scenario="draft_near_complete",
			),
			build_realistic_yee_draft(
				audit_id=YEE_AUDIT_GARDEN_IN_PROGRESS_ID,
				project_id=YEE_PROJECT_PILOT_ID,
				place_id=YEE_PLACE_GARDEN_ID,
				place_name="Harbor Community Garden",
				auditor_id=YEE_AUDITOR_PROFILE_02_ID,
				auditor_code="AUD002",
				audit_code="YEE-GARDEN-02-2026-03-17",
				started_at=_utc_datetime("2026-03-17T09:15:00Z"),
				total_minutes=8,
				domain_levels=_domain_levels(access=0.6),
				include_domains={"access"},
				domain_weights=medium_weights,
				comments="Just started — only the access section was filled in before pausing.",
				seed_scenario="draft_fresh_minimal",
			),
		]
	)

	return [
		canonical_instrument,
		*users,
		manager_account,
		*manager_profiles,
		*auditor_profiles,
		*projects,
		*places,
		*project_places,
		*auditor_invites,
		*manager_invites,
		*assignments,
		*audits,
		*submissions,
		*_build_generated_yee_accounts(),
	]


async def _seed_product(
	product: ProductKey,
	environment: DatabaseEnvironment,
	*,
	skip_migrate: bool = False,
	reset: bool = True,
) -> dict[str, int]:
	"""Clear and repopulate one product database on the given tier."""

	if not skip_migrate:
		await _upgrade_product_database(product, environment)
	engine = _build_seed_engine(product, environment)
	session_factory = async_sessionmaker(engine, autoflush=False, expire_on_commit=False)
	entities = _build_playspace_entities() if product is ProductKey.PLAYSPACE else _build_yee_entities()

	try:
		async with session_factory() as session:
			if reset:
				await _clear_product_tables(session, product)
				await _insert_seed_entities(session, entities)
			else:
				await _upsert_seed_entities(session, entities)
			await session.commit()
	finally:
		await engine.dispose()

	audit_count = sum(1 for entity in entities if isinstance(entity, Audit))
	project_count = sum(1 for entity in entities if isinstance(entity, Project))
	place_count = sum(1 for entity in entities if isinstance(entity, Place))
	auditor_count = sum(1 for entity in entities if isinstance(entity, AuditorProfile))
	return {
		"projects": project_count,
		"places": place_count,
		"auditors": auditor_count,
		"audits": audit_count,
	}


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
	"""Parse command line options for the seeding entry point."""

	parser = argparse.ArgumentParser(description="Seed deterministic shared-core demo data.")
	parser.add_argument(
		"--product",
		choices=["all", ProductKey.YEE.value, ProductKey.PLAYSPACE.value],
		default="all",
		help="Seed one product database or both.",
	)
	parser.add_argument(
		"--environment",
		"-e",
		default="test",
		help=(
			"Target database tier: test, development, or production (aliases: dev, prod, local). "
			"Defaults to test so a bare run never touches dev or prod."
		),
	)
	parser.add_argument(
		"--skip-migrate",
		action="store_true",
		default=False,
		help="Skip the Alembic upgrade step (use when the schema is already current).",
	)
	parser.add_argument(
		"--reset",
		action=argparse.BooleanOptionalAction,
		default=True,
		help=(
			"Whether to clear the target product database before seeding. "
			"Defaults to true; pass --no-reset to seed on top of existing rows."
		),
	)
	parser.add_argument(
		"--allow-destructive",
		action="store_true",
		default=False,
		help="Acknowledge that seeding deletes existing product data before re-inserting demo records.",
	)
	return parser.parse_args(argv)


def _require_destructive_confirmation(
	products: list[ProductKey],
	environment: DatabaseEnvironment,
	*,
	allow_destructive: bool,
	reset: bool,
) -> None:
	"""Block destructive seeding unless the caller opted in explicitly.

	The target tier and host/database are named so the operator can confirm they
	are wiping the intended database before passing ``--allow-destructive``.
	"""

	if allow_destructive:
		return

	if not reset:
		return

	targets = []
	for product in products:
		database_url = resolve_raw_database_url(product, environment)
		database_target = describe_database_target(database_url)
		targets.append(f"{product.value} [{environment.value}] ({database_target})")

	raise SystemExit(
		"Refusing to seed because this command deletes existing data. "
		f"Targets: {targets}. Re-run with --allow-destructive if you intend to reset those databases."
	)


async def _run() -> None:
	"""Execute the seed flow for the selected product databases."""

	args = _parse_args()
	try:
		environment = parse_database_environment(args.environment)
	except ValueError as error:
		raise SystemExit(str(error)) from error
	products = [ProductKey.YEE, ProductKey.PLAYSPACE] if args.product == "all" else [ProductKey(args.product)]
	_require_destructive_confirmation(
		products,
		environment,
		allow_destructive=args.allow_destructive,
		reset=args.reset,
	)
	print(f"Seeding tier: {environment.value} (reset={'yes' if args.reset else 'no'})")
	for product in products:
		summary = await _seed_product(
			product,
			environment,
			skip_migrate=args.skip_migrate,
			reset=args.reset,
		)
		print(
			f"Seeded {product.value} [{environment.value}]: "
			f"{summary['projects']} projects, "
			f"{summary['places']} places, "
			f"{summary['auditors']} auditors, "
			f"{summary['audits']} audits",
		)


if __name__ == "__main__":
	asyncio.run(_run())

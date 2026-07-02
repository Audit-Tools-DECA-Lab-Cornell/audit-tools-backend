"""
Seed shared-core data into the YEE and Playspace databases.

Playspace data is generated from the live scoring metadata so assignments,
responses, draft progress, and submitted scores remain internally consistent.
YEE continues to use lighter placeholder audit shells until its dedicated
execution flow is implemented.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import cast
from urllib.parse import urlparse
from alembic.config import Config
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from alembic import command
from app.auth_security import hash_password
from app.core.demo_data import DEMO_ACCOUNT_ID
from app.core.source_materials import build_yee_source_metadata
from app.database import ASYNC_SESSION_FACTORY_BY_PRODUCT, ProductKey, get_database_url
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
from app.products.yee.services.scoring_spec import SCORING_VERSION
from app.yee_scoring import TOTAL_CATEGORY_NAME, get_yee_instrument_data, score_yee_responses

REPO_ROOT = Path(__file__).resolve().parents[1]

YEE_ORGANIZATION_NAME = "Youth Enabling Environments Collaborative"

UNITED_STATES = "United States"
NEW_YORK = "New York"

YEE_SECTION_AESTHETICS_AND_CARE = "Aesthetics & Care"
YEE_SECTION_USE_AND_USABILITY = "Use & Usability"

YEE_MANAGER_PROFILE_PRIMARY_ID = uuid.UUID("77777777-7777-4777-8777-777777777771")
YEE_MANAGER_PROFILE_SECONDARY_ID = uuid.UUID("77777777-7777-4777-8777-777777777772")

YEE_PROJECT_CORE_ID = uuid.UUID("88888888-8888-4888-8888-888888888881")
YEE_PROJECT_FOLLOW_UP_ID = uuid.UUID("88888888-8888-4888-8888-888888888882")

YEE_PLACE_HUB_ID = uuid.UUID("99999999-9999-4999-8999-999999999991")
YEE_PLACE_PLAZA_ID = uuid.UUID("99999999-9999-4999-8999-999999999992")
YEE_PLACE_LIBRARY_ID = uuid.UUID("99999999-9999-4999-8999-999999999993")
YEE_PLACE_COMMONS_ID = uuid.UUID("99999999-9999-4999-8999-999999999994")
# An assigned-but-unaudited place auditor 1 still has to visit. Kept free of any
# seeded audit/submission so the submit-flow durability test has a clean slot.
YEE_PLACE_GREEN_ID = uuid.UUID("99999999-9999-4999-8999-999999999995")

YEE_AUDITOR_PROFILE_01_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1")
YEE_AUDITOR_PROFILE_02_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2")
YEE_AUDITOR_PROFILE_03_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3")

YEE_AUDITOR_ACCOUNT_01_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc1")
YEE_AUDITOR_ACCOUNT_02_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc2")
YEE_AUDITOR_ACCOUNT_03_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc3")

YEE_INSTRUMENT_ID = uuid.UUID("eeeeeeee-eeee-4eee-8eee-eeeeeeeeeee1")

YEE_AUDIT_HUB_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1")
YEE_AUDIT_PLAZA_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2")
YEE_AUDIT_LIBRARY_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb3")
YEE_AUDIT_COMMONS_IN_PROGRESS_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb4")

YEE_SUBMISSION_HUB_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc1")
YEE_SUBMISSION_PLAZA_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc2")
YEE_SUBMISSION_LIBRARY_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc3")

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


def _placeholder_password_hash(label: str) -> str:
	"""Generate a stable placeholder password hash for demo seed records."""

	return f"seed::{label}"


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
		BugReport,
		KnownIssue,
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


def _run_product_upgrade(product: ProductKey) -> None:
	"""Run Alembic for one product in a synchronous context."""

	alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
	alembic_config.cmd_opts = argparse.Namespace(x=[f"product={product.value}"])
	# Each product has its own Alembic branch head (label == product value), so the
	# generic "head" is ambiguous; target the product-scoped branch head explicitly.
	command.upgrade(alembic_config, f"{product.value}@head")


async def _upgrade_product_database(product: ProductKey) -> None:
	"""Ensure the selected product database schema exists before seeding."""

	await asyncio.to_thread(_run_product_upgrade, product)


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


def _build_playspace_entities() -> list[object]:
	"""Create deterministic Playspace ORM objects for seeding."""

	return list(build_playspace_seed_entities())


def _build_yee_submission_responses(quality: float) -> dict[str, dict[str, str]]:
	"""Build a deterministic instrument-valid YEE response set.

	For each matrix scoring item, pick — per choice — the answer whose
	total-category score sits at the requested ``quality`` percentile (1.0 = best
	answer, lower = a more middling answer). This yields realistic, reproducible
	responses that the real scorer grades, so seeded submissions carry sensible
	section scores instead of placeholder data.
	"""

	instrument = get_yee_instrument_data()
	scoring_items: list[dict[str, object]] = instrument["scoring_items"]  # type: ignore[assignment]
	category_names_by_id: dict[str, str] = instrument["scoring_categories"]  # type: ignore[assignment]

	def _total_for_row(row: dict[str, object]) -> int:
		score_map = row.get("scores_by_category_id", {})
		if not isinstance(score_map, dict):
			return 0
		return sum(
			int(value)
			for category_id, value in score_map.items()
			if category_names_by_id.get(str(category_id)) == TOTAL_CATEGORY_NAME
		)

	responses: dict[str, dict[str, str]] = {}
	for item in scoring_items:
		item_id = str(item["item_id"])
		rows = item.get("score_entries", [])
		if not isinstance(rows, list):
			continue
		rows_by_choice: dict[str, list[dict[str, object]]] = defaultdict(list)
		for row in rows:
			choice_id = row.get("choice_id")
			if isinstance(choice_id, str):
				rows_by_choice[choice_id].append(row)
		answers: dict[str, str] = {}
		for choice_id, choice_rows in rows_by_choice.items():
			# Rank whole answers, not individual category rows: an answer can span
			# several grading rows, so score each answer by the sum of its rows'
			# total-category contribution before taking the quality percentile.
			rows_by_answer: dict[str, list[dict[str, object]]] = defaultdict(list)
			for row in choice_rows:
				answer_id = row.get("answer_id")
				if isinstance(answer_id, str):
					rows_by_answer[answer_id].append(row)
			if not rows_by_answer:
				continue
			ranked = sorted(
				rows_by_answer.items(),
				key=lambda answer_rows: sum(_total_for_row(row) for row in answer_rows[1]),
			)
			answers[choice_id] = ranked[min(len(ranked) - 1, int((len(ranked) - 1) * quality))][0]
		if answers:
			responses[item_id] = answers
	return responses


def _build_yee_submission(
	*,
	submission_id: uuid.UUID,
	auditor_id: uuid.UUID,
	auditor_code: str,
	place_id: uuid.UUID,
	place_name: str,
	submitted_at: datetime,
	total_minutes: int,
	quality: float,
) -> YeeAuditSubmission:
	"""Assemble one scored YEE submission row matching a submitted seed audit."""

	responses = _build_yee_submission_responses(quality)
	participant_info = {
		"auditor_id": auditor_code,
		"place_id": str(place_id),
		"place_name": place_name,
		"audit_date": submitted_at.date().isoformat(),
		"start_time": submitted_at.strftime("%H:%M"),
		"finish_time": (submitted_at + timedelta(minutes=total_minutes)).strftime("%H:%M"),
		"total_minutes": total_minutes,
		"visit_frequency": "Weekly",
		"season": "Spring",
		"weather": "Clear",
		"domain_weights": dict(YEE_SEED_DOMAIN_WEIGHTS),
		"comments": "Seeded demo submission.",
		"section_comments": {},
	}
	score = score_yee_responses(cast(dict[str, object], responses), participant_info)
	return YeeAuditSubmission(
		id=submission_id,
		auditor_id=auditor_id,
		place_id=place_id,
		submitted_at=submitted_at,
		participant_info_json=participant_info,
		responses_json=responses,
		section_scores_json=score["section_scores"],
		scores_json=score["canonical_score"],
		scoring_version=SCORING_VERSION,
		total_score=int(cast(int, score["total_score"])),
	)


def _build_yee_entities() -> list[object]:
	"""Create deterministic YEE ORM objects for seeding."""

	instrument_metadata = build_yee_source_metadata()
	yee_instrument_content = get_yee_instrument_data()

	# Source-of-truth instrument row so the YEE database mirrors Playspace: the
	# active instrument lives in the `instruments` table, and audits stamp the
	# matching (instrument_key, instrument_version) at creation time.
	canonical_instrument = Instrument(
		id=YEE_INSTRUMENT_ID,
		instrument_key="yee",
		instrument_version=str(yee_instrument_content.get("version", "1")),
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
			est_auditors=2,
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
	]
	project_places = [
		ProjectPlace(project_id=YEE_PROJECT_CORE_ID, place_id=YEE_PLACE_HUB_ID),
		ProjectPlace(project_id=YEE_PROJECT_CORE_ID, place_id=YEE_PLACE_PLAZA_ID),
		ProjectPlace(project_id=YEE_PROJECT_CORE_ID, place_id=YEE_PLACE_GREEN_ID),
		ProjectPlace(project_id=YEE_PROJECT_FOLLOW_UP_ID, place_id=YEE_PLACE_LIBRARY_ID),
		ProjectPlace(project_id=YEE_PROJECT_FOLLOW_UP_ID, place_id=YEE_PLACE_COMMONS_ID),
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
	]

	audits = [
		Audit(
			id=YEE_AUDIT_HUB_ID,
			project_id=YEE_PROJECT_CORE_ID,
			place_id=YEE_PLACE_HUB_ID,
			auditor_profile_id=YEE_AUDITOR_PROFILE_01_ID,
			audit_code="YEE-HUB-01-2026-03-02",
			instrument_key=str(instrument_metadata["instrument_key"]),
			instrument_version=str(instrument_metadata["instrument_version"]),
			status=AuditStatus.SUBMITTED,
			started_at=_utc_datetime("2026-03-02T13:00:00Z"),
			submitted_at=_utc_datetime("2026-03-02T14:05:00Z"),
			total_minutes=65,
			summary_score=78.0,
			responses_json={
				"seed_source": instrument_metadata,
				"scoring_mode": "presence_x_condition",
				"site_focus": "access, transit proximity, and youth comfort",
			},
			scores_json={
				"summary_score": 78.0,
				"section_scores": {
					"Access": 81.0,
					"Activity": 74.0,
					"Amenities": 80.0,
					"Experience": 79.0,
					YEE_SECTION_AESTHETICS_AND_CARE: 76.0,
					YEE_SECTION_USE_AND_USABILITY: 77.0,
				},
			},
			created_at=_utc_datetime("2026-03-02T13:00:00Z"),
			updated_at=_utc_datetime("2026-03-02T14:05:00Z"),
		),
		Audit(
			id=YEE_AUDIT_PLAZA_ID,
			project_id=YEE_PROJECT_CORE_ID,
			place_id=YEE_PLACE_PLAZA_ID,
			auditor_profile_id=YEE_AUDITOR_PROFILE_02_ID,
			audit_code="YEE-PLAZA-02-2026-03-03",
			instrument_key=str(instrument_metadata["instrument_key"]),
			instrument_version=str(instrument_metadata["instrument_version"]),
			status=AuditStatus.SUBMITTED,
			started_at=_utc_datetime("2026-03-03T10:15:00Z"),
			submitted_at=_utc_datetime("2026-03-03T11:10:00Z"),
			total_minutes=55,
			summary_score=84.0,
			responses_json={
				"seed_source": instrument_metadata,
				"scoring_mode": "presence_x_condition",
				"site_focus": "public transport and surrounding-area activation",
			},
			scores_json={
				"summary_score": 84.0,
				"section_scores": {
					"Access": 86.0,
					"Activity": 82.0,
					"Amenities": 83.0,
					"Experience": 85.0,
					YEE_SECTION_AESTHETICS_AND_CARE: 81.0,
					YEE_SECTION_USE_AND_USABILITY: 87.0,
				},
			},
			created_at=_utc_datetime("2026-03-03T10:15:00Z"),
			updated_at=_utc_datetime("2026-03-03T11:10:00Z"),
		),
		Audit(
			id=YEE_AUDIT_LIBRARY_ID,
			project_id=YEE_PROJECT_FOLLOW_UP_ID,
			place_id=YEE_PLACE_LIBRARY_ID,
			auditor_profile_id=YEE_AUDITOR_PROFILE_03_ID,
			audit_code="YEE-LIBRARY-03-2026-03-07",
			instrument_key=str(instrument_metadata["instrument_key"]),
			instrument_version=str(instrument_metadata["instrument_version"]),
			status=AuditStatus.SUBMITTED,
			started_at=_utc_datetime("2026-03-07T12:00:00Z"),
			submitted_at=_utc_datetime("2026-03-07T12:50:00Z"),
			total_minutes=50,
			summary_score=73.0,
			responses_json={
				"seed_source": instrument_metadata,
				"scoring_mode": "presence_x_condition",
				"site_focus": "experience of space and perceived safety near youth services",
			},
			scores_json={
				"summary_score": 73.0,
				"section_scores": {
					"Access": 70.0,
					"Activity": 72.0,
					"Amenities": 74.0,
					"Experience": 75.0,
					YEE_SECTION_AESTHETICS_AND_CARE: 71.0,
					YEE_SECTION_USE_AND_USABILITY: 76.0,
				},
			},
			created_at=_utc_datetime("2026-03-07T12:00:00Z"),
			updated_at=_utc_datetime("2026-03-07T12:50:00Z"),
		),
		Audit(
			id=YEE_AUDIT_COMMONS_IN_PROGRESS_ID,
			project_id=YEE_PROJECT_FOLLOW_UP_ID,
			place_id=YEE_PLACE_COMMONS_ID,
			auditor_profile_id=YEE_AUDITOR_PROFILE_03_ID,
			audit_code="YEE-COMMONS-03-2026-03-09",
			instrument_key=str(instrument_metadata["instrument_key"]),
			instrument_version=str(instrument_metadata["instrument_version"]),
			status=AuditStatus.IN_PROGRESS,
			started_at=_utc_datetime("2026-03-09T09:30:00Z"),
			submitted_at=None,
			total_minutes=20,
			summary_score=None,
			responses_json={
				"seed_source": instrument_metadata,
				"scoring_mode": "presence_x_condition",
				"draft_state": "observer paused before final scoring review",
			},
			scores_json={"draft_progress_percent": 35},
			created_at=_utc_datetime("2026-03-09T09:30:00Z"),
			updated_at=_utc_datetime("2026-03-09T09:50:00Z"),
		),
	]

	# Submitted audits must have a matching YeeAuditSubmission: the auditor's own
	# dashboard, the per-place audit state, and manager reporting all read from
	# yee_audit_submissions, so a SUBMITTED Audit without one renders as
	# "not started" and disappears from reporting.
	submissions = [
		_build_yee_submission(
			submission_id=YEE_SUBMISSION_HUB_ID,
			auditor_id=YEE_AUDITOR_PROFILE_01_ID,
			auditor_code="AUD001",
			place_id=YEE_PLACE_HUB_ID,
			place_name="Westside Youth Hub",
			submitted_at=_utc_datetime("2026-03-02T14:05:00Z"),
			total_minutes=65,
			quality=1.0,
		),
		_build_yee_submission(
			submission_id=YEE_SUBMISSION_PLAZA_ID,
			auditor_id=YEE_AUDITOR_PROFILE_02_ID,
			auditor_code="AUD002",
			place_id=YEE_PLACE_PLAZA_ID,
			place_name="South Transit Plaza",
			submitted_at=_utc_datetime("2026-03-03T11:10:00Z"),
			total_minutes=55,
			quality=1.0,
		),
		_build_yee_submission(
			submission_id=YEE_SUBMISSION_LIBRARY_ID,
			auditor_id=YEE_AUDITOR_PROFILE_03_ID,
			auditor_code="AUD003",
			place_id=YEE_PLACE_LIBRARY_ID,
			place_name="Maple Library Plaza",
			submitted_at=_utc_datetime("2026-03-07T12:50:00Z"),
			total_minutes=50,
			quality=0.7,
		),
	]

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
		*assignments,
		*audits,
		*submissions,
	]


async def _seed_product(product: ProductKey, *, skip_migrate: bool = False) -> dict[str, int]:
	"""Clear and repopulate one product database."""

	if not skip_migrate:
		await _upgrade_product_database(product)
	session_factory = ASYNC_SESSION_FACTORY_BY_PRODUCT[product]
	entities = _build_playspace_entities() if product is ProductKey.PLAYSPACE else _build_yee_entities()

	async with session_factory() as session:
		await _clear_product_tables(session, product)
		await _insert_seed_entities(session, entities)
		await session.commit()

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


def _parse_args() -> argparse.Namespace:
	"""Parse command line options for the seeding entry point."""

	parser = argparse.ArgumentParser(description="Seed deterministic shared-core demo data.")
	parser.add_argument(
		"--product",
		choices=["all", ProductKey.YEE.value, ProductKey.PLAYSPACE.value],
		default="all",
		help="Seed one product database or both.",
	)
	parser.add_argument(
		"--skip-migrate",
		action="store_true",
		default=False,
		help="Skip the Alembic upgrade step (use when the schema is already current).",
	)
	parser.add_argument(
		"--allow-destructive",
		action="store_true",
		default=False,
		help="Acknowledge that seeding deletes existing product data before re-inserting demo records.",
	)
	return parser.parse_args()


def _host_for_database_url(raw_url: str) -> str:
	"""Extract one database hostname from a SQLAlchemy-style URL."""

	normalized = raw_url.replace("postgresql+asyncpg://", "postgresql://", 1)
	normalized = normalized.replace("postgres://", "postgresql://", 1)
	return urlparse(normalized).hostname or ""


def _require_destructive_confirmation(products: list[ProductKey], *, allow_destructive: bool) -> None:
	"""Block destructive seeding unless the caller opted in explicitly."""

	if allow_destructive:
		return

	targets = ", ".join(
		f"{product.value} ({_host_for_database_url(get_database_url(product)) or 'unknown-host'})"
		for product in products
	)
	raise SystemExit(
		"Refusing to seed because this command deletes existing data. "
		f"Targets: {targets}. Re-run with --allow-destructive if you intend to reset those databases."
	)


async def _run() -> None:
	"""Execute the seed flow for the selected product databases."""

	args = _parse_args()
	products = [ProductKey.YEE, ProductKey.PLAYSPACE] if args.product == "all" else [ProductKey(args.product)]
	_require_destructive_confirmation(products, allow_destructive=args.allow_destructive)
	for product in products:
		summary = await _seed_product(product, skip_migrate=args.skip_migrate)
		print(
			f"Seeded {product.value}: "
			f"{summary['projects']} projects, "
			f"{summary['places']} places, "
			f"{summary['auditors']} auditors, "
			f"{summary['audits']} audits",
		)


if __name__ == "__main__":
	asyncio.run(_run())

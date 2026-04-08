"""
Seed deterministic shared-core data into the YEE and Playspace databases.

The seeded audit payloads intentionally embed metadata extracted from the
provided source documents so those files are already useful before the final
product-specific scoring engines land.
"""

from __future__ import annotations

import argparse
import asyncio
import uuid
from datetime import date, datetime, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.demo_data import (
    DEMO_ACCOUNT_ID,
    DEMO_AUDIT_KEPLER_ID,
    DEMO_AUDIT_MATAI_ID,
    DEMO_AUDIT_RIVERSIDE_ID,
    DEMO_AUDITOR_AKL01_ID,
    DEMO_AUDITOR_AKL02_ID,
    DEMO_AUDITOR_CHC01_ID,
    DEMO_MANAGER_PROFILE_PRIMARY_ID,
    DEMO_MANAGER_PROFILE_SECONDARY_ID,
    DEMO_PLACE_HILLCREST_ID,
    DEMO_PLACE_KEPLER_ID,
    DEMO_PLACE_MATAI_ID,
    DEMO_PLACE_RIVERSIDE_ID,
    DEMO_PROJECT_SOUTH_ID,
    DEMO_PROJECT_URBAN_ID,
)
from app.core.source_materials import build_playspace_source_metadata, build_yee_source_metadata
from app.auth_security import hash_password
from app.database import ASYNC_SESSION_FACTORY_BY_PRODUCT, ProductKey
from app.models import (
    Account,
    AccountType,
    Audit,
    AuditorAssignment,
    AuditorProfile,
    AuditStatus,
    ManagerProfile,
    Place,
    Project,
    User,
)

PLAYSPACE_ORGANIZATION_NAME = "Auckland Playspace Collaborative"
YEE_ORGANIZATION_NAME = "Youth Enabling Environments Collaborative"

NEW_ZEALAND = "New Zealand"
UNITED_STATES = "United States"
NEW_YORK = "New York"

PUBLIC_PLAYSPACE = "public playspace"
YEE_SECTION_AESTHETICS_AND_CARE = "Aesthetics & Care"
YEE_SECTION_USE_AND_USABILITY = "Use & Usability"

PLAYSPACE_AUDITOR_ACCOUNT_AKL01_ID = uuid.UUID("66666666-6666-4666-8666-666666666661")
PLAYSPACE_AUDITOR_ACCOUNT_AKL02_ID = uuid.UUID("66666666-6666-4666-8666-666666666662")
PLAYSPACE_AUDITOR_ACCOUNT_CHC01_ID = uuid.UUID("66666666-6666-4666-8666-666666666663")

PLAYSPACE_AUDIT_RIVERSIDE_IN_PROGRESS_ID = uuid.UUID("55555555-5555-4555-8555-555555555554")

YEE_MANAGER_PROFILE_PRIMARY_ID = uuid.UUID("77777777-7777-4777-8777-777777777771")
YEE_MANAGER_PROFILE_SECONDARY_ID = uuid.UUID("77777777-7777-4777-8777-777777777772")

YEE_PROJECT_CORE_ID = uuid.UUID("88888888-8888-4888-8888-888888888881")
YEE_PROJECT_FOLLOW_UP_ID = uuid.UUID("88888888-8888-4888-8888-888888888882")

YEE_PLACE_HUB_ID = uuid.UUID("99999999-9999-4999-8999-999999999991")
YEE_PLACE_PLAZA_ID = uuid.UUID("99999999-9999-4999-8999-999999999992")
YEE_PLACE_LIBRARY_ID = uuid.UUID("99999999-9999-4999-8999-999999999993")
YEE_PLACE_COMMONS_ID = uuid.UUID("99999999-9999-4999-8999-999999999994")

YEE_AUDITOR_PROFILE_01_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa1")
YEE_AUDITOR_PROFILE_02_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa2")
YEE_AUDITOR_PROFILE_03_ID = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaa3")

YEE_AUDITOR_ACCOUNT_01_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc1")
YEE_AUDITOR_ACCOUNT_02_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc2")
YEE_AUDITOR_ACCOUNT_03_ID = uuid.UUID("cccccccc-cccc-4ccc-8ccc-ccccccccccc3")

YEE_AUDIT_HUB_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb1")
YEE_AUDIT_PLAZA_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb2")
YEE_AUDIT_LIBRARY_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb3")
YEE_AUDIT_COMMONS_IN_PROGRESS_ID = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbb4")


def _utc_datetime(value: str) -> datetime:
    """Convert an ISO-ish timestamp string into a timezone-aware UTC datetime."""

    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def _placeholder_password_hash(label: str) -> str:
    """Generate a stable placeholder password hash for demo seed records."""

    return f"seed::{label}"


def _demo_password_hash() -> str:
    """Return the shared demo login password hash used for seeded auth users."""

    return hash_password("DemoPass123!")


async def _clear_shared_tables(session: AsyncSession) -> None:
    """Remove existing shared-core records before inserting fresh deterministic data."""

    for model in (
        Audit,
        AuditorAssignment,
        Place,
        Project,
        ManagerProfile,
        AuditorProfile,
        Account,
        User,
    ):
        await session.execute(delete(model))


def _build_playspace_entities() -> list[object]:
    """Create deterministic Playspace ORM objects for seeding."""

    instrument_metadata = build_playspace_source_metadata()

    manager_account = Account(
        id=DEMO_ACCOUNT_ID,
        name=PLAYSPACE_ORGANIZATION_NAME,
        email="manager@example.org",
        password_hash=_placeholder_password_hash("playspace-manager"),
        account_type=AccountType.MANAGER,
        created_at=_utc_datetime("2026-01-10T08:30:00Z"),
    )

    manager_profiles = [
        ManagerProfile(
            id=DEMO_MANAGER_PROFILE_PRIMARY_ID,
            account_id=DEMO_ACCOUNT_ID,
            full_name="Dr. Amelia Carter",
            email="amelia.carter@example.org",
            phone="+64 21 555 0141",
            position="Primary Manager",
            organization=PLAYSPACE_ORGANIZATION_NAME,
            is_primary=True,
            created_at=_utc_datetime("2026-01-10T09:00:00Z"),
        ),
        ManagerProfile(
            id=DEMO_MANAGER_PROFILE_SECONDARY_ID,
            account_id=DEMO_ACCOUNT_ID,
            full_name="Noah Bennett",
            email="noah.bennett@example.org",
            phone=None,
            position="Project Coordinator",
            organization=PLAYSPACE_ORGANIZATION_NAME,
            is_primary=False,
            created_at=_utc_datetime("2026-01-12T09:30:00Z"),
        ),
    ]

    auditor_accounts = [
        Account(
            id=PLAYSPACE_AUDITOR_ACCOUNT_AKL01_ID,
            name="Ariana Ngata",
            email="ariana.ngata@example.org",
            password_hash=_placeholder_password_hash("playspace-auditor-akl01"),
            account_type=AccountType.AUDITOR,
            created_at=_utc_datetime("2026-02-01T09:00:00Z"),
        ),
        Account(
            id=PLAYSPACE_AUDITOR_ACCOUNT_AKL02_ID,
            name="Luca Patel",
            email="luca.patel@example.org",
            password_hash=_placeholder_password_hash("playspace-auditor-akl02"),
            account_type=AccountType.AUDITOR,
            created_at=_utc_datetime("2026-02-01T09:05:00Z"),
        ),
        Account(
            id=PLAYSPACE_AUDITOR_ACCOUNT_CHC01_ID,
            name="Maya Thompson",
            email="maya.thompson@example.org",
            password_hash=_placeholder_password_hash("playspace-auditor-chc01"),
            account_type=AccountType.AUDITOR,
            created_at=_utc_datetime("2026-02-02T09:00:00Z"),
        ),
    ]

    auditor_profiles = [
        AuditorProfile(
            id=DEMO_AUDITOR_AKL01_ID,
            account_id=PLAYSPACE_AUDITOR_ACCOUNT_AKL01_ID,
            auditor_code="AKL-01",
            email="ariana.ngata@example.org",
            full_name="Ariana Ngata",
            age_range="18-24",
            gender="Woman",
            country=NEW_ZEALAND,
            role="student",
            created_at=_utc_datetime("2026-02-01T09:15:00Z"),
        ),
        AuditorProfile(
            id=DEMO_AUDITOR_AKL02_ID,
            account_id=PLAYSPACE_AUDITOR_ACCOUNT_AKL02_ID,
            auditor_code="AKL-02",
            email="luca.patel@example.org",
            full_name="Luca Patel",
            age_range="25-34",
            gender="Man",
            country=NEW_ZEALAND,
            role="facilitator",
            created_at=_utc_datetime("2026-02-01T09:20:00Z"),
        ),
        AuditorProfile(
            id=DEMO_AUDITOR_CHC01_ID,
            account_id=PLAYSPACE_AUDITOR_ACCOUNT_CHC01_ID,
            auditor_code="CHC-01",
            email="maya.thompson@example.org",
            full_name="Maya Thompson",
            age_range="18-24",
            gender="Woman",
            country=NEW_ZEALAND,
            role="teacher",
            created_at=_utc_datetime("2026-02-02T09:20:00Z"),
        ),
    ]

    projects = [
        Project(
            id=DEMO_PROJECT_URBAN_ID,
            account_id=DEMO_ACCOUNT_ID,
            name="Urban Playspace Usability 2026",
            overview=(
                "A citywide review of urban playspaces focused on access, comfort, and play value."
            ),
            place_types=[PUBLIC_PLAYSPACE, "school playspace"],
            start_date=date(2026, 2, 1),
            end_date=date(2026, 6, 30),
            est_places=12,
            est_auditors=4,
            auditor_description="Mixed student and facilitator teams working in pairs.",
            created_at=_utc_datetime("2026-01-20T10:00:00Z"),
        ),
        Project(
            id=DEMO_PROJECT_SOUTH_ID,
            account_id=DEMO_ACCOUNT_ID,
            name="South Region Play Value Pilot",
            overview="A pilot exploring play value and usability patterns across suburban sites.",
            place_types=[PUBLIC_PLAYSPACE, "preschool playspace"],
            start_date=date(2026, 3, 1),
            end_date=date(2026, 7, 15),
            est_places=8,
            est_auditors=3,
            auditor_description="Small multidisciplinary teams completing comparison audits.",
            created_at=_utc_datetime("2026-02-02T12:00:00Z"),
        ),
    ]

    places = [
        Place(
            id=DEMO_PLACE_RIVERSIDE_ID,
            project_id=DEMO_PROJECT_URBAN_ID,
            name="Riverside Community Playground",
            city="Auckland",
            province="Auckland",
            country=NEW_ZEALAND,
            place_type=PUBLIC_PLAYSPACE,
            lat=-36.8485,
            lng=174.7633,
            start_date=date(2026, 2, 10),
            end_date=date(2026, 5, 15),
            est_auditors=2,
            auditor_description="Pair audit with accessibility and maintenance notes.",
            created_at=_utc_datetime("2026-02-03T09:00:00Z"),
        ),
        Place(
            id=DEMO_PLACE_KEPLER_ID,
            project_id=DEMO_PROJECT_URBAN_ID,
            name="Kepler Family Park",
            city="Auckland",
            province="Auckland",
            country=NEW_ZEALAND,
            place_type="school playspace",
            lat=-36.8618,
            lng=174.7706,
            start_date=date(2026, 2, 12),
            end_date=date(2026, 5, 30),
            est_auditors=1,
            auditor_description="Single-site validation audit for school users.",
            created_at=_utc_datetime("2026-02-03T09:10:00Z"),
        ),
        Place(
            id=DEMO_PLACE_HILLCREST_ID,
            project_id=DEMO_PROJECT_SOUTH_ID,
            name="Hillcrest Shared Play Space",
            city="Christchurch",
            province="Canterbury",
            country=NEW_ZEALAND,
            place_type="preschool playspace",
            lat=-43.5321,
            lng=172.6362,
            start_date=date(2026, 3, 5),
            end_date=date(2026, 6, 20),
            est_auditors=2,
            auditor_description="Upcoming comparison site for early years play patterns.",
            created_at=_utc_datetime("2026-03-01T08:45:00Z"),
        ),
        Place(
            id=DEMO_PLACE_MATAI_ID,
            project_id=DEMO_PROJECT_SOUTH_ID,
            name="Matai Neighborhood Play Area",
            city="Christchurch",
            province="Canterbury",
            country=NEW_ZEALAND,
            place_type=PUBLIC_PLAYSPACE,
            lat=-43.5403,
            lng=172.6292,
            start_date=date(2026, 3, 4),
            end_date=date(2026, 6, 25),
            est_auditors=2,
            auditor_description="Neighbourhood pilot with emphasis on community usability.",
            created_at=_utc_datetime("2026-03-01T09:00:00Z"),
        ),
    ]

    assignments = [
        AuditorAssignment(
            id=uuid.UUID("d1000000-0000-4000-8000-000000000001"),
            auditor_profile_id=DEMO_AUDITOR_AKL01_ID,
            project_id=DEMO_PROJECT_URBAN_ID,
            place_id=None,
            assigned_at=_utc_datetime("2026-02-10T08:00:00Z"),
        ),
        AuditorAssignment(
            id=uuid.UUID("d1000000-0000-4000-8000-000000000002"),
            auditor_profile_id=DEMO_AUDITOR_AKL01_ID,
            project_id=None,
            place_id=DEMO_PLACE_RIVERSIDE_ID,
            assigned_at=_utc_datetime("2026-02-12T08:30:00Z"),
        ),
        AuditorAssignment(
            id=uuid.UUID("d1000000-0000-4000-8000-000000000003"),
            auditor_profile_id=DEMO_AUDITOR_AKL02_ID,
            project_id=DEMO_PROJECT_URBAN_ID,
            place_id=None,
            assigned_at=_utc_datetime("2026-02-10T08:05:00Z"),
        ),
        AuditorAssignment(
            id=uuid.UUID("d1000000-0000-4000-8000-000000000004"),
            auditor_profile_id=DEMO_AUDITOR_CHC01_ID,
            project_id=DEMO_PROJECT_SOUTH_ID,
            place_id=None,
            assigned_at=_utc_datetime("2026-03-02T09:00:00Z"),
        ),
        AuditorAssignment(
            id=uuid.UUID("d1000000-0000-4000-8000-000000000005"),
            auditor_profile_id=DEMO_AUDITOR_CHC01_ID,
            project_id=None,
            place_id=DEMO_PLACE_MATAI_ID,
            assigned_at=_utc_datetime("2026-03-03T09:10:00Z"),
        ),
    ]

    audits = [
        Audit(
            id=DEMO_AUDIT_RIVERSIDE_ID,
            place_id=DEMO_PLACE_RIVERSIDE_ID,
            auditor_profile_id=DEMO_AUDITOR_AKL01_ID,
            audit_code="RIVERSIDE-AKL01-2026-03-04",
            instrument_key=str(instrument_metadata["instrument_key"]),
            instrument_version=str(instrument_metadata["instrument_version"]),
            status=AuditStatus.SUBMITTED,
            started_at=_utc_datetime("2026-03-04T16:00:00Z"),
            submitted_at=_utc_datetime("2026-03-04T17:05:00Z"),
            total_minutes=65,
            summary_score=74.0,
            responses_json={
                "seed_source": instrument_metadata,
                "site_focus": "accessibility and inclusive transitions",
                "notes": [
                    "Used PVUA 5.2 audit shell with usability-focused prompts.",
                    "Sample responses retained as lightweight scaffolding only.",
                ],
            },
            scores_json={
                "summary_score": 74.0,
                "subscores": {
                    "usability": 72.0,
                    "play_value": 76.0,
                },
            },
            created_at=_utc_datetime("2026-03-04T16:00:00Z"),
            updated_at=_utc_datetime("2026-03-04T17:05:00Z"),
        ),
        Audit(
            id=PLAYSPACE_AUDIT_RIVERSIDE_IN_PROGRESS_ID,
            place_id=DEMO_PLACE_RIVERSIDE_ID,
            auditor_profile_id=DEMO_AUDITOR_AKL02_ID,
            audit_code="RIVERSIDE-AKL02-2026-03-08",
            instrument_key=str(instrument_metadata["instrument_key"]),
            instrument_version=str(instrument_metadata["instrument_version"]),
            status=AuditStatus.IN_PROGRESS,
            started_at=_utc_datetime("2026-03-08T09:15:00Z"),
            submitted_at=None,
            total_minutes=25,
            summary_score=None,
            responses_json={
                "seed_source": instrument_metadata,
                "draft_state": "partial accessibility walkthrough",
            },
            scores_json={"draft_progress_percent": 40},
            created_at=_utc_datetime("2026-03-08T09:15:00Z"),
            updated_at=_utc_datetime("2026-03-08T09:40:00Z"),
        ),
        Audit(
            id=DEMO_AUDIT_KEPLER_ID,
            place_id=DEMO_PLACE_KEPLER_ID,
            auditor_profile_id=DEMO_AUDITOR_AKL02_ID,
            audit_code="KEPLER-AKL02-2026-03-05",
            instrument_key=str(instrument_metadata["instrument_key"]),
            instrument_version=str(instrument_metadata["instrument_version"]),
            status=AuditStatus.SUBMITTED,
            started_at=_utc_datetime("2026-03-05T12:20:00Z"),
            submitted_at=_utc_datetime("2026-03-05T13:20:00Z"),
            total_minutes=60,
            summary_score=81.0,
            responses_json={
                "seed_source": instrument_metadata,
                "site_focus": "school-age play diversity and challenge",
            },
            scores_json={
                "summary_score": 81.0,
                "subscores": {
                    "usability": 79.0,
                    "play_value": 83.0,
                },
            },
            created_at=_utc_datetime("2026-03-05T12:20:00Z"),
            updated_at=_utc_datetime("2026-03-05T13:20:00Z"),
        ),
        Audit(
            id=DEMO_AUDIT_MATAI_ID,
            place_id=DEMO_PLACE_MATAI_ID,
            auditor_profile_id=DEMO_AUDITOR_CHC01_ID,
            audit_code="MATAI-CHC01-2026-03-06",
            instrument_key=str(instrument_metadata["instrument_key"]),
            instrument_version=str(instrument_metadata["instrument_version"]),
            status=AuditStatus.SUBMITTED,
            started_at=_utc_datetime("2026-03-06T14:40:00Z"),
            submitted_at=_utc_datetime("2026-03-06T15:45:00Z"),
            total_minutes=65,
            summary_score=88.0,
            responses_json={
                "seed_source": instrument_metadata,
                "site_focus": "social play, restorative edges, and inclusive wayfinding",
            },
            scores_json={
                "summary_score": 88.0,
                "subscores": {
                    "usability": 86.0,
                    "play_value": 90.0,
                },
            },
            created_at=_utc_datetime("2026-03-06T14:40:00Z"),
            updated_at=_utc_datetime("2026-03-06T15:45:00Z"),
        ),
    ]

    return [
        manager_account,
        *manager_profiles,
        *auditor_accounts,
        *auditor_profiles,
        *projects,
        *places,
        *assignments,
        *audits,
    ]


def _build_yee_entities() -> list[object]:
    """Create deterministic YEE ORM objects for seeding."""

    instrument_metadata = build_yee_source_metadata()

    manager_account = Account(
        id=DEMO_ACCOUNT_ID,
        name=YEE_ORGANIZATION_NAME,
        email="manager-demo@yee.local",
        password_hash=_demo_password_hash(),
        account_type=AccountType.MANAGER,
        created_at=_utc_datetime("2026-02-20T08:00:00Z"),
    )

    users = [
        User(
            id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd1"),
            email="manager-demo@yee.local",
            password_hash=_demo_password_hash(),
            account_id=DEMO_ACCOUNT_ID,
            account_type=AccountType.MANAGER,
            name="Demo Manager",
            email_verified=True,
            email_verified_at=_utc_datetime("2026-02-20T08:05:00Z"),
            failed_login_attempts=0,
            approved=True,
            approved_at=_utc_datetime("2026-02-20T08:06:00Z"),
            profile_completed=True,
            profile_completed_at=_utc_datetime("2026-02-20T08:07:00Z"),
            created_at=_utc_datetime("2026-02-20T08:00:00Z"),
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
            full_name="Dr. Farah Khan",
            email="farah.khan@example.org",
            phone="+1 607 555 0147",
            position="Principal Investigator",
            organization=YEE_ORGANIZATION_NAME,
            is_primary=True,
            created_at=_utc_datetime("2026-02-20T08:10:00Z"),
        ),
        ManagerProfile(
            id=YEE_MANAGER_PROFILE_SECONDARY_ID,
            account_id=DEMO_ACCOUNT_ID,
            full_name="Jordan Alvarez",
            email="jordan.alvarez@example.org",
            phone=None,
            position="Field Operations Lead",
            organization=YEE_ORGANIZATION_NAME,
            is_primary=False,
            created_at=_utc_datetime("2026-02-20T08:20:00Z"),
        ),
    ]

    auditor_accounts = [
        Account(
            id=YEE_AUDITOR_ACCOUNT_01_ID,
            name="Leah Brown",
            email="leah.brown@example.org",
            password_hash=_placeholder_password_hash("yee-auditor-01"),
            account_type=AccountType.AUDITOR,
            created_at=_utc_datetime("2026-02-22T09:00:00Z"),
        ),
        Account(
            id=YEE_AUDITOR_ACCOUNT_02_ID,
            name="Omar Rivera",
            email="omar.rivera@example.org",
            password_hash=_placeholder_password_hash("yee-auditor-02"),
            account_type=AccountType.AUDITOR,
            created_at=_utc_datetime("2026-02-22T09:05:00Z"),
        ),
        Account(
            id=YEE_AUDITOR_ACCOUNT_03_ID,
            name="Sophie Chen",
            email="sophie.chen@example.org",
            password_hash=_placeholder_password_hash("yee-auditor-03"),
            account_type=AccountType.AUDITOR,
            created_at=_utc_datetime("2026-02-22T09:10:00Z"),
        ),
    ]

    auditor_profiles = [
        AuditorProfile(
            id=YEE_AUDITOR_PROFILE_01_ID,
            account_id=DEMO_ACCOUNT_ID,
            user_id=uuid.UUID("dddddddd-dddd-4ddd-8ddd-ddddddddddd3"),
            auditor_code="YEE-01",
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
            auditor_code="YEE-02",
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
            auditor_code="YEE-03",
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
            project_id=YEE_PROJECT_CORE_ID,
            name="Westside Youth Hub",
            city="Ithaca",
            province=NEW_YORK,
            country=UNITED_STATES,
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
            project_id=YEE_PROJECT_CORE_ID,
            name="South Transit Plaza",
            city="Ithaca",
            province=NEW_YORK,
            country=UNITED_STATES,
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
            project_id=YEE_PROJECT_FOLLOW_UP_ID,
            name="Maple Library Plaza",
            city="Ithaca",
            province=NEW_YORK,
            country=UNITED_STATES,
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
            project_id=YEE_PROJECT_FOLLOW_UP_ID,
            name="North School Commons",
            city="Ithaca",
            province=NEW_YORK,
            country=UNITED_STATES,
            place_type="school commons",
            lat=42.4461,
            lng=-76.4934,
            start_date=date(2026, 3, 8),
            end_date=date(2026, 6, 24),
            est_auditors=2,
            auditor_description="In-progress site focused on use and usability patterns.",
            created_at=_utc_datetime("2026-03-02T11:10:00Z"),
        ),
    ]

    assignments = [
        AuditorAssignment(
            id=uuid.UUID("d2000000-0000-4000-8000-000000000001"),
            auditor_profile_id=YEE_AUDITOR_PROFILE_01_ID,
            project_id=YEE_PROJECT_CORE_ID,
            place_id=None,
            assigned_at=_utc_datetime("2026-02-24T08:00:00Z"),
        ),
        AuditorAssignment(
            id=uuid.UUID("d2000000-0000-4000-8000-000000000002"),
            auditor_profile_id=YEE_AUDITOR_PROFILE_01_ID,
            project_id=None,
            place_id=YEE_PLACE_HUB_ID,
            assigned_at=_utc_datetime("2026-02-26T09:00:00Z"),
        ),
        AuditorAssignment(
            id=uuid.UUID("d2000000-0000-4000-8000-000000000003"),
            auditor_profile_id=YEE_AUDITOR_PROFILE_02_ID,
            project_id=YEE_PROJECT_CORE_ID,
            place_id=None,
            assigned_at=_utc_datetime("2026-02-24T08:05:00Z"),
        ),
        AuditorAssignment(
            id=uuid.UUID("d2000000-0000-4000-8000-000000000004"),
            auditor_profile_id=YEE_AUDITOR_PROFILE_03_ID,
            project_id=YEE_PROJECT_FOLLOW_UP_ID,
            place_id=None,
            assigned_at=_utc_datetime("2026-03-04T08:30:00Z"),
        ),
        AuditorAssignment(
            id=uuid.UUID("d2000000-0000-4000-8000-000000000005"),
            auditor_profile_id=YEE_AUDITOR_PROFILE_03_ID,
            project_id=None,
            place_id=YEE_PLACE_LIBRARY_ID,
            assigned_at=_utc_datetime("2026-03-06T08:30:00Z"),
        ),
    ]

    audits = [
        Audit(
            id=YEE_AUDIT_HUB_ID,
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

    return [
        manager_account,
        *users,
        *manager_profiles,
        *auditor_profiles,
        *projects,
        *places,
        *assignments,
        *audits,
    ]


async def _seed_product(product: ProductKey) -> dict[str, int]:
    """Clear and repopulate one product database."""

    session_factory = ASYNC_SESSION_FACTORY_BY_PRODUCT[product]
    entities = (
        _build_playspace_entities() if product is ProductKey.PLAYSPACE else _build_yee_entities()
    )

    async with session_factory() as session:
        await _clear_shared_tables(session)
        session.add_all(entities)
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
    return parser.parse_args()


async def _run() -> None:
    """Execute the seed flow for the selected product databases."""

    args = _parse_args()
    products = (
        [ProductKey.YEE, ProductKey.PLAYSPACE]
        if args.product == "all"
        else [ProductKey(args.product)]
    )

    for product in products:
        summary = await _seed_product(product)
        print(
            f"Seeded {product.value}: "
            f"{summary['projects']} projects, "
            f"{summary['places']} places, "
            f"{summary['auditors']} auditors, "
            f"{summary['audits']} audits",
        )


if __name__ == "__main__":
    asyncio.run(_run())

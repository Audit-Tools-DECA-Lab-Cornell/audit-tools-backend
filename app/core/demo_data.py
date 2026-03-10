"""
Shared demo payloads used while auth and persistence are still being rebuilt.

The first dashboard slice needs deterministic data so the frontend can progress
without depending on a fully seeded database or the real authentication flow.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from app.core.schemas import (
    AccountDetailResponse,
    AccountStatsResponse,
    AuditorSummaryResponse,
    ManagerProfileResponse,
    PlaceSummaryResponse,
    ProjectDetailResponse,
    ProjectStatsResponse,
    ProjectSummaryResponse,
    RecentActivityResponse,
)
from app.models import AccountType

DEMO_ACCOUNT_ID = uuid.UUID("11111111-1111-4111-8111-111111111111")
DEMO_MANAGER_PROFILE_PRIMARY_ID = uuid.UUID("11111111-1111-4111-8111-111111111112")
DEMO_MANAGER_PROFILE_SECONDARY_ID = uuid.UUID("11111111-1111-4111-8111-111111111113")

DEMO_PROJECT_URBAN_ID = uuid.UUID("22222222-2222-4222-8222-222222222221")
DEMO_PROJECT_SOUTH_ID = uuid.UUID("22222222-2222-4222-8222-222222222222")

DEMO_PLACE_RIVERSIDE_ID = uuid.UUID("33333333-3333-4333-8333-333333333331")
DEMO_PLACE_KEPLER_ID = uuid.UUID("33333333-3333-4333-8333-333333333332")
DEMO_PLACE_HILLCREST_ID = uuid.UUID("33333333-3333-4333-8333-333333333333")
DEMO_PLACE_MATAI_ID = uuid.UUID("33333333-3333-4333-8333-333333333334")

DEMO_AUDITOR_AKL01_ID = uuid.UUID("44444444-4444-4444-8444-444444444441")
DEMO_AUDITOR_AKL02_ID = uuid.UUID("44444444-4444-4444-8444-444444444442")
DEMO_AUDITOR_CHC01_ID = uuid.UUID("44444444-4444-4444-8444-444444444443")

DEMO_AUDIT_RIVERSIDE_ID = uuid.UUID("55555555-5555-4555-8555-555555555551")
DEMO_AUDIT_KEPLER_ID = uuid.UUID("55555555-5555-4555-8555-555555555552")
DEMO_AUDIT_MATAI_ID = uuid.UUID("55555555-5555-4555-8555-555555555553")

DEMO_PROJECT_IDS = {
    DEMO_PROJECT_URBAN_ID,
    DEMO_PROJECT_SOUTH_ID,
}


def _utc_datetime(value: str) -> datetime:
    """Convert an ISO-like timestamp into an aware UTC datetime."""

    return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)


def get_demo_manager_profiles(account_id: uuid.UUID) -> list[ManagerProfileResponse] | None:
    """Return deterministic demo manager profiles for the known demo account."""

    if account_id != DEMO_ACCOUNT_ID:
        return None

    return [
        ManagerProfileResponse(
            id=DEMO_MANAGER_PROFILE_PRIMARY_ID,
            account_id=DEMO_ACCOUNT_ID,
            full_name="Dr. Amelia Carter",
            email="amelia.carter@example.org",
            phone="+64 21 555 0141",
            position="Primary Manager",
            organization="Auckland Playspace Collaborative",
            is_primary=True,
            created_at=_utc_datetime("2026-01-10T09:00:00Z"),
        ),
        ManagerProfileResponse(
            id=DEMO_MANAGER_PROFILE_SECONDARY_ID,
            account_id=DEMO_ACCOUNT_ID,
            full_name="Noah Bennett",
            email="noah.bennett@example.org",
            phone=None,
            position="Project Coordinator",
            organization="Auckland Playspace Collaborative",
            is_primary=False,
            created_at=_utc_datetime("2026-01-12T09:30:00Z"),
        ),
    ]


def get_demo_account_detail(account_id: uuid.UUID) -> AccountDetailResponse | None:
    """Return a deterministic account dashboard payload for the known demo account."""

    manager_profiles = get_demo_manager_profiles(account_id)
    if manager_profiles is None:
        return None

    return AccountDetailResponse(
        id=DEMO_ACCOUNT_ID,
        name="Auckland Playspace Collaborative",
        email="manager@example.org",
        account_type=AccountType.MANAGER,
        created_at=_utc_datetime("2026-01-10T08:30:00Z"),
        primary_manager=manager_profiles[0],
        stats=AccountStatsResponse(
            total_projects=2,
            total_places=4,
            total_auditors=3,
            total_audits_completed=3,
        ),
        recent_activity=[
            RecentActivityResponse(
                audit_id=DEMO_AUDIT_MATAI_ID,
                audit_code="MATAI-CHC01-2026-03-06",
                project_id=DEMO_PROJECT_SOUTH_ID,
                project_name="South Region Play Value Pilot",
                place_id=DEMO_PLACE_MATAI_ID,
                place_name="Matai Neighborhood Play Area",
                completed_at=_utc_datetime("2026-03-06T15:45:00Z"),
                score=88.0,
            ),
            RecentActivityResponse(
                audit_id=DEMO_AUDIT_KEPLER_ID,
                audit_code="KEPLER-AKL02-2026-03-05",
                project_id=DEMO_PROJECT_URBAN_ID,
                project_name="Urban Playspace Usability 2026",
                place_id=DEMO_PLACE_KEPLER_ID,
                place_name="Kepler Family Park",
                completed_at=_utc_datetime("2026-03-05T13:20:00Z"),
                score=81.0,
            ),
            RecentActivityResponse(
                audit_id=DEMO_AUDIT_RIVERSIDE_ID,
                audit_code="RIVERSIDE-AKL01-2026-03-04",
                project_id=DEMO_PROJECT_URBAN_ID,
                project_name="Urban Playspace Usability 2026",
                place_id=DEMO_PLACE_RIVERSIDE_ID,
                place_name="Riverside Community Playground",
                completed_at=_utc_datetime("2026-03-04T17:05:00Z"),
                score=74.0,
            ),
        ],
    )


def get_demo_projects(account_id: uuid.UUID) -> list[ProjectSummaryResponse] | None:
    """Return deterministic project summaries for the known demo account."""

    if account_id != DEMO_ACCOUNT_ID:
        return None

    return [
        ProjectSummaryResponse(
            id=DEMO_PROJECT_URBAN_ID,
            account_id=DEMO_ACCOUNT_ID,
            name="Urban Playspace Usability 2026",
            overview=(
                "A citywide review of urban playspaces focused on access, comfort, and play value."
            ),
            place_types=["public playspace", "school playspace"],
            start_date=date(2026, 2, 1),
            end_date=date(2026, 6, 30),
            status="active",
            places_count=2,
            auditors_count=2,
            audits_completed=2,
            average_score=77.5,
        ),
        ProjectSummaryResponse(
            id=DEMO_PROJECT_SOUTH_ID,
            account_id=DEMO_ACCOUNT_ID,
            name="South Region Play Value Pilot",
            overview="A pilot exploring play value and usability patterns across suburban sites.",
            place_types=["public playspace", "preschool playspace"],
            start_date=date(2026, 3, 1),
            end_date=date(2026, 7, 15),
            status="active",
            places_count=2,
            auditors_count=2,
            audits_completed=1,
            average_score=88.0,
        ),
    ]


def get_demo_auditors(account_id: uuid.UUID) -> list[AuditorSummaryResponse] | None:
    """Return deterministic auditor summaries for the known demo account."""

    if account_id != DEMO_ACCOUNT_ID:
        return None

    return [
        AuditorSummaryResponse(
            id=DEMO_AUDITOR_AKL01_ID,
            account_id=uuid.UUID("66666666-6666-4666-8666-666666666661"),
            auditor_code="AKL-01",
            full_name="Ariana Ngata",
            email="ariana.ngata@example.org",
            age_range="18-24",
            gender="Woman",
            country="New Zealand",
            role="student",
            assignments_count=2,
            completed_audits=1,
            last_active_at=_utc_datetime("2026-03-04T17:05:00Z"),
        ),
        AuditorSummaryResponse(
            id=DEMO_AUDITOR_AKL02_ID,
            account_id=uuid.UUID("66666666-6666-4666-8666-666666666662"),
            auditor_code="AKL-02",
            full_name="Luca Patel",
            email="luca.patel@example.org",
            age_range="25-34",
            gender="Man",
            country="New Zealand",
            role="facilitator",
            assignments_count=1,
            completed_audits=1,
            last_active_at=_utc_datetime("2026-03-05T13:20:00Z"),
        ),
        AuditorSummaryResponse(
            id=DEMO_AUDITOR_CHC01_ID,
            account_id=uuid.UUID("66666666-6666-4666-8666-666666666663"),
            auditor_code="CHC-01",
            full_name="Maya Thompson",
            email="maya.thompson@example.org",
            age_range="18-24",
            gender="Woman",
            country="New Zealand",
            role="teacher",
            assignments_count=2,
            completed_audits=1,
            last_active_at=_utc_datetime("2026-03-06T15:45:00Z"),
        ),
    ]


def get_demo_project_detail(project_id: uuid.UUID) -> ProjectDetailResponse | None:
    """Return deterministic project details for a known demo project."""

    if project_id == DEMO_PROJECT_URBAN_ID:
        return ProjectDetailResponse(
            id=DEMO_PROJECT_URBAN_ID,
            account_id=DEMO_ACCOUNT_ID,
            name="Urban Playspace Usability 2026",
            overview=(
                "A citywide review of urban playspaces focused on access, comfort, and play value."
            ),
            place_types=["public playspace", "school playspace"],
            start_date=date(2026, 2, 1),
            end_date=date(2026, 6, 30),
            est_places=12,
            est_auditors=4,
            auditor_description="Mixed student and facilitator teams working in pairs.",
            created_at=_utc_datetime("2026-01-20T10:00:00Z"),
        )

    if project_id == DEMO_PROJECT_SOUTH_ID:
        return ProjectDetailResponse(
            id=DEMO_PROJECT_SOUTH_ID,
            account_id=DEMO_ACCOUNT_ID,
            name="South Region Play Value Pilot",
            overview="A pilot exploring play value and usability patterns across suburban sites.",
            place_types=["public playspace", "preschool playspace"],
            start_date=date(2026, 3, 1),
            end_date=date(2026, 7, 15),
            est_places=8,
            est_auditors=3,
            auditor_description="Small multidisciplinary teams completing comparison audits.",
            created_at=_utc_datetime("2026-02-02T12:00:00Z"),
        )

    return None


def get_demo_project_stats(project_id: uuid.UUID) -> ProjectStatsResponse | None:
    """Return deterministic project stats for a known demo project."""

    if project_id == DEMO_PROJECT_URBAN_ID:
        return ProjectStatsResponse(
            project_id=DEMO_PROJECT_URBAN_ID,
            places_count=2,
            places_with_audits=2,
            audits_completed=2,
            auditors_count=2,
            in_progress_audits=1,
            average_score=77.5,
        )

    if project_id == DEMO_PROJECT_SOUTH_ID:
        return ProjectStatsResponse(
            project_id=DEMO_PROJECT_SOUTH_ID,
            places_count=2,
            places_with_audits=1,
            audits_completed=1,
            auditors_count=2,
            in_progress_audits=0,
            average_score=88.0,
        )

    return None


def get_demo_project_places(project_id: uuid.UUID) -> list[PlaceSummaryResponse] | None:
    """Return deterministic place rows for a known demo project."""

    if project_id == DEMO_PROJECT_URBAN_ID:
        return [
            PlaceSummaryResponse(
                id=DEMO_PLACE_RIVERSIDE_ID,
                project_id=DEMO_PROJECT_URBAN_ID,
                name="Riverside Community Playground",
                city="Auckland",
                province="Auckland",
                country="New Zealand",
                place_type="public playspace",
                status="in_progress",
                audits_completed=1,
                average_score=74.0,
                last_audited_at=_utc_datetime("2026-03-04T17:05:00Z"),
            ),
            PlaceSummaryResponse(
                id=DEMO_PLACE_KEPLER_ID,
                project_id=DEMO_PROJECT_URBAN_ID,
                name="Kepler Family Park",
                city="Auckland",
                province="Auckland",
                country="New Zealand",
                place_type="school playspace",
                status="submitted",
                audits_completed=1,
                average_score=81.0,
                last_audited_at=_utc_datetime("2026-03-05T13:20:00Z"),
            ),
        ]

    if project_id == DEMO_PROJECT_SOUTH_ID:
        return [
            PlaceSummaryResponse(
                id=DEMO_PLACE_HILLCREST_ID,
                project_id=DEMO_PROJECT_SOUTH_ID,
                name="Hillcrest Shared Play Space",
                city="Christchurch",
                province="Canterbury",
                country="New Zealand",
                place_type="preschool playspace",
                status="not_started",
                audits_completed=0,
                average_score=None,
                last_audited_at=None,
            ),
            PlaceSummaryResponse(
                id=DEMO_PLACE_MATAI_ID,
                project_id=DEMO_PROJECT_SOUTH_ID,
                name="Matai Neighborhood Play Area",
                city="Christchurch",
                province="Canterbury",
                country="New Zealand",
                place_type="public playspace",
                status="submitted",
                audits_completed=1,
                average_score=88.0,
                last_audited_at=_utc_datetime("2026-03-06T15:45:00Z"),
            ),
        ]

    return None

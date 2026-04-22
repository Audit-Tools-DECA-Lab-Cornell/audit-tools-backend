"""
Generate large, internally consistent Playspace seed data.

The Playspace mobile app and backend both expect audit data to align with the
runtime contract. This module generates a broad dataset where assignments,
execution modes, draft progress, submitted scores, and stored response payloads
all agree with the existing Playspace scoring helpers.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from random import Random
from typing import Literal, TypeVar

from app.auth_security import hash_password
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
from app.models import (
	Account,
	AccountType,
	Audit,
	AuditorAssignment,
	AuditorProfile,
	AuditStatus,
	Instrument,
	ManagerProfile,
	Place,
	Project,
	ProjectPlace,
	User,
)
from app.products.playspace.audit_state import (
	CURRENT_AUDIT_SCHEMA_VERSION,
	hydrate_relations_from_cached_json,
)
from app.products.playspace.instrument import (
	INSTRUMENT_KEY,
	INSTRUMENT_VERSION,
	get_canonical_instrument_payload,
)
from app.products.playspace.schemas.instrument import ExecutionMode
from app.products.playspace.scoring import (
	build_audit_progress_for_audit,
	get_allowed_execution_modes,
	score_audit_for_audit,
)
from app.products.playspace.scoring_metadata import (
	ScoringQuestion,
	ScoringScale,
	ScoringScaleOption,
	ScoringSection,
	get_scoring_sections,
)

T = TypeVar("T")

PlayspaceEntity = (
	Account
	| User
	| ManagerProfile
	| AuditorProfile
	| Project
	| Place
	| ProjectPlace
	| AuditorAssignment
	| Audit
	| Instrument
)
SeedJson = dict[str, object]
ProjectStatusLabel = Literal["completed", "active", "planned"]

UTC = timezone.utc

PLAYSPACE_ORGANIZATION_NAME = "Auckland Playspace Collaborative"
SECONDARY_PLAYSPACE_ORGANIZATION_NAME = "Canterbury Civic Play Trust"
PLAYSPACE_ADMIN_ACCOUNT_NAME = "Playspace Platform Administration"
NEW_ZEALAND = "New Zealand"

PUBLIC_PLAYSPACE = "public playspace"
SCHOOL_PLAYSPACE = "school playspace"
PRESCHOOL_PLAYSPACE = "preschool playspace"
DESTINATION_PLAYSPACE = "destination playspace"
NATURE_PLAYSPACE = "nature playspace"
WATERFRONT_PLAYSPACE = "waterfront playspace"
NEIGHBORHOOD_PLAYSPACE = "neighborhood playspace"

PLAYSPACE_AUDIT_RIVERSIDE_IN_PROGRESS_ID = uuid.UUID("55555555-5555-4555-8555-555555555554")

PLAYSPACE_SEED_NAMESPACE = uuid.UUID("0b9dcbde-1a95-4afd-8e2d-0c8a2621b7b4")
PLAYSPACE_RANDOM_SEED = 20260320

BASE_MANAGER_CREATED_AT = datetime(2026, 1, 10, 8, 30, tzinfo=UTC)
BASE_SECONDARY_MANAGER_CREATED_AT = datetime(2026, 1, 18, 9, 15, tzinfo=UTC)
BASE_ADMIN_CREATED_AT = datetime(2026, 1, 6, 11, 0, tzinfo=UTC)

SECONDARY_MANAGER_ACCOUNT_ID = uuid.uuid5(
	PLAYSPACE_SEED_NAMESPACE,
	"playspace-account::manager-secondary",
)
SECONDARY_MANAGER_PROFILE_PRIMARY_ID = uuid.uuid5(
	PLAYSPACE_SEED_NAMESPACE,
	"playspace-manager-profile::manager-secondary-primary",
)
SECONDARY_MANAGER_PROFILE_OPERATIONS_ID = uuid.uuid5(
	PLAYSPACE_SEED_NAMESPACE,
	"playspace-manager-profile::manager-secondary-operations",
)
PLAYSPACE_ADMIN_ACCOUNT_ID = uuid.uuid5(
	PLAYSPACE_SEED_NAMESPACE,
	"playspace-account::admin-primary",
)

PRE_AUDIT_SEASONS = ("spring", "summer", "autumn", "winter")
PRE_AUDIT_WEATHER_OPTIONS = ("sunshine", "cloudy", "windy", "inclement_weather")
PRE_AUDIT_USER_OPTIONS = ("children", "adults")
PRE_AUDIT_USER_COUNT_OPTIONS = ("none", "some", "a_lot")
PRE_AUDIT_AGE_GROUP_OPTIONS = ("under_5", "age_6_10", "age_11_plus")
PRE_AUDIT_SIZE_OPTIONS = ("small", "medium", "large")


@dataclass(frozen=True)
class MetroTemplate:
	"""Stable geography information used for projects and generated places."""

	key: str
	city: str
	province: str
	center_lat: float
	center_lng: float
	neighborhoods: tuple[str, ...]


@dataclass(frozen=True)
class ProjectBlueprint:
	"""Blueprint used to generate one project and its related place set."""

	key: str
	name: str
	overview: str
	metro: MetroTemplate
	status: ProjectStatusLabel
	place_types: tuple[str, ...]
	focus_terms: tuple[str, ...]
	extra_place_count: int


@dataclass(frozen=True)
class AuditorBlueprint:
	"""Identity and profile details for a seeded auditor."""

	auditor_code: str
	full_name: str
	home_city: str
	age_range: str
	gender: str
	role: str


@dataclass(frozen=True)
class AuditorSeedContext:
	"""Bundle the ORM entities and home metro for one auditor."""

	account: Account
	profile: AuditorProfile
	home_city: str


@dataclass(frozen=True)
class ProjectSeedContext:
	"""Bundle the ORM entity and blueprint for one generated project."""

	project: Project
	blueprint: ProjectBlueprint


@dataclass(frozen=True)
class PlaceSeedContext:
	"""Bundle the ORM entity and generation hints for one place."""

	place: Place
	project_context: ProjectSeedContext
	quality_bias: float
	usage_bias: float


METRO_AUCKLAND = MetroTemplate(
	key="akl",
	city="Auckland",
	province="Auckland",
	center_lat=-36.8485,
	center_lng=174.7633,
	neighborhoods=(
		"Harbourview",
		"Kauri Point",
		"Meadowbank",
		"Stonefields",
		"Westhaven",
		"Tui Grove",
		"Orakei",
		"Bayside",
		"Greenlane",
		"Seabreeze",
	),
)
METRO_CHRISTCHURCH = MetroTemplate(
	key="chc",
	city="Christchurch",
	province="Canterbury",
	center_lat=-43.5321,
	center_lng=172.6362,
	neighborhoods=(
		"Avonlea",
		"Cashmere",
		"Hillsborough",
		"Linwood",
		"Redcliffs",
		"Wainoni",
		"Strowan",
		"Somerfield",
		"Edgeware",
		"Burwood",
	),
)
METRO_WELLINGTON = MetroTemplate(
	key="wlg",
	city="Wellington",
	province="Wellington",
	center_lat=-41.2866,
	center_lng=174.7756,
	neighborhoods=(
		"Roseneath",
		"Newtown",
		"Aro Valley",
		"Karori",
		"Kilbirnie",
		"Brooklyn",
		"Thorndon",
		"Island Bay",
	),
)
METRO_HAMILTON = MetroTemplate(
	key="ham",
	city="Hamilton",
	province="Waikato",
	center_lat=-37.787,
	center_lng=175.2793,
	neighborhoods=(
		"Claudelands",
		"Dinsdale",
		"Fairfield",
		"Flagstaff",
		"Rototuna",
		"Hillcrest",
		"Nawton",
		"Whitiora",
	),
)
METRO_TAURANGA = MetroTemplate(
	key="trg",
	city="Tauranga",
	province="Bay of Plenty",
	center_lat=-37.6878,
	center_lng=176.1651,
	neighborhoods=(
		"Papamoa",
		"Bethlehem",
		"Greerton",
		"Matua",
		"Otumoetai",
		"Welcome Bay",
		"Mount Vista",
		"Bellevue",
	),
)
METRO_DUNEDIN = MetroTemplate(
	key="dud",
	city="Dunedin",
	province="Otago",
	center_lat=-45.8788,
	center_lng=170.5028,
	neighborhoods=(
		"St Clair",
		"Musselburgh",
		"Roslyn",
		"Mornington",
		"Andersons Bay",
		"North East Valley",
		"Wakari",
		"Kaikorai",
	),
)

AUDITOR_BLUEPRINTS: tuple[AuditorBlueprint, ...] = (
	AuditorBlueprint("AKL-01", "Ariana Ngata", "Auckland", "18-24", "Woman", "student"),
	AuditorBlueprint("AKL-02", "Luca Patel", "Auckland", "25-34", "Man", "facilitator"),
	AuditorBlueprint("CHC-01", "Maya Thompson", "Christchurch", "18-24", "Woman", "teacher"),
	AuditorBlueprint("AKL-03", "Riley Morgan", "Auckland", "25-34", "Non-binary", "urban designer"),
	AuditorBlueprint("AKL-04", "Sienna Walker", "Auckland", "18-24", "Woman", "student"),
	AuditorBlueprint("AKL-05", "Ethan Brooks", "Auckland", "25-34", "Man", "community youth worker"),
	AuditorBlueprint("CHC-02", "Talia Cooper", "Christchurch", "18-24", "Woman", "student"),
	AuditorBlueprint(
		"CHC-03",
		"James Mason",
		"Christchurch",
		"35-44",
		"Man",
		"occupational therapist",
	),
	AuditorBlueprint("CHC-04", "Ava Reed", "Christchurch", "25-34", "Woman", "playworker"),
	AuditorBlueprint("WLG-01", "Hana Robinson", "Wellington", "18-24", "Woman", "student"),
	AuditorBlueprint("WLG-02", "Leo Wilson", "Wellington", "25-34", "Man", "landscape architect"),
	AuditorBlueprint("WLG-03", "Mila Foster", "Wellington", "25-34", "Woman", "youth researcher"),
	AuditorBlueprint("HAM-01", "Noah Turner", "Hamilton", "25-34", "Man", "teacher"),
	AuditorBlueprint("HAM-02", "Zoe Parker", "Hamilton", "18-24", "Woman", "student"),
	AuditorBlueprint("TRG-01", "Isla Hughes", "Tauranga", "25-34", "Woman", "community facilitator"),
	AuditorBlueprint("TRG-02", "Arlo Bennett", "Tauranga", "18-24", "Man", "student"),
	AuditorBlueprint("DUD-01", "Ruby Gray", "Dunedin", "25-34", "Woman", "research assistant"),
	AuditorBlueprint("DUD-02", "Theo Sullivan", "Dunedin", "18-24", "Man", "student"),
)

PROJECT_BLUEPRINTS: tuple[ProjectBlueprint, ...] = (
	ProjectBlueprint(
		key="urban_usability_2026",
		name="Urban Playspace Usability 2026",
		overview="A citywide review of urban playspaces focused on access, comfort, and play value.",
		metro=METRO_AUCKLAND,
		status="active",
		place_types=(PUBLIC_PLAYSPACE, SCHOOL_PLAYSPACE, WATERFRONT_PLAYSPACE),
		focus_terms=("accessibility", "social play", "shade", "wayfinding"),
		extra_place_count=6,
	),
	ProjectBlueprint(
		key="south_region_pilot",
		name="South Region Play Value Pilot",
		overview="A pilot exploring play value and usability patterns across suburban sites.",
		metro=METRO_CHRISTCHURCH,
		status="active",
		place_types=(PUBLIC_PLAYSPACE, PRESCHOOL_PLAYSPACE, NEIGHBORHOOD_PLAYSPACE),
		focus_terms=(
			"community usability",
			"loose parts",
			"restorative edges",
			"challenge",
		),
		extra_place_count=6,
	),
	ProjectBlueprint(
		key="auckland_renewal_loop",
		name="Auckland Community Renewal Loop",
		overview="An expanded sample of neighborhood and destination playspaces undergoing renewal.",
		metro=METRO_AUCKLAND,
		status="active",
		place_types=(PUBLIC_PLAYSPACE, DESTINATION_PLAYSPACE, NATURE_PLAYSPACE),
		focus_terms=(
			"maintenance",
			"activity diversity",
			"inclusive circulation",
			"comfort",
		),
		extra_place_count=7,
	),
	ProjectBlueprint(
		key="capital_waterfront_network",
		name="Capital Waterfront Play Network",
		overview="A mixed audit set focused on waterfront and civic-adjacent play environments.",
		metro=METRO_WELLINGTON,
		status="active",
		place_types=(WATERFRONT_PLAYSPACE, PUBLIC_PLAYSPACE, SCHOOL_PLAYSPACE),
		focus_terms=(
			"wind protection",
			"connectivity",
			"spectator seating",
			"group play",
		),
		extra_place_count=7,
	),
	ProjectBlueprint(
		key="waikato_access_review",
		name="Waikato Neighborhood Access Review",
		overview="A regional benchmark of neighborhood play access, maintenance, and comfort.",
		metro=METRO_HAMILTON,
		status="completed",
		place_types=(NEIGHBORHOOD_PLAYSPACE, PUBLIC_PLAYSPACE, SCHOOL_PLAYSPACE),
		focus_terms=("access", "maintenance", "shade", "legibility"),
		extra_place_count=7,
	),
	ProjectBlueprint(
		key="bay_coastal_play_pilot",
		name="Bay Coastal Play Pilot",
		overview="A coastal pilot emphasizing climate conditions, sensory play, and family dwell time.",
		metro=METRO_TAURANGA,
		status="active",
		place_types=(WATERFRONT_PLAYSPACE, PUBLIC_PLAYSPACE, DESTINATION_PLAYSPACE),
		focus_terms=("coastal exposure", "sensory play", "family stay", "heat comfort"),
		extra_place_count=7,
	),
	ProjectBlueprint(
		key="southern_climate_study",
		name="Southern Climate-Responsive Playspaces",
		overview="A comparative study of climate-ready playspaces in cooler southern conditions.",
		metro=METRO_DUNEDIN,
		status="completed",
		place_types=(PUBLIC_PLAYSPACE, NATURE_PLAYSPACE, SCHOOL_PLAYSPACE),
		focus_terms=("seasonal use", "shelter", "terrain", "novelty"),
		extra_place_count=7,
	),
	ProjectBlueprint(
		key="christchurch_future_sites",
		name="Christchurch Future Sites Planning Set",
		overview="Planned places prepared for upcoming fieldwork and assignment rehearsal.",
		metro=METRO_CHRISTCHURCH,
		status="planned",
		place_types=(PUBLIC_PLAYSPACE, PRESCHOOL_PLAYSPACE, NATURE_PLAYSPACE),
		focus_terms=(
			"planning",
			"access routes",
			"community readiness",
			"future audit sequencing",
		),
		extra_place_count=6,
	),
)

PRIMARY_MANAGER_PROJECT_BLUEPRINTS: tuple[ProjectBlueprint, ...] = PROJECT_BLUEPRINTS[:4]
SECONDARY_MANAGER_PROJECT_BLUEPRINTS: tuple[ProjectBlueprint, ...] = PROJECT_BLUEPRINTS[4:]

BASE_PLACE_BLUEPRINTS: dict[uuid.UUID, tuple[float, float]] = {
	DEMO_PLACE_RIVERSIDE_ID: (0.66, 0.72),
	DEMO_PLACE_KEPLER_ID: (0.74, 0.64),
	DEMO_PLACE_HILLCREST_ID: (0.58, 0.48),
	DEMO_PLACE_MATAI_ID: (0.84, 0.78),
}


def build_playspace_seed_entities() -> list[PlayspaceEntity]:
	"""Build a multi-account Playspace seed set with realistic audit coverage."""

	randomizer = Random(PLAYSPACE_RANDOM_SEED)
	reference_date = date.today()

	primary_manager_account = Account(
		id=DEMO_ACCOUNT_ID,
		name=PLAYSPACE_ORGANIZATION_NAME,
		email="manager@example.org",
		password_hash=_placeholder_password_hash("playspace-manager"),
		account_type=AccountType.MANAGER,
		created_at=BASE_MANAGER_CREATED_AT,
	)
	secondary_manager_account = Account(
		id=SECONDARY_MANAGER_ACCOUNT_ID,
		name=SECONDARY_PLAYSPACE_ORGANIZATION_NAME,
		email="canterbury.manager@example.org",
		password_hash=_placeholder_password_hash("playspace-manager-secondary"),
		account_type=AccountType.MANAGER,
		created_at=BASE_SECONDARY_MANAGER_CREATED_AT,
	)
	admin_account = Account(
		id=PLAYSPACE_ADMIN_ACCOUNT_ID,
		name=PLAYSPACE_ADMIN_ACCOUNT_NAME,
		email="playspace.admin@example.org",
		password_hash=_placeholder_password_hash("playspace-admin"),
		account_type=AccountType.ADMIN,
		created_at=BASE_ADMIN_CREATED_AT,
	)

	canonical_instrument = Instrument(
		instrument_key=INSTRUMENT_KEY,
		instrument_version=INSTRUMENT_VERSION,
		is_active=True,
		content={"en": get_canonical_instrument_payload()},
		created_at=BASE_ADMIN_CREATED_AT + timedelta(minutes=5),
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
			created_at=BASE_MANAGER_CREATED_AT + timedelta(minutes=30),
		),
		ManagerProfile(
			id=DEMO_MANAGER_PROFILE_SECONDARY_ID,
			account_id=DEMO_ACCOUNT_ID,
			full_name="Noah Bennett",
			email="noah.bennett@example.org",
			phone="+64 21 555 0193",
			position="Project Coordinator",
			organization=PLAYSPACE_ORGANIZATION_NAME,
			is_primary=False,
			created_at=BASE_MANAGER_CREATED_AT + timedelta(days=2, minutes=45),
		),
		ManagerProfile(
			id=SECONDARY_MANAGER_PROFILE_PRIMARY_ID,
			account_id=SECONDARY_MANAGER_ACCOUNT_ID,
			full_name="Aroha Sinclair",
			email="aroha.sinclair@example.org",
			phone="+64 21 555 0211",
			position="Programme Director",
			organization=SECONDARY_PLAYSPACE_ORGANIZATION_NAME,
			is_primary=True,
			created_at=BASE_SECONDARY_MANAGER_CREATED_AT + timedelta(minutes=20),
		),
		ManagerProfile(
			id=SECONDARY_MANAGER_PROFILE_OPERATIONS_ID,
			account_id=SECONDARY_MANAGER_ACCOUNT_ID,
			full_name="Elliot Fraser",
			email="elliot.fraser@example.org",
			phone="+64 21 555 0284",
			position="Operations Lead",
			organization=SECONDARY_PLAYSPACE_ORGANIZATION_NAME,
			is_primary=False,
			created_at=BASE_SECONDARY_MANAGER_CREATED_AT + timedelta(days=1, minutes=15),
		),
	]

	auditor_contexts = _build_auditor_contexts(reference_date=reference_date)
	primary_project_contexts = _build_project_contexts(
		account_id=primary_manager_account.id,
		reference_date=reference_date,
		blueprints=PRIMARY_MANAGER_PROJECT_BLUEPRINTS,
	)
	secondary_project_contexts = _build_project_contexts(
		account_id=secondary_manager_account.id,
		reference_date=reference_date,
		blueprints=SECONDARY_MANAGER_PROJECT_BLUEPRINTS,
	)
	project_contexts = [*primary_project_contexts, *secondary_project_contexts]
	place_contexts = _build_place_contexts(
		project_contexts=project_contexts,
		reference_date=reference_date,
		randomizer=randomizer,
	)
	project_place_links = _build_project_place_links(place_contexts=place_contexts)
	assignments, execution_modes_by_place_and_auditor = _build_assignments(
		project_contexts=project_contexts,
		place_contexts=place_contexts,
		auditor_contexts=auditor_contexts,
		reference_date=reference_date,
		randomizer=randomizer,
	)
	_hydrate_estimated_counts(
		project_contexts=project_contexts,
		place_contexts=place_contexts,
		project_place_links=project_place_links,
		execution_modes_by_place_and_auditor=execution_modes_by_place_and_auditor,
	)
	audits = _build_audits(
		place_contexts=place_contexts,
		auditor_contexts=auditor_contexts,
		execution_modes_by_place_and_auditor=execution_modes_by_place_and_auditor,
		reference_date=reference_date,
		randomizer=randomizer,
	)

	accounts = [
		admin_account,
		primary_manager_account,
		secondary_manager_account,
		*(context.account for context in auditor_contexts),
	]
	auditor_profiles = [context.profile for context in auditor_contexts]
	users = _build_user_entities(
		accounts=accounts,
		manager_profiles=manager_profiles,
		auditor_profiles=auditor_profiles,
	)
	projects = [context.project for context in project_contexts]
	places = [context.place for context in place_contexts]

	return [
		*accounts,
		*users,
		*manager_profiles,
		*auditor_profiles,
		*projects,
		*places,
		*project_place_links,
		*assignments,
		*audits,
		canonical_instrument,
	]


def _build_auditor_contexts(*, reference_date: date) -> list[AuditorSeedContext]:
	"""Create the stable auditor accounts and profiles used across all seeded projects."""

	contexts: list[AuditorSeedContext] = []
	created_at = datetime.combine(reference_date - timedelta(days=80), time(9, 0), tzinfo=UTC)
	fixed_ids_by_code: dict[str, tuple[uuid.UUID, uuid.UUID]] = {
		"AKL-01": (_stable_uuid("playspace-account", "AKL-01"), DEMO_AUDITOR_AKL01_ID),
		"AKL-02": (_stable_uuid("playspace-account", "AKL-02"), DEMO_AUDITOR_AKL02_ID),
		"CHC-01": (_stable_uuid("playspace-account", "CHC-01"), DEMO_AUDITOR_CHC01_ID),
	}

	for index, blueprint in enumerate(AUDITOR_BLUEPRINTS):
		fixed_ids = fixed_ids_by_code.get(blueprint.auditor_code)
		account_id = (
			fixed_ids[0] if fixed_ids is not None else _stable_uuid("playspace-account", blueprint.auditor_code)
		)
		profile_id = (
			fixed_ids[1] if fixed_ids is not None else _stable_uuid("playspace-profile", blueprint.auditor_code)
		)
		email = _email_from_name(blueprint.full_name)
		account = Account(
			id=account_id,
			name=blueprint.full_name,
			email=email,
			password_hash=_placeholder_password_hash(f"playspace-{blueprint.auditor_code.lower()}"),
			account_type=AccountType.AUDITOR,
			created_at=created_at + timedelta(minutes=index * 7),
		)
		profile = AuditorProfile(
			id=profile_id,
			account_id=account_id,
			auditor_code=blueprint.auditor_code,
			email=email,
			full_name=blueprint.full_name,
			age_range=blueprint.age_range,
			gender=blueprint.gender,
			country=NEW_ZEALAND,
			role=blueprint.role,
			created_at=created_at + timedelta(minutes=(index * 7) + 3),
		)
		contexts.append(
			AuditorSeedContext(
				account=account,
				profile=profile,
				home_city=blueprint.home_city,
			)
		)

	return contexts


def _build_user_entities(
	*,
	accounts: list[Account],
	manager_profiles: list[ManagerProfile],
	auditor_profiles: list[AuditorProfile],
) -> list[User]:
	"""Create one auth user per seeded Playspace account."""

	primary_manager_name_by_account_id = {
		profile.account_id: profile.full_name for profile in manager_profiles if profile.is_primary
	}
	auditor_profile_by_account_id = {profile.account_id: profile for profile in auditor_profiles}
	users: list[User] = []

	for account in accounts:
		if account.account_type == AccountType.MANAGER:
			display_name = primary_manager_name_by_account_id.get(account.id)
		elif account.account_type == AccountType.AUDITOR:
			auditor_profile = auditor_profile_by_account_id.get(account.id)
			display_name = auditor_profile.full_name if auditor_profile is not None else account.name
		else:
			display_name = account.name

		user = User(
			id=_stable_uuid("playspace-user", account.email),
			email=account.email,
			password_hash=account.password_hash or _placeholder_password_hash(account.email),
			account_id=account.id,
			account_type=account.account_type,
			name=display_name,
			email_verified=True,
			email_verified_at=account.created_at,
			failed_login_attempts=0,
			approved=True,
			approved_at=account.created_at,
			profile_completed=display_name is not None,
			profile_completed_at=(account.created_at if display_name is not None else None),
			created_at=account.created_at,
		)
		users.append(user)

		auditor_profile = auditor_profile_by_account_id.get(account.id)
		if auditor_profile is not None:
			auditor_profile.user_id = user.id

	return users


def _build_project_contexts(
	*,
	account_id: uuid.UUID,
	reference_date: date,
	blueprints: tuple[ProjectBlueprint, ...],
) -> list[ProjectSeedContext]:
	"""Create a mix of active, completed, and planned Playspace projects."""

	contexts: list[ProjectSeedContext] = []
	for index, blueprint in enumerate(blueprints):
		project_id = _resolve_project_id(blueprint=blueprint)
		start_date, end_date = _project_dates(status=blueprint.status, reference_date=reference_date)
		project = Project(
			id=project_id,
			account_id=account_id,
			name=blueprint.name,
			overview=blueprint.overview,
			place_types=list(blueprint.place_types),
			start_date=start_date,
			end_date=end_date,
			est_places=blueprint.extra_place_count,
			est_auditors=0,
			auditor_description=(
				f"Teams focus on {blueprint.focus_terms[0]}, {blueprint.focus_terms[1]}, "
				f"and {blueprint.focus_terms[2]} across {blueprint.metro.city.lower()} sites."
			),
			created_at=datetime.combine(
				start_date - timedelta(days=10 + index),
				time(10, 0),
				tzinfo=UTC,
			),
		)
		contexts.append(ProjectSeedContext(project=project, blueprint=blueprint))

	return contexts


def _build_place_contexts(
	*,
	project_contexts: list[ProjectSeedContext],
	reference_date: date,
	randomizer: Random,
) -> list[PlaceSeedContext]:
	"""Create a wide place set while keeping the original demo places intact."""

	contexts: list[PlaceSeedContext] = []
	for project_context in project_contexts:
		if project_context.project.id == DEMO_PROJECT_URBAN_ID:
			contexts.extend(
				_build_base_urban_places(
					project_context=project_context,
					reference_date=reference_date,
					randomizer=randomizer,
				)
			)
			continue
		if project_context.project.id == DEMO_PROJECT_SOUTH_ID:
			contexts.extend(
				_build_base_south_places(
					project_context=project_context,
					reference_date=reference_date,
					randomizer=randomizer,
				)
			)
			continue

		contexts.extend(
			_build_generated_places_for_project(
				project_context=project_context,
				reference_date=reference_date,
				randomizer=randomizer,
			)
		)

	return contexts


def _build_base_urban_places(
	*,
	project_context: ProjectSeedContext,
	reference_date: date,
	randomizer: Random,
) -> list[PlaceSeedContext]:
	"""Create the original Auckland places plus several additional realistic sites."""

	project = project_context.project
	start_anchor = project.start_date or reference_date
	base_places = [
		PlaceSeedContext(
			place=Place(
				id=DEMO_PLACE_RIVERSIDE_ID,
				name="Riverside Community Playground",
				city="Auckland",
				province="Auckland",
				country=NEW_ZEALAND,
				place_type=PUBLIC_PLAYSPACE,
				lat=-36.8485,
				lng=174.7633,
				start_date=start_anchor + timedelta(days=4),
				end_date=start_anchor + timedelta(days=75),
				est_auditors=0,
				auditor_description="Pair audit with accessibility and maintenance notes.",
				created_at=datetime.combine(start_anchor + timedelta(days=1), time(9, 0), tzinfo=UTC),
			),
			project_context=project_context,
			quality_bias=BASE_PLACE_BLUEPRINTS[DEMO_PLACE_RIVERSIDE_ID][0],
			usage_bias=BASE_PLACE_BLUEPRINTS[DEMO_PLACE_RIVERSIDE_ID][1],
		),
		PlaceSeedContext(
			place=Place(
				id=DEMO_PLACE_KEPLER_ID,
				name="Kepler Family Park",
				city="Auckland",
				province="Auckland",
				country=NEW_ZEALAND,
				place_type=SCHOOL_PLAYSPACE,
				lat=-36.8618,
				lng=174.7706,
				start_date=start_anchor + timedelta(days=6),
				end_date=start_anchor + timedelta(days=86),
				est_auditors=0,
				auditor_description="Single-site validation audit for school users.",
				created_at=datetime.combine(start_anchor + timedelta(days=2), time(9, 10), tzinfo=UTC),
			),
			project_context=project_context,
			quality_bias=BASE_PLACE_BLUEPRINTS[DEMO_PLACE_KEPLER_ID][0],
			usage_bias=BASE_PLACE_BLUEPRINTS[DEMO_PLACE_KEPLER_ID][1],
		),
	]
	generated_places = _build_generated_places_for_project(
		project_context=project_context,
		reference_date=reference_date,
		randomizer=randomizer,
		fixed_name_overrides=(
			"Harbourview Adventure Playspace",
			"Kauri Point Learning Park",
			"Meadowbank Discovery Grove",
			"Stonefields Family Play Lawn",
			"Westhaven Tide Terrace",
			"Tui Grove Neighborhood Play Hub",
		),
	)
	return [*base_places, *generated_places]


def _build_base_south_places(
	*,
	project_context: ProjectSeedContext,
	reference_date: date,
	randomizer: Random,
) -> list[PlaceSeedContext]:
	"""Create the original Christchurch places plus several additional realistic sites."""

	project = project_context.project
	start_anchor = project.start_date or reference_date
	base_places = [
		PlaceSeedContext(
			place=Place(
				id=DEMO_PLACE_HILLCREST_ID,
				name="Hillcrest Shared Play Space",
				city="Christchurch",
				province="Canterbury",
				country=NEW_ZEALAND,
				place_type=PRESCHOOL_PLAYSPACE,
				lat=-43.5321,
				lng=172.6362,
				start_date=start_anchor + timedelta(days=4),
				end_date=start_anchor + timedelta(days=84),
				est_auditors=0,
				auditor_description="Upcoming comparison site for early years play patterns.",
				created_at=datetime.combine(start_anchor + timedelta(days=1), time(8, 45), tzinfo=UTC),
			),
			project_context=project_context,
			quality_bias=BASE_PLACE_BLUEPRINTS[DEMO_PLACE_HILLCREST_ID][0],
			usage_bias=BASE_PLACE_BLUEPRINTS[DEMO_PLACE_HILLCREST_ID][1],
		),
		PlaceSeedContext(
			place=Place(
				id=DEMO_PLACE_MATAI_ID,
				name="Matai Neighborhood Play Area",
				city="Christchurch",
				province="Canterbury",
				country=NEW_ZEALAND,
				place_type=PUBLIC_PLAYSPACE,
				lat=-43.5403,
				lng=172.6292,
				start_date=start_anchor + timedelta(days=3),
				end_date=start_anchor + timedelta(days=90),
				est_auditors=0,
				auditor_description="Neighbourhood pilot with emphasis on community usability.",
				created_at=datetime.combine(start_anchor + timedelta(days=1), time(9, 0), tzinfo=UTC),
			),
			project_context=project_context,
			quality_bias=BASE_PLACE_BLUEPRINTS[DEMO_PLACE_MATAI_ID][0],
			usage_bias=BASE_PLACE_BLUEPRINTS[DEMO_PLACE_MATAI_ID][1],
		),
	]
	generated_places = _build_generated_places_for_project(
		project_context=project_context,
		reference_date=reference_date,
		randomizer=randomizer,
		fixed_name_overrides=(
			"Avonlea Nature Playscape",
			"Cashmere Climb Commons",
			"Redcliffs Coastal Play Terrace",
			"Somerfield Family Play Garden",
			"Burwood Discovery Park",
			"Wainoni Loose Parts Reserve",
		),
	)
	return [*base_places, *generated_places]


def _build_generated_places_for_project(
	*,
	project_context: ProjectSeedContext,
	reference_date: date,
	randomizer: Random,
	fixed_name_overrides: tuple[str, ...] = (),
) -> list[PlaceSeedContext]:
	"""Create additional generated places for one project blueprint."""

	contexts: list[PlaceSeedContext] = []
	blueprint = project_context.blueprint
	project = project_context.project
	project_start = project.start_date or reference_date
	name_pool = (
		fixed_name_overrides
		if fixed_name_overrides
		else tuple(
			_build_place_name(
				metro=blueprint.metro,
				focus_terms=blueprint.focus_terms,
				place_type=blueprint.place_types[index % len(blueprint.place_types)],
				index=index,
			)
			for index in range(blueprint.extra_place_count)
		)
	)

	for index, place_name in enumerate(name_pool):
		quality_bias = round(randomizer.uniform(0.32, 0.9), 2)
		usage_bias = round(randomizer.uniform(0.35, 0.88), 2)
		place_type = blueprint.place_types[index % len(blueprint.place_types)]
		lat, lng = _offset_coordinates(
			metro=blueprint.metro,
			index=index,
			randomizer=randomizer,
		)
		place_start, place_end = _place_dates(
			project_start=project_start,
			project_end=project.end_date,
			project_status=blueprint.status,
			index=index,
		)
		contexts.append(
			PlaceSeedContext(
				place=Place(
					id=_stable_uuid("playspace-place", blueprint.key, place_name),
					name=place_name,
					city=blueprint.metro.city,
					province=blueprint.metro.province,
					country=NEW_ZEALAND,
					place_type=place_type,
					lat=lat,
					lng=lng,
					start_date=place_start,
					end_date=place_end,
					est_auditors=0,
					auditor_description=_build_place_description(
						place_type=place_type,
						focus_terms=blueprint.focus_terms,
						quality_bias=quality_bias,
					),
					created_at=datetime.combine(
						project_start + timedelta(days=index + 1),
						time(9, 0),
						tzinfo=UTC,
					),
				),
				project_context=project_context,
				quality_bias=quality_bias,
				usage_bias=usage_bias,
			)
		)

	return contexts


def _build_project_place_links(
	*,
	place_contexts: list[PlaceSeedContext],
) -> list[ProjectPlace]:
	"""Create project-place links for seeded places."""

	links_by_key: dict[tuple[uuid.UUID, uuid.UUID], ProjectPlace] = {}
	for place_context in place_contexts:
		key = (place_context.project_context.project.id, place_context.place.id)
		links_by_key[key] = ProjectPlace(
			project_id=place_context.project_context.project.id,
			place_id=place_context.place.id,
			linked_at=place_context.place.created_at,
		)

	shared_demo_place = next(
		(place_context for place_context in place_contexts if place_context.place.id == DEMO_PLACE_HILLCREST_ID),
		None,
	)
	if shared_demo_place is not None:
		links_by_key[(DEMO_PROJECT_SOUTH_ID, shared_demo_place.place.id)] = ProjectPlace(
			project_id=DEMO_PROJECT_SOUTH_ID,
			place_id=shared_demo_place.place.id,
			linked_at=shared_demo_place.place.created_at + timedelta(days=3),
		)

	return list(links_by_key.values())


def _build_assignments(
	*,
	project_contexts: list[ProjectSeedContext],
	place_contexts: list[PlaceSeedContext],
	auditor_contexts: list[AuditorSeedContext],
	reference_date: date,
	randomizer: Random,
) -> tuple[list[AuditorAssignment], dict[tuple[uuid.UUID, uuid.UUID], list[ExecutionMode]]]:
	"""Create project and place assignments plus a resolved execution-mode map."""

	# making sure the auditor_profile_id, place_id, project_id are unique for each assignment.
	# no project-wide assignments. so, we don't have the project_auditors_by_project dictionary.

	assignments: list[AuditorAssignment] = []
	execution_modes_by_place_and_auditor: dict[tuple[uuid.UUID, uuid.UUID], list[ExecutionMode]] = {}

	places_by_project_id: dict[uuid.UUID, list[PlaceSeedContext]] = {}
	for place_context in place_contexts:
		places_by_project_id.setdefault(place_context.project_context.project.id, []).append(place_context)

	auditors_by_city: dict[str, list[AuditorSeedContext]] = {}
	for auditor_context in auditor_contexts:
		auditors_by_city.setdefault(auditor_context.home_city, []).append(auditor_context)

	project_place_auditor_unique_assignments: set[tuple[str, str, str]] = set()
	project_auditors_by_project: dict[uuid.UUID, list[AuditorSeedContext]] = {}
	for index, project_context in enumerate(project_contexts):
		local_auditors = list(auditors_by_city.get(project_context.blueprint.metro.city, []))
		all_auditors = list(auditor_contexts)
		randomizer.shuffle(local_auditors)
		randomizer.shuffle(all_auditors)

		selected: list[AuditorSeedContext] = local_auditors[: min(len(local_auditors), 3)]
		for auditor_context in all_auditors:
			if auditor_context in selected:
				continue
			selected.append(auditor_context)
			if len(selected) >= 5:
				break

		project_auditors_by_project[project_context.project.id] = selected

		for place_index, place_context in enumerate(
			sorted(
				places_by_project_id.get(project_context.project.id, []),
				key=lambda current_context: current_context.place.name.lower(),
			)
		):
			project_auditors = project_auditors_by_project[project_context.project.id]
			if not project_auditors:
				continue

			lead_context = project_auditors[place_index % len(project_auditors)]

			if (
				str(project_context.project.id),
				str(place_context.place.id),
				str(lead_context.profile.id),
			) in project_place_auditor_unique_assignments:
				continue
			project_place_auditor_unique_assignments.add(
				(
					str(project_context.project.id),
					str(place_context.place.id),
					str(lead_context.profile.id),
				)
			)
			try:
				lead_assignment = AuditorAssignment(
					id=_stable_uuid(
						"playspace-assignment",
						"place-lead",
						str(place_context.place.id),
						str(lead_context.profile.id),
					),
					auditor_profile_id=lead_context.profile.id,
					project_id=project_context.project.id,
					place_id=place_context.place.id,
					assigned_at=datetime.combine(
						(place_context.place.start_date or reference_date) - timedelta(days=2),
						time(8, 30),
						tzinfo=UTC,
					),
				)
				assignments.append(lead_assignment)
				execution_modes_by_place_and_auditor.setdefault(
					(place_context.place.id, lead_context.profile.id),
					get_allowed_execution_modes(),
				)
			except Exception as e:
				print(f"Exception: {e}")
				print(f"project_context.project.id: {project_context.project.id}")
				print(f"place_context.place.id: {place_context.place.id}")
				print(f"lead_context.profile.id: {lead_context.profile.id}")
				print("--------------------------------")
				print(
					f"project_place_auditor_unique_assignments: {str(project_context.project.id), str(place_context.place.id), str(lead_context.profile.id)}"
				)
				print(
					f"exist in project_place_auditor_unique_assignments: {project_context.project.id, place_context.place.id, lead_context.profile.id in project_place_auditor_unique_assignments}"
				)

			if randomizer.random() < 0.55 and len(project_auditors) > 1:
				support_context = project_auditors[(place_index + 1) % len(project_auditors)]

				if (
					str(project_context.project.id),
					str(place_context.place.id),
					str(support_context.profile.id),
				) in project_place_auditor_unique_assignments:
					continue
				project_place_auditor_unique_assignments.add(
					(
						str(project_context.project.id),
						str(place_context.place.id),
						str(support_context.profile.id),
					)
				)
				try:
					support_assignment = AuditorAssignment(
						id=_stable_uuid(
							"playspace-assignment",
							"place-support",
							str(place_context.place.id),
							str(support_context.profile.id),
						),
						auditor_profile_id=support_context.profile.id,
						project_id=project_context.project.id,
						place_id=place_context.place.id,
						assigned_at=datetime.combine(
							(place_context.place.start_date or reference_date) - timedelta(days=1),
							time(9, 10),
							tzinfo=UTC,
						),
					)
					assignments.append(support_assignment)
					execution_modes_by_place_and_auditor.setdefault(
						(place_context.place.id, support_context.profile.id),
						get_allowed_execution_modes(),
					)
				except Exception as e:
					print(f"Exception: {e}")
					print(f"project_context.project.id: {project_context.project.id}")
					print(f"place_context.place.id: {place_context.place.id}")
					print(f"support_context.profile.id: {support_context.profile.id}")
					print("--------------------------------")
					print(
						f"project_place_auditor_unique_assignments: {str(project_context.project.id), str(place_context.place.id), str(support_context.profile.id)}"
					)
					print(
						f"exist in project_place_auditor_unique_assignments: {project_context.project.id, place_context.place.id, support_context.profile.id in project_place_auditor_unique_assignments}"
					)

			off_project_candidates = [
				auditor_context
				for auditor_context in auditor_contexts
				if auditor_context.profile.id not in {context.profile.id for context in project_auditors}
				and auditor_context.home_city == place_context.place.city
			]
			if off_project_candidates and randomizer.random() < 0.2:
				specialist_context = off_project_candidates[place_index % len(off_project_candidates)]
				if (
					str(project_context.project.id),
					str(place_context.place.id),
					str(specialist_context.profile.id),
				) in project_place_auditor_unique_assignments:
					continue
				project_place_auditor_unique_assignments.add(
					(
						str(project_context.project.id),
						str(place_context.place.id),
						str(specialist_context.profile.id),
					)
				)
				try:
					specialist_assignment = AuditorAssignment(
						id=_stable_uuid(
							"playspace-assignment",
							"place-specialist",
							str(place_context.place.id),
							str(specialist_context.profile.id),
						),
						auditor_profile_id=specialist_context.profile.id,
						project_id=project_context.project.id,
						place_id=place_context.place.id,
						assigned_at=datetime.combine(
							(place_context.place.start_date or reference_date) - timedelta(days=1),
							time(10, 0),
							tzinfo=UTC,
						),
					)
					assignments.append(specialist_assignment)
					execution_modes_by_place_and_auditor.setdefault(
						(place_context.place.id, specialist_context.profile.id),
						get_allowed_execution_modes(),
					)
				except Exception as e:
					print(f"Exception: {e}")
					print(f"project_context.project.id: {project_context.project.id}")
					print(f"place_context.place.id: {place_context.place.id}")
					print(f"specialist_context.profile.id: {specialist_context.profile.id}")
					print("--------------------------------")
					print(
						f"project_place_auditor_unique_assignments: {str(project_context.project.id), str(place_context.place.id), str(specialist_context.profile.id)}"
					)
					print(
						f"exist in project_place_auditor_unique_assignments: {project_context.project.id, place_context.place.id, specialist_context.profile.id in project_place_auditor_unique_assignments}"
					)

	return assignments, execution_modes_by_place_and_auditor


def _hydrate_estimated_counts(
	*,
	project_contexts: list[ProjectSeedContext],
	place_contexts: list[PlaceSeedContext],
	project_place_links: list[ProjectPlace],
	execution_modes_by_place_and_auditor: dict[tuple[uuid.UUID, uuid.UUID], list[ExecutionMode]],
) -> None:
	"""Backfill count fields so the dashboard descriptions reflect the generated graph."""

	places_by_project_id: dict[uuid.UUID, set[uuid.UUID]] = {}
	for project_place_link in project_place_links:
		places_by_project_id.setdefault(project_place_link.project_id, set()).add(project_place_link.place_id)

	auditors_by_project_id: dict[uuid.UUID, set[uuid.UUID]] = {}
	auditors_by_place_id: dict[uuid.UUID, set[uuid.UUID]] = {}
	for (
		place_id,
		auditor_profile_id,
	), _modes in execution_modes_by_place_and_auditor.items():
		auditors_by_place_id.setdefault(place_id, set()).add(auditor_profile_id)

	place_context_by_id = {place_context.place.id: place_context for place_context in place_contexts}
	for place_id, place_context in place_context_by_id.items():
		place_context.place.est_auditors = len(auditors_by_place_id.get(place_id, set()))

	for project_id, place_ids in places_by_project_id.items():
		for place_id in place_ids:
			auditors_by_project_id.setdefault(project_id, set()).update(auditors_by_place_id.get(place_id, set()))

	for project_context in project_contexts:
		project_place_ids = places_by_project_id.get(project_context.project.id, set())
		project_context.project.est_places = len(project_place_ids)
		project_context.project.est_auditors = len(auditors_by_project_id.get(project_context.project.id, set()))
		project_context.project.place_types = sorted(
			{
				place_type
				for place_id in project_place_ids
				if (place_type := place_context_by_id[place_id].place.place_type) is not None
			}
		)


def _build_audits(
	*,
	place_contexts: list[PlaceSeedContext],
	auditor_contexts: list[AuditorSeedContext],
	execution_modes_by_place_and_auditor: dict[tuple[uuid.UUID, uuid.UUID], list[ExecutionMode]],
	reference_date: date,
	randomizer: Random,
) -> list[Audit]:
	"""Create submitted and draft audits that stay aligned with the live Playspace rules."""

	audits: list[Audit] = []
	auditor_context_by_id = {auditor_context.profile.id: auditor_context for auditor_context in auditor_contexts}
	generated_place_ids: set[uuid.UUID] = set()

	for place_context in sorted(
		place_contexts,
		key=lambda current_context: (
			current_context.project_context.project.name.lower(),
			current_context.place.name.lower(),
		),
	):
		if place_context.place.id == DEMO_PLACE_RIVERSIDE_ID:
			audits.extend(
				_build_fixed_base_audits_for_riverside(
					place_context=place_context,
					auditor_context_by_id=auditor_context_by_id,
					execution_modes_by_place_and_auditor=execution_modes_by_place_and_auditor,
					reference_date=reference_date,
					randomizer=randomizer,
				)
			)
			generated_place_ids.add(place_context.place.id)
			continue
		if place_context.place.id == DEMO_PLACE_KEPLER_ID:
			audits.extend(
				_build_fixed_base_audit(
					audit_id=DEMO_AUDIT_KEPLER_ID,
					slot_key="kepler-submitted",
					place_context=place_context,
					auditor_context=auditor_context_by_id[DEMO_AUDITOR_AKL02_ID],
					execution_modes_by_place_and_auditor=execution_modes_by_place_and_auditor,
					reference_date=reference_date,
					randomizer=randomizer,
					status=AuditStatus.SUBMITTED,
					quality_bias=0.74,
					draft_ratio=None,
				)
			)
			generated_place_ids.add(place_context.place.id)
			continue
		if place_context.place.id == DEMO_PLACE_MATAI_ID:
			audits.extend(
				_build_fixed_base_audit(
					audit_id=DEMO_AUDIT_MATAI_ID,
					slot_key="matai-submitted",
					place_context=place_context,
					auditor_context=auditor_context_by_id[DEMO_AUDITOR_CHC01_ID],
					execution_modes_by_place_and_auditor=execution_modes_by_place_and_auditor,
					reference_date=reference_date,
					randomizer=randomizer,
					status=AuditStatus.SUBMITTED,
					quality_bias=0.84,
					draft_ratio=None,
				)
			)
			generated_place_ids.add(place_context.place.id)

	for place_context in place_contexts:
		if place_context.place.id in generated_place_ids:
			continue
		audits.extend(
			_build_generated_audits_for_place(
				place_context=place_context,
				auditor_context_by_id=auditor_context_by_id,
				execution_modes_by_place_and_auditor=execution_modes_by_place_and_auditor,
				reference_date=reference_date,
				randomizer=randomizer,
			)
		)

	return audits


def _build_fixed_base_audits_for_riverside(
	*,
	place_context: PlaceSeedContext,
	auditor_context_by_id: dict[uuid.UUID, AuditorSeedContext],
	execution_modes_by_place_and_auditor: dict[tuple[uuid.UUID, uuid.UUID], list[ExecutionMode]],
	reference_date: date,
	randomizer: Random,
) -> list[Audit]:
	"""Keep the original Riverside pair while generating real responses and progress."""

	submitted_audit = _build_fixed_base_audit(
		audit_id=DEMO_AUDIT_RIVERSIDE_ID,
		slot_key="riverside-submitted",
		place_context=place_context,
		auditor_context=auditor_context_by_id[DEMO_AUDITOR_AKL01_ID],
		execution_modes_by_place_and_auditor=execution_modes_by_place_and_auditor,
		reference_date=reference_date,
		randomizer=randomizer,
		status=AuditStatus.SUBMITTED,
		quality_bias=0.67,
		draft_ratio=None,
	)
	draft_audit = _build_fixed_base_audit(
		audit_id=PLAYSPACE_AUDIT_RIVERSIDE_IN_PROGRESS_ID,
		slot_key="riverside-draft",
		place_context=place_context,
		auditor_context=auditor_context_by_id[DEMO_AUDITOR_AKL02_ID],
		execution_modes_by_place_and_auditor=execution_modes_by_place_and_auditor,
		reference_date=reference_date,
		randomizer=randomizer,
		status=AuditStatus.IN_PROGRESS,
		quality_bias=0.62,
		draft_ratio=0.43,
	)
	return [*submitted_audit, *draft_audit]


def _build_fixed_base_audit(
	*,
	audit_id: uuid.UUID,
	slot_key: str,
	place_context: PlaceSeedContext,
	auditor_context: AuditorSeedContext,
	execution_modes_by_place_and_auditor: dict[tuple[uuid.UUID, uuid.UUID], list[ExecutionMode]],
	reference_date: date,
	randomizer: Random,
	status: AuditStatus,
	quality_bias: float,
	draft_ratio: float | None,
) -> list[Audit]:
	"""Create one known audit record using the same generation helpers as the bulk dataset."""

	allowed_modes = execution_modes_by_place_and_auditor.get(
		(place_context.place.id, auditor_context.profile.id),
		[],
	)
	if not allowed_modes:
		return []

	reference_datetime = datetime.combine(reference_date, time(9, 0), tzinfo=UTC)
	day_offset = -16 if status == AuditStatus.SUBMITTED else -4
	started_at = reference_datetime + timedelta(days=day_offset, hours=7, minutes=15)
	usage_bias = max(0.2, min(0.95, place_context.usage_bias + 0.03))
	audit = _build_audit_record(
		audit_id=audit_id,
		slot_key=slot_key,
		place_context=place_context,
		auditor_context=auditor_context,
		allowed_execution_modes=allowed_modes,
		started_at=started_at,
		randomizer=randomizer,
		status=status,
		quality_bias=quality_bias,
		usage_bias=usage_bias,
		draft_ratio=draft_ratio,
	)
	return [audit]


def _build_generated_audits_for_place(
	*,
	place_context: PlaceSeedContext,
	auditor_context_by_id: dict[uuid.UUID, AuditorSeedContext],
	execution_modes_by_place_and_auditor: dict[tuple[uuid.UUID, uuid.UUID], list[ExecutionMode]],
	reference_date: date,
	randomizer: Random,
) -> list[Audit]:
	"""Create a realistic mix of completed, active, and untouched place audit histories."""

	project_status = _status_from_dates(
		start_date=place_context.project_context.project.start_date,
		end_date=place_context.project_context.project.end_date,
		reference_date=reference_date,
	)
	place_start = place_context.place.start_date
	if place_start is None or place_start > reference_date:
		return []
	if project_status == "planned":
		return []

	assigned_auditor_ids = [
		auditor_profile_id
		for (
			place_id,
			auditor_profile_id,
		), _modes in execution_modes_by_place_and_auditor.items()
		if place_id == place_context.place.id
	]
	assigned_auditor_ids = sorted(set(assigned_auditor_ids))
	if not assigned_auditor_ids:
		return []

	should_leave_empty = project_status == "active" and randomizer.random() < 0.08
	if should_leave_empty:
		return []

	audit_count = 1
	if project_status == "completed":
		audit_count = 1 if randomizer.random() < 0.45 else 2
	elif randomizer.random() < 0.35:
		audit_count = 2

	audits: list[Audit] = []
	author_ids = assigned_auditor_ids[:]
	randomizer.shuffle(author_ids)
	historical_author_ids = author_ids[:audit_count]
	for history_index, auditor_profile_id in enumerate(historical_author_ids):
		auditor_context = auditor_context_by_id[auditor_profile_id]
		allowed_modes = execution_modes_by_place_and_auditor[(place_context.place.id, auditor_profile_id)]
		started_at = _historical_started_at(
			place_context=place_context,
			reference_date=reference_date,
			slot_index=history_index,
			randomizer=randomizer,
		)
		quality_bias = _bounded_bias(place_context.quality_bias + randomizer.uniform(-0.08, 0.08))
		usage_bias = _bounded_bias(place_context.usage_bias + randomizer.uniform(-0.05, 0.05))
		audits.append(
			_build_audit_record(
				audit_id=_stable_uuid(
					"playspace-audit",
					str(place_context.project_context.project.id),
					str(place_context.place.id),
					str(auditor_profile_id),
					"submitted",
					str(history_index),
				),
				slot_key=f"submitted-{history_index}",
				place_context=place_context,
				auditor_context=auditor_context,
				allowed_execution_modes=allowed_modes,
				started_at=started_at,
				randomizer=randomizer,
				status=AuditStatus.SUBMITTED,
				quality_bias=quality_bias,
				usage_bias=usage_bias,
				draft_ratio=None,
			)
		)

	if project_status == "completed":
		return audits

	should_add_draft = randomizer.random() < 0.42
	if not should_add_draft:
		return audits

	# One audit per (project, place, auditor) — do not assign a second draft row to an
	# auditor who already has a submitted audit for this place.
	submitted_auditor_ids = set(historical_author_ids)
	draft_candidates = [aid for aid in author_ids if aid not in submitted_auditor_ids]
	if not draft_candidates:
		return audits

	draft_author_id = draft_candidates[-1]
	draft_author = auditor_context_by_id[draft_author_id]
	draft_allowed_modes = execution_modes_by_place_and_auditor[(place_context.place.id, draft_author_id)]
	draft_status = AuditStatus.PAUSED if randomizer.random() < 0.35 else AuditStatus.IN_PROGRESS
	started_at = _draft_started_at(reference_date=reference_date, randomizer=randomizer)
	draft_ratio = randomizer.uniform(0.12, 0.88)
	audits.append(
		_build_audit_record(
			audit_id=_stable_uuid(
				"playspace-audit",
				str(place_context.project_context.project.id),
				str(place_context.place.id),
				str(draft_author_id),
				draft_status.value.lower(),
				"current",
			),
			slot_key=f"{draft_status.value.lower()}-current",
			place_context=place_context,
			auditor_context=draft_author,
			allowed_execution_modes=draft_allowed_modes,
			started_at=started_at,
			randomizer=randomizer,
			status=draft_status,
			quality_bias=_bounded_bias(place_context.quality_bias + randomizer.uniform(-0.07, 0.04)),
			usage_bias=_bounded_bias(place_context.usage_bias + randomizer.uniform(-0.1, 0.08)),
			draft_ratio=draft_ratio,
		)
	)
	return audits


def _build_audit_record(
	*,
	audit_id: uuid.UUID,
	slot_key: str,
	place_context: PlaceSeedContext,
	auditor_context: AuditorSeedContext,
	allowed_execution_modes: list[ExecutionMode],
	started_at: datetime,
	randomizer: Random,
	status: AuditStatus,
	quality_bias: float,
	usage_bias: float,
	draft_ratio: float | None,
) -> Audit:
	"""Create one audit row with responses and scores derived from live Playspace helpers."""

	execution_mode = _select_execution_mode(
		allowed_execution_modes=allowed_execution_modes,
		randomizer=randomizer,
		quality_bias=quality_bias,
	)
	if status == AuditStatus.SUBMITTED:
		responses_json = _build_responses_json(
			execution_mode=execution_mode,
			quality_bias=quality_bias,
			usage_bias=usage_bias,
			started_at=started_at,
			place_context=place_context,
			randomizer=randomizer,
			target_completion_ratio=1.0,
			complete_pre_audit=True,
		)
		duration_minutes = randomizer.randint(42, 96)
		submitted_at = started_at + timedelta(minutes=duration_minutes)
		submitted_audit = Audit(
			id=audit_id,
			project_id=place_context.project_context.project.id,
			place_id=place_context.place.id,
			auditor_profile_id=auditor_context.profile.id,
			audit_code=_build_audit_code(
				place_name=place_context.place.name,
				auditor_code=auditor_context.profile.auditor_code,
				started_at=started_at,
				slot_key=slot_key,
			),
			instrument_key=INSTRUMENT_KEY,
			instrument_version=INSTRUMENT_VERSION,
			status=AuditStatus.SUBMITTED,
			started_at=started_at,
			submitted_at=submitted_at,
			total_minutes=duration_minutes,
			summary_score=None,
			responses_json=responses_json,
			scores_json={},
			created_at=started_at,
			updated_at=submitted_at,
		)
		hydrate_relations_from_cached_json(submitted_audit)
		calculated_scores = score_audit_for_audit(audit=submitted_audit)
		submitted_audit.scores_json = calculated_scores
		overall_payload = calculated_scores.get("overall")
		submitted_audit.summary_score = (
			round(
				float(overall_payload["play_value_total"]) + float(overall_payload["usability_total"]),
				2,
			)
			if isinstance(overall_payload, dict)
			and isinstance(overall_payload.get("play_value_total"), int | float)
			and isinstance(overall_payload.get("usability_total"), int | float)
			else None
		)
		return submitted_audit

	resolved_ratio = 0.45 if draft_ratio is None else max(0.02, min(draft_ratio, 0.98))
	responses_json = _build_responses_json(
		execution_mode=execution_mode,
		quality_bias=quality_bias,
		usage_bias=usage_bias,
		started_at=started_at,
		place_context=place_context,
		randomizer=randomizer,
		target_completion_ratio=resolved_ratio,
		complete_pre_audit=resolved_ratio >= 0.35,
	)
	last_saved_minutes = randomizer.randint(12, 78)
	updated_at = started_at + timedelta(minutes=last_saved_minutes)
	draft_audit = Audit(
		id=audit_id,
		project_id=place_context.project_context.project.id,
		place_id=place_context.place.id,
		auditor_profile_id=auditor_context.profile.id,
		audit_code=_build_audit_code(
			place_name=place_context.place.name,
			auditor_code=auditor_context.profile.auditor_code,
			started_at=started_at,
			slot_key=slot_key,
		),
		instrument_key=INSTRUMENT_KEY,
		instrument_version=INSTRUMENT_VERSION,
		status=status,
		started_at=started_at,
		submitted_at=None,
		total_minutes=last_saved_minutes,
		summary_score=None,
		responses_json=responses_json,
		scores_json={},
		created_at=started_at,
		updated_at=updated_at,
	)
	hydrate_relations_from_cached_json(draft_audit)
	progress = build_audit_progress_for_audit(audit=draft_audit)
	draft_audit.scores_json = {
		"draft_progress_percent": _progress_percent(progress=progress),
		"progress": progress.model_dump(),
	}
	return draft_audit


def _build_responses_json(
	*,
	execution_mode: ExecutionMode,
	quality_bias: float,
	usage_bias: float,
	started_at: datetime,
	place_context: PlaceSeedContext,
	randomizer: Random,
	target_completion_ratio: float,
	complete_pre_audit: bool,
) -> SeedJson:
	"""Build a runtime-shaped responses payload for one draft or submission."""

	meta: SeedJson = {"execution_mode": execution_mode.value}
	pre_audit = _build_pre_audit_payload(
		place_context=place_context,
		started_at=started_at,
		usage_bias=usage_bias,
		randomizer=randomizer,
		complete_pre_audit=complete_pre_audit,
	)
	visible_sections = [
		section
		for section in get_scoring_sections()
		if len(_visible_questions_for_mode(section=section, execution_mode=execution_mode)) > 0
	]

	total_visible_questions = sum(
		1
		for section in visible_sections
		for question in _visible_questions_for_mode(section=section, execution_mode=execution_mode)
		if question.required and question.question_type == "scaled"
	)
	target_answered_questions = min(
		total_visible_questions,
		max(0, round(total_visible_questions * target_completion_ratio)),
	)
	if 0.0 < target_completion_ratio < 1.0 and target_answered_questions == total_visible_questions:
		target_answered_questions = max(total_visible_questions - 1, 0)
	if target_completion_ratio > 0 and target_answered_questions == 0 and total_visible_questions > 0:
		target_answered_questions = 1

	sections_payload: SeedJson = {}
	remaining_questions = target_answered_questions
	# We fill sections in order so drafts look like a real session that progressed through the form.
	for section in visible_sections:
		if remaining_questions <= 0:
			break

		visible_questions = _visible_questions_for_mode(
			section=section,
			execution_mode=execution_mode,
		)
		answered_count = 0
		section_responses: SeedJson = {}
		for question in visible_questions:
			if not _is_question_visible_for_seed(
				question=question,
				section_responses=section_responses,
			):
				continue

			if question.question_type == "checklist":
				if question.required or randomizer.random() < 0.35:
					checklist_answers = _build_checklist_answers(
						question=question,
						randomizer=randomizer,
					)
					if checklist_answers:
						section_responses[question.question_key] = checklist_answers
				continue

			if remaining_questions <= 0:
				continue

			section_responses[question.question_key] = _build_question_answers(
				question=question,
				quality_bias=quality_bias,
				usage_bias=usage_bias,
				randomizer=randomizer,
			)
			answered_count += 1
			remaining_questions -= 1

		if section_responses:
			required_visible_question_count = sum(
				1
				for question in visible_questions
				if question.required
				and question.question_type == "scaled"
				and _is_question_visible_for_seed(
					question=question,
					section_responses=section_responses,
				)
			)
			section_payload: SeedJson = {"responses": section_responses}
			if randomizer.random() < 0.45:
				section_payload["note"] = _build_section_note(
					section_key=section.section_key,
					place_name=place_context.place.name,
					focus_terms=place_context.project_context.blueprint.focus_terms,
					is_complete=answered_count == required_visible_question_count,
				)
			sections_payload[section.section_key] = section_payload

	return {
		"schema_version": CURRENT_AUDIT_SCHEMA_VERSION,
		"revision": 1,
		"meta": meta,
		"pre_audit": pre_audit,
		"sections": sections_payload,
	}


def _build_question_answers(
	*,
	question: ScoringQuestion,
	quality_bias: float,
	usage_bias: float,
	randomizer: Random,
) -> dict[str, str]:
	"""Build one valid per-question response object using the scoring metadata itself."""

	provision_scale = next(scale for scale in question.scales if scale.key == "provision")
	provision_target = _bounded_bias((quality_bias * 0.7) + (usage_bias * 0.3))
	provision_option = _pick_option_for_scale(
		scale=provision_scale,
		target_bias=provision_target,
		randomizer=randomizer,
		not_applicable_weight=0.0,
	)
	answers = {"provision": provision_option.key}
	if not provision_option.allows_follow_up_scales:
		return answers

	for scale in question.scales:
		if scale.key == "provision":
			continue
		follow_up_target = _bounded_bias(quality_bias + randomizer.uniform(-0.15, 0.12))
		follow_up_option = _pick_option_for_scale(
			scale=scale,
			target_bias=follow_up_target,
			randomizer=randomizer,
			not_applicable_weight=0.12,
		)
		answers[scale.key] = follow_up_option.key

	return answers


def _build_checklist_answers(
	*,
	question: ScoringQuestion,
	randomizer: Random,
) -> SeedJson:
	"""Build a simple optional checklist response payload for one follow-up question."""

	option_keys = [option.key for option in question.options]
	if len(option_keys) == 0:
		return {}

	max_selected = min(3, len(option_keys))
	selected_count = randomizer.randint(1, max_selected)
	selected_option_keys = randomizer.sample(option_keys, selected_count)
	payload: SeedJson = {
		"selected_option_keys": selected_option_keys,
	}
	if "other" in selected_option_keys:
		payload["other_details"] = {
			"text": "Additional seeded example",
		}
	return payload


def _pick_option_for_scale(
	*,
	scale: ScoringScale,
	target_bias: float,
	randomizer: Random,
	not_applicable_weight: float,
) -> ScoringScaleOption:
	"""Pick a scale option by weighting values toward a target quality level."""

	ratings = [_option_rating(option=option) for option in scale.options]
	max_rating = max(ratings) if ratings else 1.0
	weights: list[float] = []
	for option, rating in zip(scale.options, ratings, strict=True):
		if option.key == "not_applicable":
			weights.append(not_applicable_weight)
			continue
		normalized_rating = rating / max_rating if max_rating > 0 else 0.0
		closeness = max(0.0, 1.0 - abs(normalized_rating - target_bias))
		weights.append(0.15 + (closeness * 4.0))
	return _weighted_choice(options=scale.options, weights=weights, randomizer=randomizer)


def _build_pre_audit_payload(
	*,
	place_context: PlaceSeedContext,
	started_at: datetime,
	usage_bias: float,
	randomizer: Random,
	complete_pre_audit: bool,
) -> SeedJson:
	"""Build valid pre-audit keys using the exact option keys expected by the app."""

	season = _season_for_new_zealand(date_value=started_at.date())
	weather_conditions = _weather_for_season(
		season=season,
		usage_bias=usage_bias,
		randomizer=randomizer,
	)
	place_size = _place_size_for_context(place_context=place_context)
	current_user_counts = _current_user_counts_for_place(
		place_context=place_context,
		usage_bias=usage_bias,
		randomizer=randomizer,
	)
	playspace_busyness = _playspace_busyness_from_usage(usage_bias=usage_bias)
	wind_conditions = _wind_conditions_for_season(
		season=season,
		usage_bias=usage_bias,
		randomizer=randomizer,
	)

	payload: SeedJson = {
		"place_size": place_size,
		"current_users_0_5": current_user_counts["current_users_0_5"],
		"current_users_6_12": current_user_counts["current_users_6_12"],
		"current_users_13_17": current_user_counts["current_users_13_17"],
		"current_users_18_plus": current_user_counts["current_users_18_plus"],
		"playspace_busyness": playspace_busyness,
		"season": season,
		"weather_conditions": weather_conditions,
		"wind_conditions": wind_conditions,
	}
	if complete_pre_audit:
		return payload

	partial_steps = [
		("place_size", place_size),
		("current_users_0_5", current_user_counts["current_users_0_5"]),
		("season", season),
		("weather_conditions", weather_conditions),
	]
	partial_payload: SeedJson = {}
	keep_count = randomizer.randint(1, len(partial_steps))
	for field_name, value in partial_steps[:keep_count]:
		partial_payload[field_name] = value
	return partial_payload


def _build_section_note(
	*,
	section_key: str,
	place_name: str,
	focus_terms: tuple[str, ...],
	is_complete: bool,
) -> str:
	"""Create a short, human-readable note for one section payload."""

	humanized_section = section_key.replace("section_", "").replace("_", " ")
	focus_index = sum(ord(character) for character in section_key) % len(focus_terms)
	focus_label = focus_terms[focus_index]
	if is_complete:
		return f"Captured final observations for {humanized_section} at {place_name} with emphasis on {focus_label}."
	return f"Partial notes saved for {humanized_section}; revisit {focus_label} patterns before submission."


def _select_execution_mode(
	*,
	allowed_execution_modes: list[ExecutionMode],
	randomizer: Random,
	quality_bias: float,
) -> ExecutionMode:
	"""Pick one auditor-selected execution mode from the available choices."""

	if len(allowed_execution_modes) == 1:
		return allowed_execution_modes[0]

	weights: list[float] = []
	for mode in allowed_execution_modes:
		if mode is ExecutionMode.BOTH:
			weights.append(1.6 + quality_bias)
			continue
		if mode is ExecutionMode.AUDIT:
			weights.append(1.2 if quality_bias >= 0.55 else 0.9)
			continue
		weights.append(0.85)
	return _weighted_choice(
		options=allowed_execution_modes,
		weights=weights,
		randomizer=randomizer,
	)


def _weighted_choice(*, options: list[T], weights: list[float], randomizer: Random) -> T:
	"""Pick one option using deterministic weights without relying on global random state."""

	if not options:
		raise ValueError("weighted choice requires at least one option")

	bounded_weights = [max(weight, 0.0) for weight in weights]
	total_weight = sum(bounded_weights)
	if total_weight <= 0:
		return options[0]

	threshold = randomizer.uniform(0.0, total_weight)
	running_total = 0.0
	for option, weight in zip(options, bounded_weights, strict=True):
		running_total += weight
		if threshold <= running_total:
			return option
	return options[-1]


def _project_dates(
	*,
	status: ProjectStatusLabel,
	reference_date: date,
) -> tuple[date, date]:
	"""Create dates that keep the seeded project status meaningful relative to today."""

	if status == "completed":
		start_date = reference_date - timedelta(days=120)
		end_date = reference_date - timedelta(days=15)
		return start_date, end_date
	if status == "planned":
		start_date = reference_date + timedelta(days=18)
		end_date = reference_date + timedelta(days=110)
		return start_date, end_date
	start_date = reference_date - timedelta(days=42)
	end_date = reference_date + timedelta(days=95)
	return start_date, end_date


def _place_dates(
	*,
	project_start: date,
	project_end: date | None,
	project_status: ProjectStatusLabel,
	index: int,
) -> tuple[date, date]:
	"""Create place fieldwork windows nested inside the project schedule."""

	if project_status == "planned":
		place_start = project_start + timedelta(days=index * 2)
		place_end = place_start + timedelta(days=45)
		return place_start, place_end

	place_start = project_start + timedelta(days=min(index * 3, 25))
	default_end = place_start + timedelta(days=65)
	if project_end is None:
		return place_start, default_end
	return place_start, min(project_end, default_end)


def _historical_started_at(
	*,
	place_context: PlaceSeedContext,
	reference_date: date,
	slot_index: int,
	randomizer: Random,
) -> datetime:
	"""Create a completed-audit timestamp that fits inside the seeded place schedule."""

	place_start = place_context.place.start_date or reference_date
	latest_allowed_date = min(
		reference_date - timedelta(days=3),
		place_context.place.end_date or reference_date,
	)
	window_days = max((latest_allowed_date - place_start).days, 2)
	chosen_day = min(window_days, 4 + (slot_index * 9) + randomizer.randint(0, 12))
	audit_day = place_start + timedelta(days=chosen_day)
	return datetime.combine(
		audit_day,
		time(hour=9 + (slot_index % 3), minute=(slot_index * 10) % 60),
		tzinfo=UTC,
	)


def _draft_started_at(*, reference_date: date, randomizer: Random) -> datetime:
	"""Create a recent draft timestamp for active audits."""

	audit_day = reference_date - timedelta(days=randomizer.randint(0, 8))
	return datetime.combine(
		audit_day,
		time(
			hour=8 + randomizer.randint(0, 6),
			minute=randomizer.choice([0, 10, 20, 30, 40, 50]),
		),
		tzinfo=UTC,
	)


def _status_from_dates(
	*,
	start_date: date | None,
	end_date: date | None,
	reference_date: date,
) -> ProjectStatusLabel:
	"""Derive a simple project status using the same semantics as the dashboard."""

	if start_date is not None and start_date > reference_date:
		return "planned"
	if end_date is not None and end_date < reference_date:
		return "completed"
	return "active"


def _progress_percent(*, progress: object) -> float:
	"""Mirror the runtime draft progress percentage logic used by the audit service."""

	if not hasattr(progress, "total_visible_questions") or not hasattr(progress, "answered_visible_questions"):
		return 0.0
	total_visible_questions = progress.total_visible_questions
	answered_visible_questions = progress.answered_visible_questions
	if not isinstance(total_visible_questions, int) or not isinstance(answered_visible_questions, int):
		return 0.0
	if total_visible_questions <= 0:
		return 0.0
	return round((answered_visible_questions / total_visible_questions) * 100, 2)


def _visible_questions_for_mode(
	*,
	section: ScoringSection,
	execution_mode: ExecutionMode,
) -> list[ScoringQuestion]:
	"""Filter a scoring section down to the questions visible for one execution mode."""

	mode_value = execution_mode.value
	return [question for question in section.questions if question.mode == "both" or question.mode == mode_value]


def _is_question_visible_for_seed(
	*,
	question: ScoringQuestion,
	section_responses: SeedJson,
) -> bool:
	"""Evaluate simple intra-section display logic using already-built section answers."""

	if question.display_if is None:
		return True

	parent_answers = section_responses.get(question.display_if.question_key)
	if not isinstance(parent_answers, dict):
		return False

	selected_value = parent_answers.get(question.display_if.response_key)
	if isinstance(selected_value, str):
		return selected_value in question.display_if.any_of_option_keys
	if isinstance(selected_value, list):
		return any(
			isinstance(entry, str) and entry in question.display_if.any_of_option_keys for entry in selected_value
		)
	return False


def _offset_coordinates(
	*,
	metro: MetroTemplate,
	index: int,
	randomizer: Random,
) -> tuple[float, float]:
	"""Generate nearby coordinates so places cluster around the expected city."""

	lat_offset = ((index % 4) - 1.5) * 0.013 + randomizer.uniform(-0.003, 0.003)
	lng_offset = ((index % 5) - 2.0) * 0.012 + randomizer.uniform(-0.003, 0.003)
	return round(metro.center_lat + lat_offset, 6), round(metro.center_lng + lng_offset, 6)


def _build_place_name(
	*,
	metro: MetroTemplate,
	focus_terms: tuple[str, ...],
	place_type: str,
	index: int,
) -> str:
	"""Create a plausible place name from the metro neighborhood and project theme."""

	neighborhood = metro.neighborhoods[index % len(metro.neighborhoods)]
	focus_term = focus_terms[index % len(focus_terms)].title()
	suffix = {
		PUBLIC_PLAYSPACE: "Playground",
		SCHOOL_PLAYSPACE: "Learning Park",
		PRESCHOOL_PLAYSPACE: "Early Years Play Space",
		DESTINATION_PLAYSPACE: "Adventure Playscape",
		NATURE_PLAYSPACE: "Nature Grove",
		WATERFRONT_PLAYSPACE: "Waterfront Play Terrace",
		NEIGHBORHOOD_PLAYSPACE: "Neighborhood Play Hub",
	}.get(place_type, "Play Space")
	return f"{neighborhood} {focus_term} {suffix}"


def _build_place_description(
	*,
	place_type: str,
	focus_terms: tuple[str, ...],
	quality_bias: float,
) -> str:
	"""Create a short realistic manager-facing description for one place."""

	tone = "strong existing" if quality_bias >= 0.72 else "mixed existing"
	return f"{tone} {place_type} conditions with emphasis on {focus_terms[0]}, {focus_terms[1]}, and {focus_terms[2]}."


def _place_size_for_context(*, place_context: PlaceSeedContext) -> str:
	"""Map place type and usage into one of the front-end size option keys."""

	place_type = place_context.place.place_type
	if place_type in {DESTINATION_PLAYSPACE, WATERFRONT_PLAYSPACE}:
		return "very_large"
	if place_type == PRESCHOOL_PLAYSPACE:
		return "small"
	if place_context.usage_bias >= 0.72:
		return "large"
	if place_context.usage_bias <= 0.42:
		return "small"
	return "medium"


def _current_user_counts_for_place(
	*,
	place_context: PlaceSeedContext,
	usage_bias: float,
	randomizer: Random,
) -> SeedJson:
	"""Build age-group provision values for the onsite setup matrix."""

	place_type = place_context.place.place_type
	counts_by_group: SeedJson = {
		"current_users_0_5": "none",
		"current_users_6_12": "none",
		"current_users_13_17": "none",
		"current_users_18_plus": "none",
	}

	def resolve_count(*, base_bias: float) -> str:
		adjusted_bias = _bounded_bias(base_bias + randomizer.uniform(-0.12, 0.12))
		if adjusted_bias >= 0.72:
			return "a_lot"
		if adjusted_bias >= 0.32:
			return "a_few"
		return "none"

	counts_by_group["current_users_18_plus"] = resolve_count(base_bias=max(0.28, usage_bias - 0.18))

	if place_type == PRESCHOOL_PLAYSPACE:
		counts_by_group["current_users_0_5"] = resolve_count(base_bias=usage_bias + 0.18)
		counts_by_group["current_users_6_12"] = resolve_count(base_bias=usage_bias - 0.12)
		return counts_by_group

	if place_type == SCHOOL_PLAYSPACE:
		counts_by_group["current_users_6_12"] = resolve_count(base_bias=usage_bias + 0.2)
		counts_by_group["current_users_13_17"] = resolve_count(base_bias=usage_bias - 0.02)
		return counts_by_group

	counts_by_group["current_users_0_5"] = resolve_count(base_bias=usage_bias - 0.08)
	counts_by_group["current_users_6_12"] = resolve_count(base_bias=usage_bias + 0.1)
	counts_by_group["current_users_13_17"] = resolve_count(base_bias=usage_bias - 0.04)
	return counts_by_group


def _playspace_busyness_from_usage(*, usage_bias: float) -> str:
	"""Convert usage bias into one of the busyness option keys."""

	if usage_bias >= 0.72:
		return "very_busy"
	if usage_bias >= 0.34:
		return "somewhat_busy"
	return "not_at_all_busy"


def _wind_conditions_for_season(
	*,
	season: str,
	usage_bias: float,
	randomizer: Random,
) -> str:
	"""Pick one wind condition that aligns with the seeded season and weather."""

	if season == "winter":
		return "heavy_wind" if usage_bias < 0.45 and randomizer.random() < 0.35 else "occasional_gusts"
	if season == "autumn":
		return "occasional_gusts" if randomizer.random() < 0.55 else "light_wind"
	if season == "summer":
		return "no_wind" if usage_bias >= 0.62 and randomizer.random() < 0.5 else "light_wind"
	return "light_wind" if randomizer.random() < 0.6 else "occasional_gusts"


def _weather_for_season(
	*,
	season: str,
	usage_bias: float,
	randomizer: Random,
) -> list[str]:
	"""Select weather tags that align with the season and still look plausible."""

	if season == "summer":
		if usage_bias >= 0.7:
			return ["full_sun"]
		return ["full_sun", "light_rain"] if randomizer.random() < 0.18 else ["partial_sun_cloud"]
	if season == "autumn":
		return ["cloudy_overcast", "light_rain"] if randomizer.random() < 0.45 else ["foggy_misty"]
	if season == "winter":
		return ["moderate_rain"] if randomizer.random() < 0.4 else ["cloudy_overcast", "light_rain"]
	return ["partial_sun_cloud"] if randomizer.random() < 0.45 else ["full_sun"]


def _season_for_new_zealand(*, date_value: date) -> str:
	"""Resolve a southern-hemisphere season key from a date."""

	month = date_value.month
	if month in {9, 10, 11}:
		return "spring"
	if month in {12, 1, 2}:
		return "summer"
	if month in {3, 4, 5}:
		return "autumn"
	return "winter"


def _build_audit_code(
	*,
	place_name: str,
	auditor_code: str,
	started_at: datetime,
	slot_key: str,
) -> str:
	"""Create a stable, readable audit code with enough variance for large seed volumes."""

	place_segment = "".join(character for character in place_name.upper() if character.isalnum())[:12]
	slot_segment = "".join(character for character in slot_key.upper() if character.isalnum())[:4] or "RUN"
	return f"{place_segment or 'PLAYSPACE'}-{auditor_code}-{started_at.strftime('%Y%m%d%H%M')}-{slot_segment}"


def _resolve_project_id(*, blueprint: ProjectBlueprint) -> uuid.UUID:
	"""Keep the original project IDs for the legacy demo projects and generate the rest."""

	if blueprint.key == "urban_usability_2026":
		return DEMO_PROJECT_URBAN_ID
	if blueprint.key == "south_region_pilot":
		return DEMO_PROJECT_SOUTH_ID
	return _stable_uuid("playspace-project", blueprint.key)


def _email_from_name(full_name: str) -> str:
	"""Create a deterministic example email from a human name."""

	slug = "".join(character.lower() if character.isalnum() else "." for character in full_name.strip())
	normalized_slug = ".".join(part for part in slug.split(".") if part)
	return f"{normalized_slug}@example.org"


def _placeholder_password_hash(label: str) -> str:
	"""Return the shared demo password hash used by seeded Playspace accounts."""

	_ = label
	return hash_password("DemoPass123!")


def _stable_uuid(*parts: str) -> uuid.UUID:
	"""Create a deterministic UUID from stable seed labels."""

	return uuid.uuid5(PLAYSPACE_SEED_NAMESPACE, "::".join(parts))


def _option_rating(*, option: ScoringScaleOption) -> float:
	"""Estimate how positive an option is so weighted choices can follow the place bias."""

	if option.key == "not_applicable":
		return 0.0
	return option.addition_value + option.boost_value


def _bounded_bias(value: float) -> float:
	"""Clamp a floating-point bias into the expected 0..1 range."""

	return max(0.05, min(value, 0.95))

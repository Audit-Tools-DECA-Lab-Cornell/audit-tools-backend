"""Integration coverage for the full Playspace FastAPI route surface."""

from __future__ import annotations

import uuid

from conftest import PlayspaceSeedSnapshot
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from app.main import app


def _manager_headers(account_id: str) -> dict[str, str]:
    """Build manager auth headers for dummy route authorization."""

    return {
        "x-demo-role": "manager",
        "x-demo-account-id": account_id,
    }


def _admin_headers() -> dict[str, str]:
    """Build administrator auth headers for dummy route authorization."""

    return {
        "x-demo-role": "admin",
    }


def _auditor_headers(snapshot: PlayspaceSeedSnapshot) -> dict[str, str]:
    """Build seeded auditor auth headers for dummy route authorization."""

    return {
        "x-demo-role": "auditor",
        "x-demo-account-id": snapshot.seeded_auditor_account_id,
        "x-demo-auditor-code": snapshot.seeded_auditor_code,
    }


def _route_inventory() -> set[tuple[str, str]]:
    """Collect the concrete Playspace route methods and paths from the app."""

    inventory: set[tuple[str, str]] = set()
    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        if not route.path.startswith("/playspace"):
            continue
        for method in route.methods:
            if method in {"HEAD", "OPTIONS"}:
                continue
            inventory.add((method, route.path))
    return inventory


def _unique_suffix() -> str:
    """Create a short unique suffix for ephemeral test resource names."""

    return uuid.uuid4().hex[:8]


def _create_project(client: TestClient, account_id: str, *, suffix: str) -> dict[str, object]:
    """Create an ephemeral project through the Playspace management API."""

    response = client.post(
        "/playspace/projects",
        headers=_manager_headers(account_id),
        json={
            "name": f"Endpoint Project {suffix}",
            "overview": f"Endpoint project {suffix}",
            "place_types": ["public playspace"],
            "start_date": "2026-01-10",
            "end_date": "2026-12-20",
            "est_places": 1,
            "est_auditors": 1,
            "auditor_description": f"Endpoint guidance {suffix}",
        },
    )
    assert response.status_code == 201
    return response.json()


def _create_place(
    client: TestClient,
    account_id: str,
    *,
    project_id: str,
    suffix: str,
) -> dict[str, object]:
    """Create an ephemeral place linked to one project."""

    response = client.post(
        "/playspace/places",
        headers=_manager_headers(account_id),
        json={
            "project_ids": [project_id],
            "name": f"Endpoint Place {suffix}",
            "city": "Auckland",
            "province": "Auckland",
            "country": "New Zealand",
            "place_type": "public playspace",
            "lat": -36.85,
            "lng": 174.76,
            "start_date": "2026-02-01",
            "end_date": "2026-11-30",
            "est_auditors": 1,
            "auditor_description": f"Endpoint place guidance {suffix}",
        },
    )
    assert response.status_code == 201
    return response.json()


def _create_auditor_profile(
    client: TestClient,
    account_id: str,
    *,
    suffix: str,
) -> dict[str, object]:
    """Create an ephemeral auditor profile through the Playspace management API."""

    response = client.post(
        "/playspace/auditor-profiles",
        headers=_manager_headers(account_id),
        json={
            "email": f"endpoint-{suffix}@example.org",
            "full_name": f"Endpoint Auditor {suffix}",
            "auditor_code": f"EPT-{suffix.upper()}",
            "country": "New Zealand",
            "role": "Tester",
        },
    )
    assert response.status_code == 201
    return response.json()


def _create_assigned_audit_context(
    client: TestClient,
    seed_snapshot: PlayspaceSeedSnapshot,
    *,
    suffix: str,
) -> tuple[dict[str, object], dict[str, object], dict[str, object], dict[str, str]]:
    """Create a project-place-auditor trio and assign the auditor to the place."""

    manager_headers = _manager_headers(seed_snapshot.manager_account_id)
    project = _create_project(
        client,
        seed_snapshot.manager_account_id,
        suffix=suffix,
    )
    place = _create_place(
        client,
        seed_snapshot.manager_account_id,
        project_id=str(project["id"]),
        suffix=suffix,
    )
    auditor_profile = _create_auditor_profile(
        client,
        seed_snapshot.manager_account_id,
        suffix=suffix,
    )

    assignment_response = client.post(
        f"/playspace/auditor-profiles/{auditor_profile['id']}/assignments",
        headers=manager_headers,
        json={
            "project_id": project["id"],
            "place_id": place["id"],
        },
    )
    assert assignment_response.status_code == 201

    auditor_headers = {
        "x-demo-role": "auditor",
        "x-demo-account-id": auditor_profile["account_id"],
        "x-demo-auditor-code": auditor_profile["auditor_code"],
    }
    return project, place, auditor_profile, auditor_headers


def test_playspace_route_inventory_matches_expected_surface() -> None:
    """Keep the endpoint coverage suite aligned with the real Playspace route tree."""

    expected_routes = {
        ("POST", "/playspace/auth/signup"),
        ("POST", "/playspace/auth/login"),
        ("GET", "/playspace/accounts/{account_id}"),
        ("GET", "/playspace/accounts/{account_id}/manager-profiles"),
        ("GET", "/playspace/accounts/{account_id}/projects"),
        ("GET", "/playspace/accounts/{account_id}/auditors"),
        ("GET", "/playspace/accounts/{account_id}/places"),
        ("GET", "/playspace/accounts/{account_id}/audits"),
        ("GET", "/playspace/projects/{project_id}"),
        ("GET", "/playspace/projects/{project_id}/stats"),
        ("GET", "/playspace/projects/{project_id}/places"),
        ("GET", "/playspace/places/{place_id}/audits"),
        ("GET", "/playspace/places/{place_id}/history"),
        ("GET", "/playspace/auditor-profiles/{auditor_profile_id}/assignments"),
        ("POST", "/playspace/auditor-profiles/{auditor_profile_id}/assignments"),
        ("PATCH", "/playspace/auditor-profiles/{auditor_profile_id}/assignments/{assignment_id}"),
        ("DELETE", "/playspace/auditor-profiles/{auditor_profile_id}/assignments/{assignment_id}"),
        ("POST", "/playspace/places/{place_id}/audits/access"),
        ("GET", "/playspace/audits/{audit_id}"),
        ("PATCH", "/playspace/audits/{audit_id}/draft"),
        ("PATCH", "/playspace/places/{place_id}/audits/draft"),
        ("POST", "/playspace/audits/{audit_id}/submit"),
        ("GET", "/playspace/auditor/me/places"),
        ("GET", "/playspace/auditor/me/audits"),
        ("GET", "/playspace/auditor/me/dashboard-summary"),
        ("GET", "/playspace/admin/overview"),
        ("GET", "/playspace/admin/accounts"),
        ("GET", "/playspace/admin/projects"),
        ("GET", "/playspace/admin/places"),
        ("GET", "/playspace/admin/auditors"),
        ("GET", "/playspace/admin/audits"),
        ("GET", "/playspace/admin/system"),
        ("GET", "/playspace/me"),
        ("GET", "/playspace/me/auditor-profile"),
        ("GET", "/playspace/instrument"),
        ("PATCH", "/playspace/accounts/{account_id}"),
        ("POST", "/playspace/projects"),
        ("PATCH", "/playspace/projects/{project_id}"),
        ("DELETE", "/playspace/projects/{project_id}"),
        ("POST", "/playspace/places"),
        ("PATCH", "/playspace/places/{place_id}"),
        ("DELETE", "/playspace/places/{place_id}"),
        ("POST", "/playspace/auditor-profiles"),
        ("PATCH", "/playspace/auditor-profiles/{auditor_profile_id}"),
        ("DELETE", "/playspace/auditor-profiles/{auditor_profile_id}"),
    }

    assert _route_inventory() == expected_routes


def test_auth_self_service_and_instrument_endpoints(
    playspace_client: TestClient,
    playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
    """Exercise auth, self-service, and instrument metadata endpoints."""

    signup_response = playspace_client.post(
        "/playspace/auth/signup",
        json={
            "email": "signup-playspace@example.org",
            "password": "not-used",
            "name": "Signup User",
            "account_type": "MANAGER",
        },
    )
    assert signup_response.status_code == 201
    assert signup_response.json()["user"]["account_type"] == "MANAGER"

    login_response = playspace_client.post(
        "/playspace/auth/login",
        json={
            "email": "manager@example.org",
            "password": "not-used",
        },
    )
    assert login_response.status_code == 200
    assert login_response.json()["user"]["email"] == "manager@example.org"

    me_response = playspace_client.get(
        "/playspace/me",
        headers=_auditor_headers(playspace_seed_snapshot),
    )
    assert me_response.status_code == 200
    assert me_response.json()["account_id"] == playspace_seed_snapshot.seeded_auditor_account_id

    profile_response = playspace_client.get(
        "/playspace/me/auditor-profile",
        headers=_auditor_headers(playspace_seed_snapshot),
    )
    assert profile_response.status_code == 200
    assert (
        profile_response.json()["profile_id"] == playspace_seed_snapshot.seeded_auditor_profile_id
    )

    instrument_response = playspace_client.get(
        "/playspace/instrument",
        headers=_manager_headers(playspace_seed_snapshot.manager_account_id),
    )
    assert instrument_response.status_code == 200
    assert instrument_response.json()["instrument_key"] == "pvua_v5_2"


def test_manager_dashboard_endpoints(
    playspace_client: TestClient,
    playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
    """Exercise every manager-facing Playspace dashboard endpoint."""

    headers = _manager_headers(playspace_seed_snapshot.manager_account_id)
    account_id = playspace_seed_snapshot.manager_account_id
    project_id = playspace_seed_snapshot.urban_project_id
    place_id = playspace_seed_snapshot.riverside_place_id

    account_response = playspace_client.get(f"/playspace/accounts/{account_id}", headers=headers)
    assert account_response.status_code == 200
    assert account_response.json()["id"] == account_id

    manager_profiles_response = playspace_client.get(
        f"/playspace/accounts/{account_id}/manager-profiles",
        headers=headers,
    )
    assert manager_profiles_response.status_code == 200
    assert len(manager_profiles_response.json()) >= 1

    projects_response = playspace_client.get(
        f"/playspace/accounts/{account_id}/projects",
        headers=headers,
    )
    assert projects_response.status_code == 200
    assert len(projects_response.json()) >= 1

    auditors_response = playspace_client.get(
        f"/playspace/accounts/{account_id}/auditors",
        headers=headers,
    )
    assert auditors_response.status_code == 200
    assert len(auditors_response.json()) >= 1

    places_response = playspace_client.get(
        f"/playspace/accounts/{account_id}/places",
        headers=headers,
    )
    assert places_response.status_code == 200
    assert len(places_response.json()["items"]) >= 1

    audits_response = playspace_client.get(
        f"/playspace/accounts/{account_id}/audits",
        headers=headers,
    )
    assert audits_response.status_code == 200
    assert len(audits_response.json()["items"]) >= 1

    project_detail_response = playspace_client.get(
        f"/playspace/projects/{project_id}",
        headers=headers,
    )
    assert project_detail_response.status_code == 200
    assert project_detail_response.json()["id"] == project_id

    project_stats_response = playspace_client.get(
        f"/playspace/projects/{project_id}/stats",
        headers=headers,
    )
    assert project_stats_response.status_code == 200
    assert project_stats_response.json()["project_id"] == project_id

    project_places_response = playspace_client.get(
        f"/playspace/projects/{project_id}/places",
        headers=headers,
    )
    assert project_places_response.status_code == 200
    assert len(project_places_response.json()) >= 1

    place_audits_response = playspace_client.get(
        f"/playspace/places/{place_id}/audits",
        headers=headers,
        params={"project_id": project_id},
    )
    assert place_audits_response.status_code == 200
    assert len(place_audits_response.json()) >= 1

    place_history_response = playspace_client.get(
        f"/playspace/places/{place_id}/history",
        headers=headers,
        params={"project_id": project_id},
    )
    assert place_history_response.status_code == 200
    assert place_history_response.json()["project_id"] == project_id


def test_admin_dashboard_endpoints(
    playspace_client: TestClient,
) -> None:
    """Exercise every admin-facing Playspace dashboard endpoint."""

    headers = _admin_headers()

    overview_response = playspace_client.get("/playspace/admin/overview", headers=headers)
    assert overview_response.status_code == 200
    assert overview_response.json()["total_projects"] >= 1

    accounts_response = playspace_client.get("/playspace/admin/accounts", headers=headers)
    assert accounts_response.status_code == 200
    assert len(accounts_response.json()["items"]) >= 1

    projects_response = playspace_client.get("/playspace/admin/projects", headers=headers)
    assert projects_response.status_code == 200
    assert len(projects_response.json()["items"]) >= 1

    places_response = playspace_client.get("/playspace/admin/places", headers=headers)
    assert places_response.status_code == 200
    assert len(places_response.json()["items"]) >= 1

    auditors_response = playspace_client.get("/playspace/admin/auditors", headers=headers)
    assert auditors_response.status_code == 200
    assert len(auditors_response.json()["items"]) >= 1

    audits_response = playspace_client.get("/playspace/admin/audits", headers=headers)
    assert audits_response.status_code == 200
    assert len(audits_response.json()["items"]) >= 1

    system_response = playspace_client.get("/playspace/admin/system", headers=headers)
    assert system_response.status_code == 200
    assert system_response.json()["instrument_key"] == "pvua_v5_2"


def test_auditor_dashboard_endpoints(
    playspace_client: TestClient,
    playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
    """Exercise every auditor-facing Playspace dashboard endpoint."""

    headers = _auditor_headers(playspace_seed_snapshot)

    places_response = playspace_client.get("/playspace/auditor/me/places", headers=headers)
    assert places_response.status_code == 200
    assert len(places_response.json()["items"]) >= 1

    audits_response = playspace_client.get("/playspace/auditor/me/audits", headers=headers)
    assert audits_response.status_code == 200
    assert len(audits_response.json()["items"]) >= 1

    summary_response = playspace_client.get(
        "/playspace/auditor/me/dashboard-summary",
        headers=headers,
    )
    assert summary_response.status_code == 200
    assert summary_response.json()["total_assigned_places"] >= 1


def test_management_endpoints_cover_account_project_place_and_auditor_crud(
    playspace_client: TestClient,
    playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
    """Exercise every Playspace management endpoint."""

    headers = _manager_headers(playspace_seed_snapshot.manager_account_id)
    suffix = _unique_suffix()

    account_response = playspace_client.patch(
        f"/playspace/accounts/{playspace_seed_snapshot.manager_account_id}",
        headers=headers,
        json={"name": f"Updated Manager Account {suffix}"},
    )
    assert account_response.status_code == 200
    assert account_response.json()["id"] == playspace_seed_snapshot.manager_account_id

    project = _create_project(
        playspace_client,
        playspace_seed_snapshot.manager_account_id,
        suffix=suffix,
    )

    update_project_response = playspace_client.patch(
        f"/playspace/projects/{project['id']}",
        headers=headers,
        json={
            "overview": f"Updated project overview {suffix}",
            "place_types": ["public playspace", "school playspace"],
        },
    )
    assert update_project_response.status_code == 200
    assert "school playspace" in update_project_response.json()["place_types"]

    place = _create_place(
        playspace_client,
        playspace_seed_snapshot.manager_account_id,
        project_id=str(project["id"]),
        suffix=suffix,
    )

    update_place_response = playspace_client.patch(
        f"/playspace/places/{place['id']}",
        headers=headers,
        json={
            "name": f"Updated Endpoint Place {suffix}",
            "project_ids": [project["id"]],
            "country": "New Zealand",
        },
    )
    assert update_place_response.status_code == 200
    assert update_place_response.json()["id"] == place["id"]

    auditor_profile = _create_auditor_profile(
        playspace_client,
        playspace_seed_snapshot.manager_account_id,
        suffix=suffix,
    )

    update_profile_response = playspace_client.patch(
        f"/playspace/auditor-profiles/{auditor_profile['id']}",
        headers=headers,
        json={
            "country": "New Zealand",
            "role": f"Updated Role {suffix}",
        },
    )
    assert update_profile_response.status_code == 200
    assert update_profile_response.json()["id"] == auditor_profile["id"]

    delete_place_response = playspace_client.delete(
        f"/playspace/places/{place['id']}",
        headers=headers,
    )
    assert delete_place_response.status_code == 204

    delete_project_response = playspace_client.delete(
        f"/playspace/projects/{project['id']}",
        headers=headers,
    )
    assert delete_project_response.status_code == 204

    delete_profile_response = playspace_client.delete(
        f"/playspace/auditor-profiles/{auditor_profile['id']}",
        headers=headers,
    )
    assert delete_profile_response.status_code == 204


def test_assignment_endpoints_cover_project_and_place_scopes(
    playspace_client: TestClient,
    playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
    """Exercise list/create/update/delete assignment routes."""

    suffix = _unique_suffix()
    manager_headers = _manager_headers(playspace_seed_snapshot.manager_account_id)
    project = _create_project(
        playspace_client,
        playspace_seed_snapshot.manager_account_id,
        suffix=suffix,
    )
    place = _create_place(
        playspace_client,
        playspace_seed_snapshot.manager_account_id,
        project_id=str(project["id"]),
        suffix=suffix,
    )
    auditor_profile = _create_auditor_profile(
        playspace_client,
        playspace_seed_snapshot.manager_account_id,
        suffix=suffix,
    )

    list_empty_response = playspace_client.get(
        f"/playspace/auditor-profiles/{auditor_profile['id']}/assignments",
        headers=manager_headers,
    )
    assert list_empty_response.status_code == 200
    assert list_empty_response.json() == []

    create_assignment_response = playspace_client.post(
        f"/playspace/auditor-profiles/{auditor_profile['id']}/assignments",
        headers=manager_headers,
        json={
            "project_id": project["id"],
            "place_id": None,
        },
    )
    assert create_assignment_response.status_code == 201
    assignment = create_assignment_response.json()
    assert assignment["scope_type"] == "project"

    duplicate_assignment_response = playspace_client.post(
        f"/playspace/auditor-profiles/{auditor_profile['id']}/assignments",
        headers=manager_headers,
        json={
            "project_id": project["id"],
            "place_id": None,
        },
    )
    assert duplicate_assignment_response.status_code == 409
    assert "already exists" in duplicate_assignment_response.json()["detail"]

    update_assignment_response = playspace_client.patch(
        f"/playspace/auditor-profiles/{auditor_profile['id']}/assignments/{assignment['id']}",
        headers=manager_headers,
        json={
            "project_id": project["id"],
            "place_id": place["id"],
        },
    )
    assert update_assignment_response.status_code == 200
    assert update_assignment_response.json()["scope_type"] == "place"

    list_after_update_response = playspace_client.get(
        f"/playspace/auditor-profiles/{auditor_profile['id']}/assignments",
        headers=manager_headers,
    )
    assert list_after_update_response.status_code == 200
    assert len(list_after_update_response.json()) == 1

    delete_assignment_response = playspace_client.delete(
        f"/playspace/auditor-profiles/{auditor_profile['id']}/assignments/{assignment['id']}",
        headers=manager_headers,
    )
    assert delete_assignment_response.status_code == 204


def test_audit_execution_endpoints_cover_access_read_patch_and_submit(
    playspace_client: TestClient,
    playspace_seed_snapshot: PlayspaceSeedSnapshot,
) -> None:
    """Exercise the full Playspace audit execution route set."""

    suffix = _unique_suffix()
    manager_headers = _manager_headers(playspace_seed_snapshot.manager_account_id)
    project = _create_project(
        playspace_client,
        playspace_seed_snapshot.manager_account_id,
        suffix=suffix,
    )
    place = _create_place(
        playspace_client,
        playspace_seed_snapshot.manager_account_id,
        project_id=str(project["id"]),
        suffix=suffix,
    )
    auditor_profile = _create_auditor_profile(
        playspace_client,
        playspace_seed_snapshot.manager_account_id,
        suffix=suffix,
    )

    assignment_response = playspace_client.post(
        f"/playspace/auditor-profiles/{auditor_profile['id']}/assignments",
        headers=manager_headers,
        json={
            "project_id": project["id"],
            "place_id": place["id"],
        },
    )
    assert assignment_response.status_code == 201

    auditor_headers = {
        "x-demo-role": "auditor",
        "x-demo-account-id": auditor_profile["account_id"],
        "x-demo-auditor-code": auditor_profile["auditor_code"],
    }

    access_response = playspace_client.post(
        f"/playspace/places/{place['id']}/audits/access",
        headers=auditor_headers,
        json={
            "project_id": project["id"],
            "execution_mode": "audit",
        },
    )
    assert access_response.status_code == 200
    audit_session = access_response.json()
    audit_id = audit_session["audit_id"]
    assert audit_session["project_id"] == project["id"]

    get_audit_response = playspace_client.get(
        f"/playspace/audits/{audit_id}",
        headers=auditor_headers,
    )
    assert get_audit_response.status_code == 200
    assert get_audit_response.json()["audit_id"] == audit_id

    patch_draft_response = playspace_client.patch(
        f"/playspace/audits/{audit_id}/draft",
        headers=auditor_headers,
        json={
            "meta": {"execution_mode": "survey"},
            "pre_audit": {"season": "summer"},
        },
    )
    assert patch_draft_response.status_code == 200
    assert patch_draft_response.json()["audit_id"] == audit_id

    patch_place_draft_response = playspace_client.patch(
        f"/playspace/places/{place['id']}/audits/draft",
        headers=auditor_headers,
        params={"project_id": project["id"]},
        json={
            "meta": {"execution_mode": "both"},
            "pre_audit": {"season": "summer", "place_size": "medium"},
        },
    )
    assert patch_place_draft_response.status_code == 200
    assert patch_place_draft_response.json()["audit_id"] == audit_id

    submit_response = playspace_client.post(
        f"/playspace/audits/{audit_id}/submit",
        headers=auditor_headers,
    )
    assert submit_response.status_code == 400


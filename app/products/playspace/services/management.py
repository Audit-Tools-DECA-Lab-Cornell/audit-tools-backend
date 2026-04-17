"""
Manager/admin write-path service for Playspace dashboard workflows.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actors import (
    CurrentUserContext,
    CurrentUserRole,
    require_manager_or_admin_user,
)
from app.models import (
    Account,
    AccountType,
    AuditorProfile,
    Place,
    Project,
    ProjectPlace,
    User,
)
from app.products.playspace.schemas import (
    AccountManagementResponse,
    AccountUpdateRequest,
    AuditorProfileCreateRequest,
    AuditorProfileDetailResponse,
    AuditorProfileUpdateRequest,
    PlaceCreateRequest,
    PlaceDetailResponse,
    PlaceUpdateRequest,
    ProjectCreateRequest,
    ProjectDetailResponse,
    ProjectUpdateRequest,
)
from app.products.playspace.services.privacy import mask_email
from app.auth_security import hash_password


class PlayspaceManagementService:
    """Write operations for manager/admin dashboard workflows."""

    def __init__(self, session: AsyncSession):
        self._session = session

    def _require_manager_or_admin(self, actor: CurrentUserContext) -> None:
        """Guard write endpoints to manager/admin actors."""

        require_manager_or_admin_user(actor)

    def _resolve_target_account_id(
        self,
        *,
        actor: CurrentUserContext,
        requested_account_id: uuid.UUID | None,
    ) -> uuid.UUID:
        """Resolve target account id based on actor role."""

        if actor.role is CurrentUserRole.ADMIN:
            if requested_account_id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="account_id is required for admin project creation.",
                )
            return requested_account_id

        if actor.account_id is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Manager account context is required.",
            )
        if requested_account_id is not None and requested_account_id != actor.account_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Managers can only create records in their own account.",
            )
        return actor.account_id

    def _ensure_account_access(self, *, actor: CurrentUserContext, account_id: uuid.UUID) -> None:
        """Ensure actor can mutate resources under target account."""

        if actor.role is CurrentUserRole.ADMIN:
            return
        if actor.account_id != account_id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This actor cannot modify resources in the requested account.",
            )

    async def _get_account(self, account_id: uuid.UUID) -> Account:
        """Load an account or raise 404."""

        account_result = await self._session.execute(
            select(Account).where(Account.id == account_id)
        )
        account = account_result.scalar_one_or_none()
        if account is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Account not found.")
        return account

    async def _get_project(self, project_id: uuid.UUID) -> Project:
        """Load a project or raise 404."""

        project_result = await self._session.execute(
            select(Project).where(Project.id == project_id)
        )
        project = project_result.scalar_one_or_none()
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Project not found.")
        return project

    async def _get_place(self, place_id: uuid.UUID) -> Place:
        """Load a place or raise 404."""

        place_result = await self._session.execute(select(Place).where(Place.id == place_id))
        place = place_result.scalar_one_or_none()
        if place is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Place not found.")
        return place

    async def _get_auditor_profile(self, auditor_profile_id: uuid.UUID) -> AuditorProfile:
        """Load an auditor profile or raise 404."""

        profile_result = await self._session.execute(
            select(AuditorProfile).where(AuditorProfile.id == auditor_profile_id)
        )
        profile = profile_result.scalar_one_or_none()
        if profile is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Auditor profile not found.",
            )
        return profile

    @staticmethod
    def _serialize_account(account: Account) -> AccountManagementResponse:
        """Serialize a privacy-safe account payload."""

        return AccountManagementResponse(
            id=account.id,
            name=account.name,
            email_masked=mask_email(account.email),
            account_type=account.account_type,
            created_at=account.created_at,
        )

    @staticmethod
    def _serialize_project(project: Project) -> ProjectDetailResponse:
        """Serialize a project detail payload."""

        return ProjectDetailResponse(
            id=project.id,
            account_id=project.account_id,
            name=project.name,
            overview=project.overview,
            place_types=list(project.place_types),
            start_date=project.start_date,
            end_date=project.end_date,
            est_places=project.est_places,
            est_auditors=project.est_auditors,
            auditor_description=project.auditor_description,
            created_at=project.created_at,
        )

    @staticmethod
    def _serialize_place(place: Place, projects: list[Project]) -> PlaceDetailResponse:
        """Serialize a place detail payload."""

        return PlaceDetailResponse(
            id=place.id,
            project_ids=[project.id for project in projects],
            project_names=[project.name for project in projects],
            name=place.name,
            city=place.city,
            province=place.province,
            country=place.country,
            place_type=place.place_type,
            lat=place.lat,
            lng=place.lng,
            start_date=place.start_date,
            end_date=place.end_date,
            est_auditors=place.est_auditors,
            auditor_description=place.auditor_description,
            created_at=place.created_at,
        )

    async def _get_projects_for_place(self, place_id: uuid.UUID) -> list[Project]:
        """Load all linked projects for one place in stable display order."""

        project_result = await self._session.execute(
            select(Project)
            .join(ProjectPlace, Project.id == ProjectPlace.project_id)
            .where(ProjectPlace.place_id == place_id)
            .order_by(Project.name.asc(), Project.id.asc())
        )
        return list(project_result.scalars().all())

    async def _validate_project_ids(self, project_ids: list[uuid.UUID]) -> list[Project]:
        """Load requested projects, requiring at least one and a shared owning account."""

        normalized_project_ids = list(dict.fromkeys(project_ids))
        if not normalized_project_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="At least one project_id is required for a place.",
            )

        project_result = await self._session.execute(
            select(Project).where(Project.id.in_(normalized_project_ids))
        )
        project_by_id = {project.id: project for project in project_result.scalars().all()}
        ordered_projects: list[Project] = []
        for project_id in normalized_project_ids:
            project = project_by_id.get(project_id)
            if project is None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Project not found.",
                )
            ordered_projects.append(project)

        account_ids = {project.account_id for project in ordered_projects}
        if len(account_ids) != 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="All projects linked to one place must belong to the same account.",
            )

        return ordered_projects

    @staticmethod
    def _serialize_auditor_profile(
        profile: AuditorProfile,
    ) -> AuditorProfileDetailResponse:
        """Serialize a privacy-safe auditor profile payload."""

        return AuditorProfileDetailResponse(
            id=profile.id,
            account_id=profile.account_id,
            auditor_code=profile.auditor_code,
            email_masked=mask_email(profile.email),
            age_range=profile.age_range,
            gender=profile.gender,
            country=profile.country,
            role=profile.role,
            created_at=profile.created_at,
        )

    async def create_project(
        self,
        *,
        actor: CurrentUserContext,
        payload: ProjectCreateRequest,
    ) -> ProjectDetailResponse:
        """Create one project row."""

        self._require_manager_or_admin(actor)
        account_id = self._resolve_target_account_id(
            actor=actor,
            requested_account_id=payload.account_id,
        )
        await self._get_account(account_id)
        project = Project(
            account_id=account_id,
            name=payload.name,
            overview=payload.overview,
            place_types=payload.place_types,
            start_date=payload.start_date,
            end_date=payload.end_date,
            est_places=payload.est_places,
            est_auditors=payload.est_auditors,
            auditor_description=payload.auditor_description,
        )
        self._session.add(project)
        await self._session.commit()
        await self._session.refresh(project)
        return self._serialize_project(project)

    async def update_account(
        self,
        *,
        actor: CurrentUserContext,
        account_id: uuid.UUID,
        payload: AccountUpdateRequest,
    ) -> AccountManagementResponse:
        """Update one account row."""

        self._require_manager_or_admin(actor)
        account = await self._get_account(account_id)
        self._ensure_account_access(actor=actor, account_id=account.id)

        updates = payload.model_dump(exclude_unset=True)
        next_email = updates.get("email")
        if isinstance(next_email, str) and next_email != account.email:
            duplicate_account_query = await self._session.execute(
                select(Account).where(Account.email == next_email, Account.id != account.id)
            )
            if duplicate_account_query.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Email is already in use by another account.",
                )

        for key, value in updates.items():
            setattr(account, key, value)

        await self._session.commit()
        await self._session.refresh(account)
        return self._serialize_account(account)

    async def update_project(
        self,
        *,
        actor: CurrentUserContext,
        project_id: uuid.UUID,
        payload: ProjectUpdateRequest,
    ) -> ProjectDetailResponse:
        """Update one project row."""

        self._require_manager_or_admin(actor)
        project = await self._get_project(project_id)
        self._ensure_account_access(actor=actor, account_id=project.account_id)
        updates = payload.model_dump(exclude_unset=True)
        for key, value in updates.items():
            if key == "place_types" and value is None:
                continue
            setattr(project, key, value)
        await self._session.commit()
        await self._session.refresh(project)
        return self._serialize_project(project)

    async def delete_project(
        self,
        *,
        actor: CurrentUserContext,
        project_id: uuid.UUID,
    ) -> None:
        """Delete one project row."""

        self._require_manager_or_admin(actor)
        project = await self._get_project(project_id)
        self._ensure_account_access(actor=actor, account_id=project.account_id)
        await self._session.delete(project)
        await self._session.commit()

    async def create_place(
        self,
        *,
        actor: CurrentUserContext,
        payload: PlaceCreateRequest,
    ) -> PlaceDetailResponse:
        """Create one place row and link it to one or more projects."""

        self._require_manager_or_admin(actor)
        projects = await self._validate_project_ids(payload.project_ids)
        self._ensure_account_access(actor=actor, account_id=projects[0].account_id)
        place = Place(
            name=payload.name,
            city=payload.city,
            province=payload.province,
            country=payload.country,
            place_type=payload.place_type,
            lat=payload.lat,
            lng=payload.lng,
            start_date=payload.start_date,
            end_date=payload.end_date,
            est_auditors=payload.est_auditors,
            auditor_description=payload.auditor_description,
        )
        self._session.add(place)
        await self._session.flush()
        for project in projects:
            self._session.add(ProjectPlace(project_id=project.id, place_id=place.id))
        await self._session.commit()
        await self._session.refresh(place)
        return self._serialize_place(place, projects)

    async def update_place(
        self,
        *,
        actor: CurrentUserContext,
        place_id: uuid.UUID,
        payload: PlaceUpdateRequest,
    ) -> PlaceDetailResponse:
        """Update one place row."""

        self._require_manager_or_admin(actor)
        place = await self._get_place(place_id)
        current_projects = await self._get_projects_for_place(place.id)
        if not current_projects:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The place is not linked to any project.",
            )
        self._ensure_account_access(actor=actor, account_id=current_projects[0].account_id)
        updates = payload.model_dump(exclude_unset=True)
        next_project_ids = updates.pop("project_ids", None)
        for key, value in updates.items():
            setattr(place, key, value)

        response_projects = current_projects
        if next_project_ids is not None:
            response_projects = await self._validate_project_ids(next_project_ids)
            self._ensure_account_access(actor=actor, account_id=response_projects[0].account_id)
            link_result = await self._session.execute(
                select(ProjectPlace).where(ProjectPlace.place_id == place.id)
            )
            existing_links = link_result.scalars().all()
            existing_project_ids = {link.project_id for link in existing_links}
            next_project_id_set = {project.id for project in response_projects}
            for existing_link in existing_links:
                if existing_link.project_id not in next_project_id_set:
                    await self._session.delete(existing_link)
            for project in response_projects:
                if project.id not in existing_project_ids:
                    self._session.add(ProjectPlace(project_id=project.id, place_id=place.id))

        await self._session.commit()
        await self._session.refresh(place)
        return self._serialize_place(place, response_projects)

    async def delete_place(
        self,
        *,
        actor: CurrentUserContext,
        place_id: uuid.UUID,
    ) -> None:
        """Delete one place row."""

        self._require_manager_or_admin(actor)
        place = await self._get_place(place_id)
        linked_projects = await self._get_projects_for_place(place.id)
        if not linked_projects:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="The place is not linked to any project.",
            )
        self._ensure_account_access(actor=actor, account_id=linked_projects[0].account_id)
        await self._session.delete(place)
        await self._session.commit()

    async def create_auditor_profile(
        self,
        *,
        actor: CurrentUserContext,
        payload: AuditorProfileCreateRequest,
    ) -> AuditorProfileDetailResponse:
        """Create one auditor account + profile pair."""

        self._require_manager_or_admin(actor)
        duplicate_profile_query = await self._session.execute(
            select(AuditorProfile).where(
                or_(
                    AuditorProfile.auditor_code == payload.auditor_code,
                    AuditorProfile.email == payload.email,
                )
            )
        )
        duplicate_profile = duplicate_profile_query.scalar_one_or_none()
        if duplicate_profile is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="auditor_code or email is already in use.",
            )

        duplicate_account_query = await self._session.execute(
            select(Account).where(Account.email == payload.email)
        )
        if duplicate_account_query.scalar_one_or_none() is not None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Email is already in use by another account.",
            )

        account = Account(
            name=payload.full_name,
            email=payload.email,
            password_hash=hash_password("DemoPass123!"),
            account_type=AccountType.AUDITOR,
        )
        self._session.add(account)
        await self._session.flush()

        user = User(
            email=payload.email,
            password_hash=hash_password("DemoPass123!"),
            account_id=account.id,
            account_type=AccountType.AUDITOR,
            name=payload.full_name,
            email_verified=True,
            email_verified_at=datetime.now(timezone.utc),
            failed_login_attempts=0,
            approved=True,
            approved_at=datetime.now(timezone.utc),
            profile_completed=True,
            profile_completed_at=datetime.now(timezone.utc),
        )
        self._session.add(user)
        await self._session.flush()

        profile = AuditorProfile(
            account_id=account.id,
            user_id=user.id,
            auditor_code=payload.auditor_code,
            email=payload.email,
            full_name=payload.full_name,
            age_range=payload.age_range,
            gender=payload.gender,
            country=payload.country,
            role=payload.role,
        )
        self._session.add(profile)
        await self._session.commit()
        await self._session.refresh(profile)
        return self._serialize_auditor_profile(profile)

    async def update_auditor_profile(
        self,
        *,
        actor: CurrentUserContext,
        auditor_profile_id: uuid.UUID,
        payload: AuditorProfileUpdateRequest,
    ) -> AuditorProfileDetailResponse:
        """Update one auditor profile."""

        self._require_manager_or_admin(actor)
        profile = await self._get_auditor_profile(auditor_profile_id)

        updates = payload.model_dump(exclude_unset=True)
        if "email" in updates and updates["email"] is not None:
            duplicate_account_query = await self._session.execute(
                select(Account).where(
                    Account.email == updates["email"], Account.id != profile.account_id
                )
            )
            if duplicate_account_query.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Email is already in use by another account.",
                )
            duplicate_profile_query = await self._session.execute(
                select(AuditorProfile).where(
                    AuditorProfile.email == updates["email"],
                    AuditorProfile.id != profile.id,
                )
            )
            if duplicate_profile_query.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="Email is already in use by another auditor profile.",
                )

        if "auditor_code" in updates and updates["auditor_code"] is not None:
            duplicate_code_query = await self._session.execute(
                select(AuditorProfile).where(
                    AuditorProfile.auditor_code == updates["auditor_code"],
                    AuditorProfile.id != profile.id,
                )
            )
            if duplicate_code_query.scalar_one_or_none() is not None:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="auditor_code is already in use.",
                )

        account = await self._get_account(profile.account_id)
        for key, value in updates.items():
            if key == "email":
                profile.email = value
                if value is not None:
                    account.email = value
            elif key == "full_name":
                profile.full_name = value
                if value is not None:
                    account.name = value
            else:
                setattr(profile, key, value)

        await self._session.commit()
        await self._session.refresh(profile)
        return self._serialize_auditor_profile(profile)

    async def delete_auditor_profile(
        self,
        *,
        actor: CurrentUserContext,
        auditor_profile_id: uuid.UUID,
    ) -> None:
        """Delete one auditor profile and its underlying account."""

        self._require_manager_or_admin(actor)
        profile = await self._get_auditor_profile(auditor_profile_id)
        account = await self._get_account(profile.account_id)
        await self._session.delete(profile)
        await self._session.flush()
        await self._session.delete(account)
        await self._session.commit()

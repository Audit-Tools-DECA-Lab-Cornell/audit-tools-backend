"""
Strawberry GraphQL schema for the Audit Tools backend.

The current dashboard work is REST-first, but GraphQL remains available for
basic project and place exploration against the shared core models.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date

import strawberry
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from strawberry.exceptions import GraphQLError
from strawberry.types import Info

from app.models import Account, AuditorProfile, Place, Project


@dataclass(slots=True)
class GraphQLContext:
    """Per-request GraphQL context (currently only DB session)."""

    session: AsyncSession


@strawberry.type
class ProjectType:
    """GraphQL representation of the shared `Project` model."""

    id: uuid.UUID
    account_id: uuid.UUID
    name: str
    overview: str | None
    place_types: list[str]
    start_date: date | None
    end_date: date | None

    @staticmethod
    def from_model(model: Project) -> ProjectType:
        return ProjectType(
            id=model.id,
            account_id=model.account_id,
            name=model.name,
            overview=model.overview,
            place_types=list(model.place_types),
            start_date=model.start_date,
            end_date=model.end_date,
        )


@strawberry.type
class PlaceType:
    """GraphQL representation of the shared `Place` model."""

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    city: str | None
    province: str | None
    country: str | None
    place_type: str | None

    @staticmethod
    def from_model(model: Place) -> PlaceType:
        return PlaceType(
            id=model.id,
            project_id=model.project_id,
            name=model.name,
            city=model.city,
            province=model.province,
            country=model.country,
            place_type=model.place_type,
        )


@strawberry.type
class AuditorType:
    """GraphQL representation of the shared `AuditorProfile` model."""

    id: uuid.UUID
    account_id: uuid.UUID
    auditor_code: str
    full_name: str
    email: str | None
    role: str | None

    @staticmethod
    def from_model(model: AuditorProfile) -> AuditorType:
        return AuditorType(
            id=model.id,
            account_id=model.account_id,
            auditor_code=model.auditor_code,
            full_name=model.full_name,
            email=model.email,
            role=model.role,
        )


@strawberry.type
class Query:
    """Root GraphQL queries."""

    @strawberry.field
    async def places(
        self,
        info: Info[GraphQLContext, None],
        project_id: uuid.UUID | None = None,
    ) -> list[PlaceType]:
        """Fetch places, optionally filtered by project."""

        session = info.context.session
        stmt = select(Place).order_by(Place.name.asc())
        if project_id is not None:
            stmt = stmt.where(Place.project_id == project_id)

        result = await session.execute(stmt)
        models = result.scalars().all()
        return [PlaceType.from_model(model) for model in models]


@strawberry.type
class Mutation:
    """Root GraphQL mutations."""

    @strawberry.mutation
    async def create_project(
        self,
        info: Info[GraphQLContext, None],
        account_id: uuid.UUID,
        name: str,
        overview: str | None = None,
        place_types: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> ProjectType:
        """Create a project under an existing shared account."""

        project_name = name.strip()
        if not project_name:
            raise GraphQLError("name is required.")

        normalized_overview = overview.strip() if overview is not None else None
        if normalized_overview == "":
            normalized_overview = None

        if start_date is not None and end_date is not None and end_date < start_date:
            raise GraphQLError("end_date must be greater than or equal to start_date.")

        session = info.context.session
        account = await session.get(Account, account_id)
        if account is None:
            raise GraphQLError("Account not found.")

        project = Project(
            account_id=account_id,
            name=project_name,
            overview=normalized_overview,
            place_types=place_types or [],
            start_date=start_date,
            end_date=end_date,
        )
        session.add(project)

        try:
            await session.commit()
        except IntegrityError as err:
            await session.rollback()
            raise GraphQLError("Unable to create project due to a constraint violation.") from err

        await session.refresh(project)
        return ProjectType.from_model(project)

    @strawberry.mutation
    async def create_place(
        self,
        info: Info[GraphQLContext, None],
        project_id: uuid.UUID,
        name: str,
        city: str | None = None,
        province: str | None = None,
        country: str | None = None,
        place_type: str | None = None,
    ) -> PlaceType:
        """Create a place under an existing shared project."""

        place_name = name.strip()
        if not place_name:
            raise GraphQLError("name is required.")

        session = info.context.session
        project = await session.get(Project, project_id)
        if project is None:
            raise GraphQLError("Project not found.")

        place = Place(
            project_id=project_id,
            name=place_name,
            city=city.strip() if city is not None and city.strip() else None,
            province=province.strip() if province is not None and province.strip() else None,
            country=country.strip() if country is not None and country.strip() else None,
            place_type=(
                place_type.strip() if place_type is not None and place_type.strip() else None
            ),
        )
        session.add(place)

        try:
            await session.commit()
        except IntegrityError as err:
            await session.rollback()
            raise GraphQLError("Unable to create place due to a constraint violation.") from err

        await session.refresh(place)
        return PlaceType.from_model(place)


schema: strawberry.Schema = strawberry.Schema(query=Query, mutation=Mutation)

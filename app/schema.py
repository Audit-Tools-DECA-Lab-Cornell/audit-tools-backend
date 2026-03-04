"""
Strawberry GraphQL schema for the Audit Tools backend.

This file defines:
- GraphQL types: Project, Place, Auditor
- Query: fetch places (optionally filtered by project)
- Mutations: create a project, create a place
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

import strawberry
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from strawberry.exceptions import GraphQLError
from strawberry.types import Info

from app.models import Account, Auditor, Place, Project


@dataclass(slots=True)
class GraphQLContext:
    """Per-request GraphQL context (currently only DB session)."""

    session: AsyncSession


@strawberry.type
class ProjectType:
    """GraphQL representation of `Project`."""

    id: uuid.UUID
    account_id: uuid.UUID
    name: str
    start_date: datetime | None
    end_date: datetime | None
    description: str | None

    @staticmethod
    def from_model(model: Project) -> ProjectType:
        return ProjectType(
            id=model.id,
            account_id=model.account_id,
            name=model.name,
            start_date=model.start_date,
            end_date=model.end_date,
            description=model.description,
        )


@strawberry.type
class PlaceType:
    """GraphQL representation of `Place`."""

    id: uuid.UUID
    project_id: uuid.UUID
    name: str
    address: str
    notes: str | None

    @staticmethod
    def from_model(model: Place) -> PlaceType:
        return PlaceType(
            id=model.id,
            project_id=model.project_id,
            name=model.name,
            address=model.address,
            notes=model.notes,
        )


@strawberry.type
class AuditorType:
    """GraphQL representation of `Auditor`."""

    id: uuid.UUID
    account_id: uuid.UUID
    auditor_code: str
    user_id: uuid.UUID | None
    created_at: datetime

    @staticmethod
    def from_model(model: Auditor) -> AuditorType:
        return AuditorType(
            id=model.id,
            account_id=model.account_id,
            auditor_code=model.auditor_code,
            user_id=model.user_id,
            created_at=model.created_at,
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
        """
        Fetch places.

        - If `project_id` is provided, returns places for that project only.
        - Otherwise, returns all places.
        """

        session = info.context.session
        stmt = select(Place).order_by(Place.name.asc())
        if project_id is not None:
            stmt = stmt.where(Place.project_id == project_id)

        result = await session.execute(stmt)
        models = result.scalars().all()
        return [PlaceType.from_model(m) for m in models]


@strawberry.type
class Mutation:
    """Root GraphQL mutations."""

    @strawberry.mutation
    async def create_project(
        self,
        info: Info[GraphQLContext, None],
        account_id: uuid.UUID,
        name: str,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        description: str | None = None,
    ) -> ProjectType:
        """Create a project under an existing account."""

        project_name = name.strip()
        if not project_name:
            raise GraphQLError("name is required.")

        project_description = description.strip() if description is not None else None
        if project_description == "":
            project_description = None

        if start_date is not None and end_date is not None and end_date < start_date:
            raise GraphQLError("end_date must be greater than or equal to start_date.")

        session = info.context.session

        account = await session.get(Account, account_id)
        if account is None:
            raise GraphQLError("Account not found.")

        project = Project(
            account_id=account_id,
            name=project_name,
            start_date=start_date,
            end_date=end_date,
            description=project_description,
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
        address: str,
        notes: str | None = None,
    ) -> PlaceType:
        """Create a place under an existing project."""

        session = info.context.session

        place_name = name.strip()
        place_address = address.strip()
        if not place_name:
            raise GraphQLError("name is required.")
        if not place_address:
            raise GraphQLError("address is required.")

        project = await session.get(Project, project_id)
        if project is None:
            raise GraphQLError("Project not found.")

        place = Place(
            project_id=project_id,
            name=place_name,
            address=place_address,
            notes=notes.strip() if notes is not None and notes.strip() else None,
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

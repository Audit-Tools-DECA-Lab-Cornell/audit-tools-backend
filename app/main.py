"""
FastAPI application entrypoint.

This module initializes FastAPI and mounts the Strawberry GraphQL API.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from sqlalchemy.ext.asyncio import AsyncSession
from strawberry.fastapi import GraphQLRouter

from app.database import dispose_engine, get_async_session
from app.schema import GraphQLContext, schema


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """
    Application lifecycle handler.

    Disposes the DB engine on shutdown so connections are closed cleanly.
    """

    yield
    await dispose_engine()


app: FastAPI = FastAPI(title="Audit Tools Backend", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    """Simple health check endpoint."""

    return {"status": "ok"}


async def get_graphql_context(
    session: AsyncSession = Depends(get_async_session),
) -> GraphQLContext:
    """
    Provide Strawberry with a per-request context.

    FastAPI will create/cleanup the `AsyncSession` via `get_async_session()`.
    """

    return GraphQLContext(session=session)


graphql_router: GraphQLRouter = GraphQLRouter(
    schema,
    context_getter=get_graphql_context,
)

app.include_router(graphql_router, prefix="/graphql")

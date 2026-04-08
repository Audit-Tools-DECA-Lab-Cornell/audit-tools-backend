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

from app.auth import router as auth_router
from app.database import dispose_engines, get_async_session_playsafe, get_async_session_yee
from app.dashboard_router import router as dashboard_router
from app.schema import GraphQLContext, schema
from app.yee_router import router as yee_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """
    Application lifecycle handler.

    Disposes the DB engine on shutdown so connections are closed cleanly.
    """

    yield
    await dispose_engines()


app: FastAPI = FastAPI(title="Audit Tools Backend", version="0.1.0", lifespan=lifespan)

# Product-scoped REST routes (dummy auth for now).
app.include_router(auth_router, prefix="/yee")
app.include_router(auth_router, prefix="/playsafe")
app.include_router(dashboard_router, prefix="/yee")
app.include_router(yee_router)


@app.get("/health")
def health() -> dict[str, str]:
    """Simple health check endpoint."""

    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    """Root endpoint."""

    return {"status": "ok"}

def get_graphql_context_yee(
    session: AsyncSession = Depends(get_async_session_yee),
) -> GraphQLContext:
    """
    Provide Strawberry with a per-request context.

    FastAPI will create/cleanup the `AsyncSession` via `get_async_session_yee()`.
    """

    return GraphQLContext(session=session)

def get_graphql_context_playsafe(
    session: AsyncSession = Depends(get_async_session_playsafe),
) -> GraphQLContext:
    """
    Provide Strawberry with a per-request context.

    FastAPI will create/cleanup the `AsyncSession` via `get_async_session_playsafe()`.
    """

    return GraphQLContext(session=session)

yee_graphql_router: GraphQLRouter = GraphQLRouter(
    schema,
    context_getter=get_graphql_context_yee,
)
playsafe_graphql_router: GraphQLRouter = GraphQLRouter(
    schema,
    context_getter=get_graphql_context_playsafe,
)

app.include_router(yee_graphql_router, prefix="/yee/graphql")
app.include_router(playsafe_graphql_router, prefix="/playsafe/graphql")

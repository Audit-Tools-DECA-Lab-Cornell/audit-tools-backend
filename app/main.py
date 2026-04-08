"""
FastAPI application entrypoint.

This module initializes FastAPI and mounts both the shared-core routes from
`master` and the YEE-specific auth/dashboard/audit routes from the integration
branch so neither side of the merge is lost.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.ext.asyncio import AsyncSession
from strawberry.fastapi import GraphQLRouter

from app.auth import router as auth_router
from app.database import dispose_engines, get_async_session_playspace, get_async_session_yee
from app.dashboard_router import router as dashboard_router
from app.products.playspace.routes import router as playspace_shared_router
from app.products.yee.routes import router as yee_shared_router
from app.schema import GraphQLContext, schema
from app.yee_router import router as yee_router

origins = [
    "http://localhost:3000",
    "http://localhost:8000",
    "http://localhost:8081",
    "https://audit-tools-backend.onrender.com",
    "https://audit-tools-playsafe-frontend.vercel.app",
    "https://audit-tools-playspace-frontend.vercel.app",
]


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Dispose DB engines on shutdown."""

    yield
    await dispose_engines()


app: FastAPI = FastAPI(title="Audit Tools Backend", version="0.1.0", lifespan=lifespan)

# Real auth is now mounted for both product prefixes.
app.include_router(auth_router, prefix="/yee")
app.include_router(auth_router, prefix="/playspace")

# Shared-core product routes from master.
app.include_router(yee_shared_router, prefix="/yee")
app.include_router(playspace_shared_router, prefix="/playspace")

# YEE-specific dashboard and audit routes from the YEE branch.
app.include_router(dashboard_router, prefix="/yee")
app.include_router(yee_router)


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/")
def root() -> dict[str, str]:
    return {"status": "ok"}


def get_graphql_context_yee(
    session: AsyncSession = Depends(get_async_session_yee),
) -> GraphQLContext:
    return GraphQLContext(session=session)


def get_graphql_context_playspace(
    session: AsyncSession = Depends(get_async_session_playspace),
) -> GraphQLContext:
    return GraphQLContext(session=session)


yee_graphql_router: GraphQLRouter = GraphQLRouter(
    schema,
    context_getter=get_graphql_context_yee,
)
playspace_graphql_router: GraphQLRouter = GraphQLRouter(
    schema,
    context_getter=get_graphql_context_playspace,
)

app.include_router(yee_graphql_router, prefix="/yee/graphql")
app.include_router(playspace_graphql_router, prefix="/playspace/graphql")
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

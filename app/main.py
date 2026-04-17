"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware

from app.auth import router as auth_router
from app.dashboard_router import router as dashboard_router
from app.database import dispose_engines
from app.limiter import limiter
from app.notifications_router import router as notifications_router
from app.products.playspace.routes import router as playspace_router
from app.products.yee.routes import router as yee_shared_router
from app.yee_router import router as yee_router

# cors
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
    """
    Application lifecycle handler.

    Disposes the DB engine on shutdown so connections are closed cleanly.
    """

    yield
    await dispose_engines()


app: FastAPI = FastAPI(title="Audit Tools Backend", version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# Enforce @limiter.limit on routes (SlowAPI; must be registered before CORS so CORS stays outermost).
app.add_middleware(SlowAPIMiddleware)

# Product-scoped REST routes.
app.include_router(auth_router, prefix="/yee")
app.include_router(auth_router, prefix="/playspace")
app.include_router(yee_shared_router, prefix="/yee")
app.include_router(playspace_router, prefix="/playspace")
app.include_router(notifications_router, prefix="/playspace")
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


app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

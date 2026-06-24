"""FastAPI application entrypoint."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
import os

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler  # type: ignore[import-not-found]
from slowapi.errors import RateLimitExceeded  # type: ignore[import-not-found]
from slowapi.middleware import SlowAPIMiddleware  # type: ignore[import-not-found]

from app.auth import router as auth_router
from app.dashboard_router import router as dashboard_router
from app.database import (
	ACTIVE_DATABASE_ENVIRONMENT,
	ASYNC_SESSION_FACTORY_BY_PRODUCT,
	describe_active_database_targets,
	dispose_engines,
)
from app.db_urls import ProductKey
from app.demo_account_reconciler import reconcile_protected_yee_demo_accounts
from app.limiter import limiter
from app.notifications_router import router as notifications_router
from app.products.playspace.routes import router as playspace_router
from app.products.yee.routes import router as yee_shared_router
from app.yee_router import router as yee_router


def _resolve_cors_origins() -> list[str]:
	"""Resolve allowed browser origins for local and deployed frontends."""

	default_origins = [
		"http://localhost:3000",
		"http://localhost:8000",
		"http://localhost:8081",
		"https://audit-tools-backend.onrender.com",
		"https://copa-frontend.vercel.app",
		"https://copa-mobile.expo.app",
		"https://copa-tool.vercel.app",
	]
	configured_origins = os.getenv("CORS_ALLOWED_ORIGINS", "").strip()
	if not configured_origins:
		return default_origins

	extra_origins = [origin.strip() for origin in configured_origins.split(",") if origin.strip()]
	return list(dict.fromkeys([*default_origins, *extra_origins]))


origins = _resolve_cors_origins()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
	"""
	Application lifecycle handler.

	Disposes the DB engine on shutdown so connections are closed cleanly.
	"""

	targets = describe_active_database_targets()
	logger.info(
		"Database environment=%s yee=%s playspace=%s",
		ACTIVE_DATABASE_ENVIRONMENT.value,
		targets["yee"],
		targets["playspace"],
	)
	try:
		async with ASYNC_SESSION_FACTORY_BY_PRODUCT[ProductKey.YEE]() as session:
			reconciliation_summary = await reconcile_protected_yee_demo_accounts(session)
		logger.info("Protected YEE demo reconciliation summary=%s", reconciliation_summary)
	except Exception:
		logger.exception("Protected YEE demo reconciliation failed during startup.")
	yield
	await dispose_engines()


app: FastAPI = FastAPI(title="Audit Tools Backend", version="0.1.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)  # type: ignore[arg-type]

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
	allow_origin_regex=r"https://audit-tools-[\w-]+-cleverhugs\.vercel\.app",
	allow_methods=["*"],
	allow_headers=["*"],
)

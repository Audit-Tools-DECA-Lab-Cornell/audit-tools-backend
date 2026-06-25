"""YEE product route composition.

This package owns the YEE product's HTTP surface, mirroring the Playspace
layout (`app/products/playspace/routes/`). The composed `router` is mounted
under `/yee` in `app/main.py` and serves the audit lifecycle and instrument
slices. YEE manager/admin dashboard endpoints currently live in the shared
`app/dashboard_router.py` (also mounted under `/yee`); their business logic has
moved to `app/products/yee/services/dashboard.py`, and `routes/dashboard.py` is
the placeholder for relocating those route handlers here.
"""

from __future__ import annotations

from fastapi import APIRouter

from app.products.yee.routes.audits import router as audits_router
from app.products.yee.routes.dashboard import router as dashboard_router
from app.products.yee.routes.instrument import router as instrument_router

router = APIRouter(tags=["yee"])


@router.get("/status")
async def get_yee_status() -> dict[str, str]:
	"""Report that the YEE product namespace is mounted and isolated."""

	return {
		"status": "ok",
		"product": "yee",
		"message": "YEE product routes are mounted from app/products/yee/.",
	}


# Product sub-routers. `dashboard_router` is an empty placeholder for now (the
# YEE dashboard route handlers still live in the shared app/dashboard_router.py).
router.include_router(audits_router)
router.include_router(instrument_router)
router.include_router(dashboard_router)

"""
Role-specific dashboard route tree for Playspace.
"""

from fastapi import APIRouter

from app.products.playspace.routes.dashboard.admin import (
	router as admin_dashboard_router,
)
from app.products.playspace.routes.dashboard.auditor import (
	router as auditor_dashboard_router,
)
from app.products.playspace.routes.dashboard.manager import (
	router as manager_dashboard_router,
)

router = APIRouter()
router.include_router(auditor_dashboard_router)
router.include_router(manager_dashboard_router)
router.include_router(admin_dashboard_router)

__all__ = ["router"]

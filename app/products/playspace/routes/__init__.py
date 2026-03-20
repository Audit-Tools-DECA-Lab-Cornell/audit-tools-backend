"""
Playspace route package.
"""

from fastapi import APIRouter

from app.products.playspace.routes.assignments import router as assignments_router
from app.products.playspace.routes.audits import router as audits_router
from app.products.playspace.routes.dashboard import router as dashboard_router

######################################################################################
############################### Playspace Route Tree #################################
######################################################################################

router = APIRouter()
router.include_router(dashboard_router)
router.include_router(assignments_router)
router.include_router(audits_router)

__all__ = ["router"]

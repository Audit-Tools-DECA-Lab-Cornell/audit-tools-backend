"""
Playspace route package.
"""

from fastapi import APIRouter

from app.products.playspace.routes.assignments import router as assignments_router
from app.products.playspace.routes.audits import router as audits_router
from app.products.playspace.routes.bug_reports import router as bug_reports_router
from app.products.playspace.routes.dashboard import router as dashboard_router
from app.products.playspace.routes.exports import router as exports_router
from app.products.playspace.routes.instrument import router as instrument_router
from app.products.playspace.routes.management import router as management_router
from app.products.playspace.routes.me import router as me_router
from app.products.playspace.routes.release_policy import router as release_policy_router

######################################################################################
############################### Playspace Route Tree #################################
######################################################################################

router = APIRouter()
router.include_router(dashboard_router)
router.include_router(assignments_router)
router.include_router(audits_router)
router.include_router(instrument_router)
router.include_router(me_router)
router.include_router(management_router)
router.include_router(exports_router)
router.include_router(bug_reports_router)
router.include_router(release_policy_router)

__all__ = ["router"]

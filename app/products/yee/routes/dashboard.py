"""YEE-only manager/admin dashboard routes.

YEE-specific reporting, edit/re-submit, and manager-workflow handlers that move
out of the shared top-level `app/dashboard_router.py`. Thin HTTP layer over
`app.products.yee.services.dashboard`. Populated during Phase 2D; until then the
shared dashboard router serves these paths.
"""

from __future__ import annotations

from fastapi import APIRouter

router = APIRouter()

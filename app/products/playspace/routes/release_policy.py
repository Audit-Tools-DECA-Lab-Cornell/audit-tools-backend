from __future__ import annotations

from fastapi import APIRouter, Request, Response

from app.products.mobile_release_policy import MobileReleasePolicyResponse, get_mobile_release_policy
from app.products.mobile_release_webhooks import handle_eas_release_webhook

router = APIRouter(tags=["playspace-release-policy"])


@router.get("/mobile-release-policy", response_model=MobileReleasePolicyResponse)
async def get_playspace_mobile_release_policy() -> MobileReleasePolicyResponse:
	return await get_mobile_release_policy("playspace")


@router.post("/mobile-release-policy/eas-webhook", status_code=204)
async def capture_playspace_eas_release_webhook(request: Request) -> Response:
	return await handle_eas_release_webhook("playspace", request)

from __future__ import annotations

from fastapi import APIRouter

from app.products.mobile_release_policy import MobileReleasePolicyResponse, get_mobile_release_policy

router = APIRouter(tags=["yee-release-policy"])


@router.get("/mobile-release-policy", response_model=MobileReleasePolicyResponse)
async def get_yee_mobile_release_policy() -> MobileReleasePolicyResponse:
	return get_mobile_release_policy("yee")

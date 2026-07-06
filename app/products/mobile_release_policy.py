from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict

MobileProduct = Literal["playspace", "yee"]
MobilePlatform = Literal["android", "ios"]


class PlatformReleasePolicy(BaseModel):
	model_config = ConfigDict(frozen=True)

	latest_version: str
	minimum_supported_version: str
	latest_build: int | None = None
	minimum_supported_build: int | None = None
	update_url: str


class MobileReleasePolicyResponse(BaseModel):
	model_config = ConfigDict(frozen=True)

	product: MobileProduct
	message: str
	android: PlatformReleasePolicy
	ios: PlatformReleasePolicy


PLAYSPACE_RELEASE_POLICY = MobileReleasePolicyResponse(
	product="playspace",
	message="Install the latest COPA app to keep using field audits.",
	android=PlatformReleasePolicy(
		latest_version="0.6.0",
		minimum_supported_version="0.6.0",
		update_url="https://play.google.com/store/apps/details?id=com.pratyush.sudhakar.audittoolsplayspacemobile",
	),
	ios=PlatformReleasePolicy(
		latest_version="0.6.0",
		minimum_supported_version="0.6.0",
		update_url="https://apps.apple.com/",
	),
)

YEE_RELEASE_POLICY = MobileReleasePolicyResponse(
	product="yee",
	message="Install the latest YEE app to keep using field audits.",
	android=PlatformReleasePolicy(
		latest_version="0.6.3",
		minimum_supported_version="0.6.3",
		update_url="https://play.google.com/store/apps/details?id=com.andisha2004.audittoolsyeemobile",
	),
	ios=PlatformReleasePolicy(
		latest_version="0.6.3",
		minimum_supported_version="0.6.3",
		update_url="https://apps.apple.com/",
	),
)


def get_mobile_release_policy(product: MobileProduct) -> MobileReleasePolicyResponse:
	if product == "playspace":
		return PLAYSPACE_RELEASE_POLICY
	return YEE_RELEASE_POLICY

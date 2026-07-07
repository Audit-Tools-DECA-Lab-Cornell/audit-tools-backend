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

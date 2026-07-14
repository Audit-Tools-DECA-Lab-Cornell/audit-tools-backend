from __future__ import annotations

from app.products import mobile_release_sources as release_sources
from app.products.mobile_release_eas import get_eas_release
from app.products.mobile_release_models import (
	MobileProduct,
	MobileReleasePolicyResponse,
	PlatformReleasePolicy,
)

PLAYSPACE_RELEASE_POLICY = MobileReleasePolicyResponse(
	product="playspace",
	message="Install the latest COPA app to keep using field audits.",
	android=PlatformReleasePolicy(
		latest_version="0.6.4",
		minimum_supported_version="0.6.2",
		update_url="https://play.google.com/store/apps/details?id=com.pratyush.sudhakar.audittoolsplayspacemobile",
	),
	ios=PlatformReleasePolicy(
		latest_version="0.6.4",
		minimum_supported_version="0.6.2",
		update_url="https://apps.apple.com/",
	),
)

YEE_RELEASE_POLICY = MobileReleasePolicyResponse(
	product="yee",
	message="Install the latest YEE app to keep using field audits.",
	android=PlatformReleasePolicy(
		# 0.8.0 exists in git only — it was never published to the Play Store,
		# so the fleet's latest installable version is 0.7.3. Advertising an
		# unpublished version would point the in-app update gate at a store
		# listing that cannot satisfy it.
		latest_version="0.8.2",
		minimum_supported_version="0.8.0",
		update_url="https://play.google.com/store/apps/details?id=com.andisha2004.audittoolsyeemobile",
	),
	ios=PlatformReleasePolicy(
		latest_version="0.8.2",
		minimum_supported_version="0.8.0",
		update_url="https://apps.apple.com/",
	),
)


async def get_mobile_release_policy(product: MobileProduct) -> MobileReleasePolicyResponse:
	base_policy = _get_static_release_policy(product)
	google_release = await release_sources.fetch_google_play_release(product)
	github_release = await release_sources.fetch_github_release(product)
	android_policy = release_sources.resolve_platform_release_policy(
		base_policy=base_policy.android,
		google_release=google_release,
		eas_release=get_eas_release(product, "android"),
		github_release=github_release,
	)
	ios_policy = release_sources.resolve_platform_release_policy(
		base_policy=base_policy.ios,
		google_release=None,
		eas_release=get_eas_release(product, "ios"),
		github_release=github_release,
	)
	return base_policy.model_copy(update={"android": android_policy, "ios": ios_policy})


def _get_static_release_policy(product: MobileProduct) -> MobileReleasePolicyResponse:
	match product:
		case "playspace":
			return PLAYSPACE_RELEASE_POLICY
		case "yee":
			return YEE_RELEASE_POLICY
		case _ as unreachable:
			raise AssertionError(f"unknown mobile product: {unreachable}")

from __future__ import annotations

from dataclasses import dataclass
import os
from typing import Final

from app.products.mobile_release_models import MobileProduct

DEFAULT_GOOGLE_PLAY_TRACK: Final = "alpha"
DEFAULT_CACHE_TTL_SECONDS: Final = 900
GITHUB_TOKEN_ENV: Final = "MOBILE_RELEASE_POLICY_GITHUB_TOKEN"
GOOGLE_SERVICE_ACCOUNT_JSON_ENV: Final = "GOOGLE_PLAY_SERVICE_ACCOUNT_JSON"


@dataclass(frozen=True, slots=True)
class ProductReleaseConfig:
	product: MobileProduct
	android_package_name: str
	ios_bundle_identifier: str
	eas_project_id: str
	github_app_config_url: str
	google_play_track_env: str
	eas_webhook_secret_env: str

	def google_play_track(self) -> str:
		return env_value(self.google_play_track_env) or DEFAULT_GOOGLE_PLAY_TRACK

	def eas_webhook_secret(self) -> str | None:
		return env_value(self.eas_webhook_secret_env)


PRODUCT_RELEASE_CONFIGS: Final[dict[MobileProduct, ProductReleaseConfig]] = {
	"playspace": ProductReleaseConfig(
		product="playspace",
		android_package_name="com.pratyush.sudhakar.audittoolsplayspacemobile",
		ios_bundle_identifier="com.pratyush.sudhakar.audit-tools-playspace-mobile",
		eas_project_id="2e559376-25f3-44e1-88bf-00eeaf9fb763",
		github_app_config_url=(
			"https://raw.githubusercontent.com/Audit-Tools-DECA-Lab-Cornell/copa-mobile/master/app.config.js"
		),
		google_play_track_env="PLAYSPACE_GOOGLE_PLAY_TRACK",
		eas_webhook_secret_env="PLAYSPACE_EAS_WEBHOOK_SECRET",
	),
	"yee": ProductReleaseConfig(
		product="yee",
		android_package_name="com.andisha2004.audittoolsyeemobile",
		ios_bundle_identifier="com.andisha2004.audit-tools-yee-mobile",
		eas_project_id="34a0dc8b-bf74-4b5a-8d76-ac98418eccd3",
		github_app_config_url="https://raw.githubusercontent.com/audit-Tools-DECA-Lab-Cornell/yee-mobile/master/app.config.js",
		google_play_track_env="YEE_GOOGLE_PLAY_TRACK",
		eas_webhook_secret_env="YEE_EAS_WEBHOOK_SECRET",
	),
}


def get_product_release_config(product: MobileProduct) -> ProductReleaseConfig:
	match product:
		case "playspace":
			return PRODUCT_RELEASE_CONFIGS["playspace"]
		case "yee":
			return PRODUCT_RELEASE_CONFIGS["yee"]
		case _ as unreachable:
			raise AssertionError(f"unknown mobile product: {unreachable}")


def env_value(name: str) -> str | None:
	value = os.getenv(name)
	if value is None:
		return None
	stripped = value.strip()
	return stripped or None


def cache_ttl_seconds() -> int:
	raw_value = env_value("MOBILE_RELEASE_POLICY_CACHE_TTL_SECONDS")
	if raw_value is None:
		return DEFAULT_CACHE_TTL_SECONDS
	try:
		parsed = int(raw_value)
	except ValueError:
		return DEFAULT_CACHE_TTL_SECONDS
	return parsed if parsed > 0 else DEFAULT_CACHE_TTL_SECONDS

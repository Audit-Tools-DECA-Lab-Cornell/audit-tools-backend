from __future__ import annotations

import hashlib
import hmac
import json
from typing import TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.products.mobile_release_config import ProductReleaseConfig, get_product_release_config
from app.products.mobile_release_models import MobilePlatform, MobileProduct
from app.products.mobile_release_sources import MobileReleaseSnapshot

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

_EAS_RELEASE_CACHE: dict[tuple[MobileProduct, MobilePlatform], MobileReleaseSnapshot] = {}


class EasWebhookMetadata(BaseModel):
	model_config = ConfigDict(frozen=True)

	app_version: str | None = Field(default=None, alias="appVersion")
	app_build_version: str | None = Field(default=None, alias="appBuildVersion")
	distribution: str | None = None
	app_identifier: str | None = Field(default=None, alias="appIdentifier")


class EasWebhookPayload(BaseModel):
	model_config = ConfigDict(frozen=True)

	app_id: str | None = Field(default=None, alias="appId")
	platform: str | None = None
	status: str | None = None
	metadata: EasWebhookMetadata | None = None


def get_eas_release(product: MobileProduct, platform: MobilePlatform) -> MobileReleaseSnapshot | None:
	return _EAS_RELEASE_CACHE.get((product, platform))


def record_eas_webhook_payload(product: MobileProduct, raw_payload: str) -> MobileReleaseSnapshot | None:
	config = get_product_release_config(product)
	try:
		payload = EasWebhookPayload.model_validate(json.loads(raw_payload))
	except ValidationError:
		return None

	platform = _parse_eas_platform(payload.platform)
	if (
		payload.status != "finished"
		or payload.metadata is None
		or platform is None
		or payload.metadata.app_version is None
		or not _payload_matches_product(config, payload)
	):
		return None

	snapshot = MobileReleaseSnapshot(
		latest_version=payload.metadata.app_version,
		latest_build=_parse_build_number(payload.metadata.app_build_version),
		source="eas",
	)
	_EAS_RELEASE_CACHE[(product, platform)] = snapshot
	return snapshot


def clear_eas_release_cache() -> None:
	_EAS_RELEASE_CACHE.clear()


def verify_eas_webhook_signature(body: bytes, signature: str | None, secret: str) -> bool:
	if signature is None:
		return False
	expected = "sha1=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
	return hmac.compare_digest(expected, signature)


def _payload_matches_product(config: ProductReleaseConfig, payload: EasWebhookPayload) -> bool:
	metadata = payload.metadata
	if payload.app_id == config.eas_project_id:
		return True
	if metadata is None:
		return False
	return metadata.app_identifier in {config.android_package_name, config.ios_bundle_identifier}


def _parse_eas_platform(value: str | None) -> MobilePlatform | None:
	match value:
		case "android":
			return "android"
		case "ios":
			return "ios"
		case None:
			return None
		case _:
			return None


def _parse_build_number(value: str | None) -> int | None:
	if value is None:
		return None
	try:
		parsed = int(value)
	except ValueError:
		return None
	return parsed if parsed > 0 else None

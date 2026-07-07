from __future__ import annotations

import json
import logging
import re
from typing import Final, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, ValidationError
import requests

from app.products.mobile_release_config import GOOGLE_SERVICE_ACCOUNT_JSON_ENV, ProductReleaseConfig, env_value
from app.products.mobile_release_sources import MobileReleaseSnapshot

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonObject: TypeAlias = dict[str, JsonValue]

GOOGLE_PLAY_API_SCOPE: Final = "https://www.googleapis.com/auth/androidpublisher"
GOOGLE_PLAY_RELEASES_URL: Final = (
	"https://androidpublisher.googleapis.com/androidpublisher/v3/applications/{package_name}/tracks/{track}/releases"
)
PUBLISHED_GOOGLE_RELEASE_STATE: Final = "RELEASE_LIFECYCLE_STATE_PUBLISHED"
VERSION_RE: Final = re.compile(r"\b\d+\.\d+\.\d+(?:[-.][0-9A-Za-z]+)?\b")

logger = logging.getLogger(__name__)


class GoogleArtifactSummary(BaseModel):
	model_config = ConfigDict(frozen=True)

	version_code: int = Field(alias="versionCode")


class GoogleReleaseSummary(BaseModel):
	model_config = ConfigDict(frozen=True)

	release_name: str | None = Field(default=None, alias="releaseName")
	active_artifacts: tuple[GoogleArtifactSummary, ...] = Field(default=(), alias="activeArtifacts")
	release_lifecycle_state: str | None = Field(default=None, alias="releaseLifecycleState")


class GoogleReleasesResponse(BaseModel):
	model_config = ConfigDict(frozen=True)

	releases: tuple[GoogleReleaseSummary, ...] = ()


def fetch_google_play_release_sync(config: ProductReleaseConfig) -> MobileReleaseSnapshot | None:
	access_token = _get_google_access_token()
	if access_token is None:
		return None

	url = GOOGLE_PLAY_RELEASES_URL.format(
		package_name=config.android_package_name,
		track=config.google_play_track(),
	)
	try:
		response = requests.get(
			url,
			headers={"Authorization": f"Bearer {access_token}", "Accept": "application/json"},
			timeout=(3.05, 8.0),
		)
		response.raise_for_status()
	except requests.RequestException as exc:
		logger.warning("Google Play release lookup failed for %s: %s", config.product, exc)
		return None

	try:
		payload = response.json()
	except requests.JSONDecodeError as exc:
		logger.warning("Google Play release lookup returned invalid JSON for %s: %s", config.product, exc)
		return None

	if not isinstance(payload, dict):
		return None
	return parse_google_release_payload(payload)


def parse_google_release_payload(raw_payload: JsonObject) -> MobileReleaseSnapshot | None:
	try:
		payload = GoogleReleasesResponse.model_validate(raw_payload)
	except ValidationError:
		return None

	published_releases = [
		release for release in payload.releases if release.release_lifecycle_state == PUBLISHED_GOOGLE_RELEASE_STATE
	]
	artifact_candidates = [
		(release, artifact.version_code) for release in published_releases for artifact in release.active_artifacts
	]
	if not artifact_candidates:
		return None

	release, version_code = max(artifact_candidates, key=lambda item: item[1])
	return MobileReleaseSnapshot(
		latest_version=_extract_version_from_release_name(release.release_name),
		latest_build=version_code,
		source="google_play",
	)


def _get_google_access_token() -> str | None:
	service_account_json = env_value(GOOGLE_SERVICE_ACCOUNT_JSON_ENV)
	if service_account_json is None:
		return None

	try:
		from google.auth.exceptions import GoogleAuthError
		from google.auth.transport.requests import Request
		from google.oauth2 import service_account
	except ModuleNotFoundError:
		logger.warning("google-auth is not installed; Google Play release lookup is disabled.")
		return None

	try:
		service_account_info = json.loads(service_account_json)
		credentials = service_account.Credentials.from_service_account_info(
			service_account_info,
			scopes=[GOOGLE_PLAY_API_SCOPE],
		)
		credentials.refresh(Request())
	except (json.JSONDecodeError, ValueError, GoogleAuthError) as exc:
		logger.warning("Google Play service account token refresh failed: %s", exc)
		return None

	return credentials.token


def _extract_version_from_release_name(release_name: str | None) -> str | None:
	if release_name is None:
		return None
	match = VERSION_RE.search(release_name)
	if match is None:
		return None
	return match.group(0)

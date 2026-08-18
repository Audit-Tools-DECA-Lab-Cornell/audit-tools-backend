from __future__ import annotations

import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.products.mobile_release_models import MobileProduct
from app.products.mobile_release_policy import PlatformReleasePolicy
from app.products.mobile_release_sources import (
	MobileReleaseSnapshot,
	clear_eas_release_cache,
	parse_github_app_config_version,
	parse_google_release_payload,
	record_eas_webhook_payload,
	resolve_platform_release_policy,
	verify_eas_webhook_signature,
)


@pytest.fixture(autouse=True)
def clear_release_cache() -> None:
	clear_eas_release_cache()


def test_google_play_published_release_wins_for_android_closed_alpha() -> None:
	base_policy = PlatformReleasePolicy(
		latest_version="0.7.1",
		minimum_supported_version="0.7.1",
		update_url="https://play.google.com/store/apps/details?id=com.andisha2004.audittoolsyeemobile",
	)
	google_release = MobileReleaseSnapshot(latest_version="0.7.3", latest_build=203, source="google_play")
	eas_release = MobileReleaseSnapshot(latest_version="0.7.2", latest_build=202, source="eas")
	github_release = MobileReleaseSnapshot(latest_version="0.7.2", latest_build=None, source="github")

	resolved = resolve_platform_release_policy(
		base_policy=base_policy,
		google_release=google_release,
		eas_release=eas_release,
		github_release=github_release,
	)

	assert resolved.latest_version == "0.7.3"
	assert resolved.latest_build == 203
	assert resolved.minimum_supported_version == "0.7.1"


def test_resolver_fills_google_version_gap_from_eas_then_static() -> None:
	base_policy = PlatformReleasePolicy(
		latest_version="0.6.4",
		minimum_supported_version="0.6.2",
		update_url="https://play.google.com/store/apps/details?id=com.pratyush.sudhakar.audittoolsplayspacemobile",
	)
	google_release = MobileReleaseSnapshot(latest_version=None, latest_build=104, source="google_play")
	eas_release = MobileReleaseSnapshot(latest_version="0.6.5", latest_build=103, source="eas")

	resolved = resolve_platform_release_policy(
		base_policy=base_policy,
		google_release=google_release,
		eas_release=eas_release,
		github_release=None,
	)

	assert resolved.latest_version == "0.6.5"
	assert resolved.latest_build == 104


def test_parse_google_release_payload_ignores_unpublished_releases() -> None:
	snapshot = parse_google_release_payload(
		{
			"releases": [
				{
					"releaseName": "0.8.0",
					"releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_IN_REVIEW",
					"activeArtifacts": [{"versionCode": 800}],
				},
				{
					"releaseName": "0.7.4",
					"releaseLifecycleState": "RELEASE_LIFECYCLE_STATE_PUBLISHED",
					"activeArtifacts": [{"versionCode": 704}],
				},
			]
		}
	)

	assert snapshot == MobileReleaseSnapshot(latest_version="0.7.4", latest_build=704, source="google_play")


def test_parse_github_app_config_version_extracts_expo_version() -> None:
	version = parse_github_app_config_version('export default {expo: {name: "COPA", version: "0.6.4"},};')

	assert version == "0.6.4"


def test_record_eas_webhook_payload_caches_finished_store_build() -> None:
	payload = {
		"appId": "34a0dc8b-bf74-4b5a-8d76-ac98418eccd3",
		"platform": "android",
		"status": "finished",
		"metadata": {
			"appVersion": "0.7.5",
			"appBuildVersion": "205",
			"distribution": "store",
			"appIdentifier": "com.andisha2004.audittoolsyeemobile",
		},
	}

	snapshot = record_eas_webhook_payload("yee", json.dumps(payload))

	assert snapshot == MobileReleaseSnapshot(latest_version="0.7.5", latest_build=205, source="eas")


def test_eas_webhook_signature_uses_expo_hmac_sha1_header() -> None:
	body = json.dumps({"status": "finished"}).encode("utf-8")
	secret = "test-secret-at-least-sixteen"
	signature = "sha1=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()

	assert verify_eas_webhook_signature(body, signature, secret)
	assert not verify_eas_webhook_signature(body, "sha1=bad", secret)


def test_signed_eas_webhook_updates_yee_policy_route(monkeypatch: pytest.MonkeyPatch) -> None:
	async def empty_release(_: MobileProduct) -> None:
		return None

	secret = "test-secret-at-least-sixteen"
	monkeypatch.setenv("YEE_EAS_WEBHOOK_SECRET", secret)
	monkeypatch.setattr("app.products.mobile_release_sources.fetch_google_play_release", empty_release)
	monkeypatch.setattr("app.products.mobile_release_sources.fetch_github_release", empty_release)
	body = json.dumps(
		{
			"appId": "34a0dc8b-bf74-4b5a-8d76-ac98418eccd3",
			"platform": "android",
			"status": "finished",
			"metadata": {
				"appVersion": "0.7.6",
				"appBuildVersion": "206",
				"distribution": "store",
				"appIdentifier": "com.andisha2004.audittoolsyeemobile",
			},
		}
	).encode("utf-8")
	signature = "sha1=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha1).hexdigest()

	client = TestClient(app)
	webhook_response = client.post(
		"/yee/mobile-release-policy/eas-webhook",
		content=body,
		headers={"content-type": "application/json", "expo-signature": signature},
	)
	policy_response = client.get("/yee/mobile-release-policy")

	assert webhook_response.status_code == 204
	assert policy_response.json()["android"]["latest_version"] == "0.7.6"
	assert policy_response.json()["android"]["latest_build"] == 206

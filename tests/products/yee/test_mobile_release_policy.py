from __future__ import annotations

from fastapi.testclient import TestClient
import pytest

from app.main import app
from app.products import mobile_release_sources
from app.products.mobile_release_eas import clear_eas_release_cache
from app.products.mobile_release_models import MobileProduct


def test_yee_mobile_release_policy_is_public(monkeypatch: pytest.MonkeyPatch) -> None:
	async def empty_release(_: MobileProduct) -> None:
		return None

	monkeypatch.setattr(mobile_release_sources, "fetch_google_play_release", empty_release)
	monkeypatch.setattr(mobile_release_sources, "fetch_github_release", empty_release)
	clear_eas_release_cache()

	client = TestClient(app)
	response = client.get("/yee/mobile-release-policy")

	assert response.status_code == 200
	body = response.json()
	assert body["product"] == "yee"
	assert body["android"]["latest_version"] == "0.7.3"
	assert body["android"]["minimum_supported_version"] == "0.7.2"
	assert body["android"]["update_url"].startswith("https://play.google.com/")

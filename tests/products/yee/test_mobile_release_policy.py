from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app


def test_yee_mobile_release_policy_is_public() -> None:
	client = TestClient(app)
	response = client.get("/yee/mobile-release-policy")

	assert response.status_code == 200
	body = response.json()
	assert body["product"] == "yee"
	assert body["android"]["minimum_supported_version"] == "0.6.2"
	assert body["android"]["update_url"].startswith("https://play.google.com/")

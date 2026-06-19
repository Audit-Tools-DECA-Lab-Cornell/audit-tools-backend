from fastapi.testclient import TestClient
from httpx import Response

from app.main import app


client = TestClient(app)


def _preflight(origin: str) -> Response:
	return client.options(
		"/playspace/auth/me",
		headers={
			"Origin": origin,
			"Access-Control-Request-Method": "GET",
			"Access-Control-Request-Headers": "authorization,content-type",
		},
	)


def test_cors_allows_credentialed_web_frontend_origins() -> None:
	for origin in [
		"http://localhost:3000",
		"https://audit-tools-playspace-frontend.vercel.app",
		"https://copa-tool.vercel.app",
		"https://audit-tools-feature-cleverhugs.vercel.app",
	]:
		response = _preflight(origin)

		assert response.status_code == 200
		assert response.headers["access-control-allow-origin"] == origin
		assert response.headers["access-control-allow-credentials"] == "true"

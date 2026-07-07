from __future__ import annotations

import logging
import re

import requests

from app.products.mobile_release_config import GITHUB_TOKEN_ENV, ProductReleaseConfig, env_value
from app.products.mobile_release_sources import MobileReleaseSnapshot

logger = logging.getLogger(__name__)


def fetch_github_release_sync(config: ProductReleaseConfig) -> MobileReleaseSnapshot | None:
	headers = {"Accept": "text/plain"}
	token = env_value(GITHUB_TOKEN_ENV)
	if token is not None:
		headers["Authorization"] = f"Bearer {token}"

	try:
		response = requests.get(config.github_app_config_url, headers=headers, timeout=(3.05, 8.0))
		response.raise_for_status()
	except requests.RequestException as exc:
		logger.warning("GitHub app config lookup failed for %s: %s", config.product, exc)
		return None

	version = parse_github_app_config_version(response.text)
	if version is None:
		return None
	return MobileReleaseSnapshot(latest_version=version, latest_build=None, source="github")


def parse_github_app_config_version(raw_app_config: str) -> str | None:
	version_match = re.search(r"\bversion\s*:\s*[\"']([^\"']+)[\"']", raw_app_config)
	if version_match is None:
		return None
	return version_match.group(1)

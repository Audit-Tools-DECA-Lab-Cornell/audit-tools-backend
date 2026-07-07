from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Literal

import anyio

from app.products.mobile_release_config import cache_ttl_seconds, get_product_release_config
from app.products.mobile_release_models import MobileProduct, PlatformReleasePolicy

ReleaseSource = Literal["google_play", "eas", "github"]


@dataclass(frozen=True, slots=True)
class MobileReleaseSnapshot:
	latest_version: str | None
	latest_build: int | None
	source: ReleaseSource


@dataclass(frozen=True, slots=True)
class CachedReleaseSnapshot:
	snapshot: MobileReleaseSnapshot
	expires_at: float


_GOOGLE_PLAY_CACHE: dict[MobileProduct, CachedReleaseSnapshot] = {}
_GITHUB_CACHE: dict[MobileProduct, CachedReleaseSnapshot] = {}


def resolve_platform_release_policy(
	*,
	base_policy: PlatformReleasePolicy,
	google_release: MobileReleaseSnapshot | None,
	eas_release: MobileReleaseSnapshot | None,
	github_release: MobileReleaseSnapshot | None,
) -> PlatformReleasePolicy:
	version = _first_version(google_release, eas_release, github_release) or base_policy.latest_version
	build = _first_build(google_release, eas_release, github_release) or base_policy.latest_build
	return base_policy.model_copy(update={"latest_version": version, "latest_build": build})


async def fetch_google_play_release(product: MobileProduct) -> MobileReleaseSnapshot | None:
	from app.products.mobile_release_google import fetch_google_play_release_sync

	config = get_product_release_config(product)
	cached = _get_cached_snapshot(_GOOGLE_PLAY_CACHE, product)
	if cached is not None:
		return cached

	snapshot = await anyio.to_thread.run_sync(fetch_google_play_release_sync, config)
	if snapshot is not None:
		_GOOGLE_PLAY_CACHE[product] = CachedReleaseSnapshot(snapshot=snapshot, expires_at=_cache_expires_at())
	return snapshot


async def fetch_github_release(product: MobileProduct) -> MobileReleaseSnapshot | None:
	from app.products.mobile_release_github import fetch_github_release_sync

	config = get_product_release_config(product)
	cached = _get_cached_snapshot(_GITHUB_CACHE, product)
	if cached is not None:
		return cached

	snapshot = await anyio.to_thread.run_sync(fetch_github_release_sync, config)
	if snapshot is not None:
		_GITHUB_CACHE[product] = CachedReleaseSnapshot(snapshot=snapshot, expires_at=_cache_expires_at())
	return snapshot


def _first_version(*snapshots: MobileReleaseSnapshot | None) -> str | None:
	for snapshot in snapshots:
		if snapshot is not None and snapshot.latest_version is not None:
			return snapshot.latest_version
	return None


def _first_build(*snapshots: MobileReleaseSnapshot | None) -> int | None:
	for snapshot in snapshots:
		if snapshot is not None and snapshot.latest_build is not None:
			return snapshot.latest_build
	return None


def _get_cached_snapshot(
	cache: dict[MobileProduct, CachedReleaseSnapshot],
	product: MobileProduct,
) -> MobileReleaseSnapshot | None:
	cached = cache.get(product)
	if cached is None:
		return None
	if cached.expires_at <= time.monotonic():
		cache.pop(product, None)
		return None
	return cached.snapshot


def _cache_expires_at() -> float:
	return time.monotonic() + cache_ttl_seconds()


from app.products.mobile_release_eas import (  # noqa: E402
	clear_eas_release_cache,
	record_eas_webhook_payload,
	verify_eas_webhook_signature,
)
from app.products.mobile_release_github import parse_github_app_config_version  # noqa: E402
from app.products.mobile_release_google import parse_google_release_payload  # noqa: E402

__all__ = [
	"MobileReleaseSnapshot",
	"clear_eas_release_cache",
	"fetch_github_release",
	"fetch_google_play_release",
	"parse_github_app_config_version",
	"parse_google_release_payload",
	"record_eas_webhook_payload",
	"resolve_platform_release_policy",
	"verify_eas_webhook_signature",
]

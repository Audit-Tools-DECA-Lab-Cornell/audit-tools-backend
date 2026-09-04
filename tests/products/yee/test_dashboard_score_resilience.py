"""Regression coverage for manager lists containing unresolved score snapshots."""

from __future__ import annotations

import asyncio
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import YeeAuditSubmission
from app.seed import YEE_PROJECT_PILOT_ID, YEE_SUBMISSION_MARKET_ZERO_WEIGHT_ID
from tests.products.yee._helpers import SEED_MANAGER_EMAIL, SEED_PASSWORD, _bearer_headers


@pytest.fixture
def corrupt_historical_submission(
	yee_test_session_factory: async_sessionmaker[AsyncSession],
) -> Iterator[int]:
	"""Corrupt one snapshot, then restore it after each endpoint regression case."""

	async def corrupt() -> tuple[dict[str, object], str | None, str | None, int]:
		async with yee_test_session_factory() as session:
			submission = await session.get(YeeAuditSubmission, YEE_SUBMISSION_MARKET_ZERO_WEIGHT_ID)
			assert submission is not None
			original = (
				dict(submission.scores_json),
				submission.instrument_key,
				submission.instrument_version,
				submission.total_score,
			)
			# An invalid snapshot plus an unknown stamp must remain unresolved;
			# resolution must not fall forward to the active instrument.
			submission.scores_json = {"corrupt": True}
			submission.instrument_key = "yee"
			submission.instrument_version = "missing-manager-list-version"
			await session.commit()
			return original

	async def restore(
		scores_json: dict[str, object],
		instrument_key: str | None,
		instrument_version: str | None,
	) -> None:
		async with yee_test_session_factory() as session:
			submission = await session.get(YeeAuditSubmission, YEE_SUBMISSION_MARKET_ZERO_WEIGHT_ID)
			assert submission is not None
			submission.scores_json = scores_json
			submission.instrument_key = instrument_key
			submission.instrument_version = instrument_version
			await session.commit()

	original_scores, original_key, original_version, original_total = asyncio.run(corrupt())
	try:
		yield original_total
	finally:
		asyncio.run(restore(original_scores, original_key, original_version))


@pytest.mark.parametrize(
	("url", "collection_key"),
	[
		("/yee/dashboard/audits", None),
		("/yee/dashboard/overview", "latest_audits"),
		(f"/yee/dashboard/projects/{YEE_PROJECT_PILOT_ID}", "latest_audits"),
	],
	ids=["audit-list", "overview", "project-detail"],
)
def test_manager_lists_keep_unresolvable_submission_visible(
	yee_client: TestClient,
	corrupt_historical_submission: int,
	url: str,
	collection_key: str | None,
) -> None:
	"""A corrupt historical row degrades locally instead of failing a manager response."""

	login = yee_client.post(
		"/yee/auth/login",
		json={"email": SEED_MANAGER_EMAIL, "password": SEED_PASSWORD},
	)
	assert login.status_code == 200, login.text
	headers = _bearer_headers(login.json()["access_token"])

	response = yee_client.get(url, headers=headers)
	assert response.status_code == 200, response.text
	payload = response.json()
	rows = payload if collection_key is None else payload[collection_key]
	assert isinstance(rows, list)
	assert len(rows) > 1, "The response should retain unaffected manager-visible rows."

	row = next(
		(item for item in rows if item.get("submission_id") == str(YEE_SUBMISSION_MARKET_ZERO_WEIGHT_ID)),
		None,
	)
	assert row is not None, response.text
	assert row["score"] == corrupt_historical_submission
	assert row["total_raw_score"] == corrupt_historical_submission
	assert row["total_raw_maximum"] is None
	assert row["total_weighted_maximum"] is None

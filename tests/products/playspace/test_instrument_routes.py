import asyncio
import uuid
from typing import cast
from unittest.mock import create_autospec

import pytest

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.actors import CurrentUserContext, CurrentUserRole
from app.products.playspace.routes import instrument as instrument_routes
from app.products.playspace.schemas.instrument import PlayspaceInstrumentResponse


def _build_instrument_response(version: str, section_title: str) -> PlayspaceInstrumentResponse:
	"""Create a minimal valid instrument response for route tests."""

	return PlayspaceInstrumentResponse.model_validate(
		{
			"instrument_key": "pvua_v5_2",
			"instrument_name": f"COPA {version}",
			"instrument_version": version,
			"current_sheet": f"PVUA v{version}",
			"source_files": [],
			"preamble": [],
			"execution_modes": [],
			"pre_audit_questions": [],
			"scale_guidance": [],
			"sections": [
				{
					"section_key": "section_a",
					"title": section_title,
					"description": None,
					"instruction": "Instruction",
					"notes_prompt": None,
					"questions": [],
				}
			],
			"legal_documents": [],
		}
	)


def _build_current_user_context() -> CurrentUserContext:
	"""Create a typed current-user context for route unit tests."""

	return CurrentUserContext(
		role=CurrentUserRole.ADMIN,
		account_id=None,
		auditor_code=None,
	)


def _build_async_session() -> AsyncSession:
	"""Create a typed async-session stub for route unit tests."""

	return cast(AsyncSession, create_autospec(AsyncSession, instance=True))


def test_get_instrument_metadata_prefers_active_instrument(monkeypatch) -> None:
	"""`/playspace/instrument` should surface the latest active DB-backed instrument."""

	active_row = object()
	active_response = _build_instrument_response("5.13", "Active Section")
	canonical_response = _build_instrument_response("5.2", "Canonical Section")

	async def fake_get_active_instrument(_session: object, instrument_key: str) -> object:
		assert instrument_key == "pvua_v5_2"
		return active_row

	def fake_build_instrument_response_from_row(
		_instrument: object,
		*,
		lang: str = "en",
	) -> PlayspaceInstrumentResponse:
		assert lang == "en"
		return active_response

	monkeypatch.setattr(instrument_routes, "get_active_instrument", fake_get_active_instrument)
	monkeypatch.setattr(
		instrument_routes,
		"build_instrument_response_from_row",
		fake_build_instrument_response_from_row,
	)
	monkeypatch.setattr(instrument_routes, "get_canonical_instrument_response", lambda: canonical_response)

	response = asyncio.run(
		instrument_routes.get_instrument_metadata(
			lang="en",
			current_user=_build_current_user_context(),
			session=_build_async_session(),
		)
	)

	assert response.instrument_version == "5.13"
	assert response.instrument_name == "COPA 5.13"
	assert response.sections[0].title == "Active Section"


def test_get_instrument_metadata_falls_back_to_canonical_when_active_payload_invalid(
	monkeypatch,
) -> None:
	"""Invalid active rows should not replace the canonical fallback payload."""

	canonical_response = _build_instrument_response("5.2", "Canonical Section")

	async def fake_get_active_instrument(_session: object, instrument_key: str) -> object:
		assert instrument_key == "pvua_v5_2"
		return object()

	def fake_build_instrument_response_from_row(
		_instrument: object,
		*,
		lang: str = "en",
	) -> None:
		assert lang == "en"
		return None

	monkeypatch.setattr(instrument_routes, "get_active_instrument", fake_get_active_instrument)
	monkeypatch.setattr(
		instrument_routes,
		"build_instrument_response_from_row",
		fake_build_instrument_response_from_row,
	)
	monkeypatch.setattr(instrument_routes, "get_canonical_instrument_response", lambda: canonical_response)

	response = asyncio.run(
		instrument_routes.get_instrument_metadata(
			lang="en",
			current_user=_build_current_user_context(),
			session=_build_async_session(),
		)
	)

	assert response.instrument_version == "5.2"
	assert response.sections[0].title == "Canonical Section"


def test_admin_delete_instrument_deletes_inactive_version(monkeypatch) -> None:
	"""Inactive instrument versions can be removed from version history."""

	instrument_id = uuid.uuid4()

	async def fake_delete_instrument_version(_session: object, row_id: uuid.UUID) -> bool:
		assert row_id == instrument_id
		return True

	monkeypatch.setattr(instrument_routes, "delete_instrument_version", fake_delete_instrument_version)

	response = asyncio.run(
		instrument_routes.admin_delete_instrument(
			instrument_id=instrument_id,
			current_user=_build_current_user_context(),
			session=_build_async_session(),
		)
	)

	assert response.status_code == 204


def test_admin_delete_instrument_rejects_active_version(monkeypatch) -> None:
	"""Active instrument versions are protected from deletion."""

	async def fake_delete_instrument_version(_session: object, _row_id: uuid.UUID) -> bool:
		return False

	monkeypatch.setattr(instrument_routes, "delete_instrument_version", fake_delete_instrument_version)

	with pytest.raises(instrument_routes.HTTPException) as exc_info:
		asyncio.run(
			instrument_routes.admin_delete_instrument(
				instrument_id=uuid.uuid4(),
				current_user=_build_current_user_context(),
				session=_build_async_session(),
			)
		)

	assert exc_info.value.status_code == 409


def test_admin_delete_instrument_returns_404_for_missing_version(monkeypatch) -> None:
	"""Missing instrument IDs surface as 404 responses."""

	async def fake_delete_instrument_version(_session: object, _row_id: uuid.UUID) -> None:
		return None

	monkeypatch.setattr(instrument_routes, "delete_instrument_version", fake_delete_instrument_version)

	with pytest.raises(instrument_routes.HTTPException) as exc_info:
		asyncio.run(
			instrument_routes.admin_delete_instrument(
				instrument_id=uuid.uuid4(),
				current_user=_build_current_user_context(),
				session=_build_async_session(),
			)
		)

	assert exc_info.value.status_code == 404

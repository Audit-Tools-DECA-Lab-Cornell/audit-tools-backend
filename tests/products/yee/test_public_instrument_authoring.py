"""The public instrument payload always carries a logical view.

Clients used to receive only ``scoring_items`` for any version published before
authoring schema v2, so each one kept its own adapter for turning the matrix
into questions. Deriving the authoring document once, at read time, gives every
client the same answer.

The point of doing it at READ time is that the stored row never changes, so
scoring cannot move: ``scoring_contract_from_instrument`` resolves from stored
content and still returns the frozen schema-v1 contract. These tests pin both
halves — clients see authoring, and the database and scoring see exactly what
they saw before.

Pure: no database, no HTTP.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, Mock

import pytest

from app.products.yee.schemas.instrument import YeeInstrumentCreateRequest
from app.products.yee.services.instrument import (
	_create_yee_instrument_version,
	public_yee_instrument_payload,
	strip_derived_authoring,
)
from app.products.yee.services.scoring_resolution import scoring_contract_from_instrument
from app.products.yee.services.scoring_spec import SCHEMA_V1_SCORING_CONTRACT
from app.yee_instrument_schema import YeeInstrumentResponse

ACTIVE_PATH = Path(__file__).parents[3] / "app/products/yee/instruments/yee.active.instrument.json"


def _legacy_content() -> dict[str, Any]:
	content = json.loads(ACTIVE_PATH.read_text())
	content.pop("authoring", None)
	return content


def test_a_legacy_instrument_is_served_with_a_derived_authoring_document() -> None:
	# Given the shipped instrument with no authoring document
	content = _legacy_content()
	assert content.get("authoring") is None

	# When a client fetches it
	payload = public_yee_instrument_payload(content)

	# Then it arrives with the same logical view every other surface uses
	authoring = payload["authoring"]
	assert authoring is not None
	questions = [q for section in authoring["sections"] for q in section["questions"]]
	assert len(questions) == 54


def test_deriving_the_view_does_not_change_how_the_instrument_scores() -> None:
	# Given the stored (legacy) content and the payload derived from it
	content = _legacy_content()
	payload = public_yee_instrument_payload(content)

	# When each is resolved to a scoring contract
	stored = scoring_contract_from_instrument(YeeInstrumentResponse.model_validate(content))
	served = scoring_contract_from_instrument(YeeInstrumentResponse.model_validate(payload))

	# Then both are the frozen contract: the derivation is score-neutral, which is
	# what makes serving it safe for audits already taken under this version.
	assert stored.item_specs == SCHEMA_V1_SCORING_CONTRACT.item_specs
	assert served.item_specs == SCHEMA_V1_SCORING_CONTRACT.item_specs
	assert served.scoring_algorithm == stored.scoring_algorithm


def test_the_stored_content_is_never_mutated() -> None:
	# Given content captured before the call
	content = _legacy_content()
	before = json.dumps(content, sort_keys=True)

	# When it is served
	public_yee_instrument_payload(content)

	# Then the caller's dict is untouched: this is a view, not a migration
	assert json.dumps(content, sort_keys=True) == before


def test_content_that_already_has_authoring_is_left_alone() -> None:
	# Given content that already carries an authoring document
	content = json.loads(ACTIVE_PATH.read_text())
	served_once = public_yee_instrument_payload(content)

	# When it is served again from that output
	served_twice = public_yee_instrument_payload(served_once)

	# Then deriving is idempotent and never rewrites an authored document
	assert served_twice["authoring"] == served_once["authoring"]


def test_undeducible_content_is_still_served_rather_than_failing() -> None:
	# Given content whose scoring items cannot produce a logical view
	content = _legacy_content()
	content["scoring_items"] = []

	# When a client fetches it
	payload = public_yee_instrument_payload(content)

	# Then the request still succeeds. A client falls back to its own adapter;
	# a 500 would take the whole audit flow down instead.
	assert payload["scoring_items"] == []


def test_unusable_input_falls_back_to_the_shipped_snapshot() -> None:
	# Given a row that is missing or unreadable
	payload = public_yee_instrument_payload(None)

	# Then the shipped instrument is served, with its logical view attached
	assert payload["authoring"] is not None
	assert len(payload["scoring_items"]) > 0


def test_the_served_view_does_not_become_stored_data_when_posted_back() -> None:
	# Given the payload a client receives for a legacy instrument
	content = _legacy_content()
	served = public_yee_instrument_payload(content)
	assert served["authoring"] is not None

	# When an admin creates a new version from exactly what they fetched
	stored = strip_derived_authoring(served)

	# Then the derived view is dropped rather than persisted. Keeping it would
	# turn a read-time convenience into a schema change nobody authored — and,
	# because authoring-v2 activation requires a parent, would break the ordinary
	# create-from-current flow with parent_instrument_required.
	assert stored.get("authoring") is None


def test_an_authored_document_survives_the_same_path() -> None:
	# Given a served payload an admin actually edited
	served = public_yee_instrument_payload(_legacy_content())
	edited = dict(served)
	document = json.loads(json.dumps(served["authoring"]))
	document["sections"][0]["questions"][0]["prompt"] = "An edit an admin made"
	edited["authoring"] = document

	# When it is stored
	stored = strip_derived_authoring(edited)

	# Then it is kept, and with it the parent requirement it should trigger. Only
	# an exactly-derived document is dropped.
	assert stored["authoring"] == document


def test_content_that_never_had_authoring_is_returned_untouched() -> None:
	content = _legacy_content()
	assert strip_derived_authoring(content) is content


@pytest.mark.anyio
async def test_creating_a_version_from_the_served_payload_stores_no_authoring() -> None:
	"""Covers the wiring, not just the helper.

	This is the regression itself: an admin fetches the active instrument and
	posts it straight back to create the next version. Before the strip was wired
	into the create path, that stored a derived authoring document and the flow
	began failing with parent_instrument_required.
	"""

	# Given the exact payload a client receives, posted back unchanged
	served = public_yee_instrument_payload(_legacy_content())
	assert served["authoring"] is not None
	session = Mock()
	session.execute = AsyncMock(return_value=Mock(first=Mock(return_value=None)))
	session.commit = AsyncMock()
	session.refresh = AsyncMock()
	data = YeeInstrumentCreateRequest(instrument_version="from-served-payload", content=served)

	# When a version is created from it
	row = await _create_yee_instrument_version(session, data, activate=False)

	# Then the stored row carries no authoring the admin did not write
	assert row.content.get("authoring") is None
	assert row.is_active is False

import uuid

from app.products.playspace.services.instrument import (
	can_delete_instrument_version,
	next_draft_version,
	next_published_version,
)


def test_next_draft_version_starts_at_one() -> None:
	assert next_draft_version("5.23", ["5.23", "5.22"]) == "5.23.1"


def test_next_draft_version_increments_existing_sub_versions() -> None:
	existing = ["5.23", "5.23.1", "5.23.2", "5.24"]
	assert next_draft_version("5.23", existing) == "5.23.3"


def test_next_published_version_increments_highest_publication() -> None:
	assert next_published_version(["5.23"]) == "5.24"
	# The next publication is one above the HIGHEST existing publication, not the
	# order they are listed in — a rollback must not let a number be reused.
	assert next_published_version(["5.23", "5.21", "5.22"]) == "5.24"


def test_next_published_version_ignores_non_numeric_versions() -> None:
	# Non-numeric labels are skipped; the next number comes from the numeric max.
	assert next_published_version(["5.23", "rollback", "5.22"]) == "5.24"


def test_next_published_version_falls_back_when_no_numeric_publication() -> None:
	assert next_published_version([]) == "1.0"
	assert next_published_version(["draft", "wip"]) == "1.0"


def test_can_delete_instrument_version_rules() -> None:
	assert can_delete_instrument_version(is_active=True, parent_instrument_id=None, submission_count=0) is False
	assert can_delete_instrument_version(is_active=False, parent_instrument_id=None, submission_count=3) is False
	assert can_delete_instrument_version(is_active=False, parent_instrument_id=None, submission_count=0) is True
	assert can_delete_instrument_version(is_active=False, parent_instrument_id=uuid.uuid4(), submission_count=9) is True

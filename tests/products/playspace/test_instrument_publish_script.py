from __future__ import annotations

import importlib.util
import json
import sys
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from app.products.playspace.instrument import (
	INSTRUMENT_KEY,
	get_active_instrument_content,
	get_active_instrument_payload,
)

SCRIPTS_DIRECTORY = Path(__file__).parents[3] / "scripts"


@lru_cache(maxsize=1)
def _load_publish_script() -> ModuleType:
	"""Import the publish script by path; ``scripts/`` is not an importable package.

	The directory is put on ``sys.path`` only for the duration of the import,
	because the script reaches sideways to a sibling script, and removed again
	so the rest of the session resolves imports exactly as it would have.
	"""

	spec = importlib.util.spec_from_file_location(
		"publish_active_instrument_from_file",
		SCRIPTS_DIRECTORY / "publish_active_instrument_from_file.py",
	)
	if spec is None or spec.loader is None:
		raise AssertionError("Could not load the publish script.")

	module = importlib.util.module_from_spec(spec)
	scripts_path = str(SCRIPTS_DIRECTORY)
	added_scripts_path = scripts_path not in sys.path
	if added_scripts_path:
		sys.path.insert(0, scripts_path)
	try:
		spec.loader.exec_module(module)
	finally:
		if added_scripts_path and scripts_path in sys.path:
			sys.path.remove(scripts_path)
	return module


@pytest.fixture(scope="module")
def publish_script() -> ModuleType:
	"""Load the script lazily, so importing this file stays free of side effects."""

	return _load_publish_script()


def test_active_instrument_content_returns_every_locale() -> None:
	content = get_active_instrument_content()

	assert set(content) == {"en"}
	# The locale map must carry the same payload the runtime loader unwraps to,
	# so publishing the file cannot drift from what the API serves as fallback.
	assert content["en"] == get_active_instrument_payload()
	assert content["en"]["instrument_key"] == INSTRUMENT_KEY


def test_active_instrument_content_rejects_a_foreign_instrument_key(
	monkeypatch: pytest.MonkeyPatch,
	tmp_path: Path,
) -> None:
	# A locale payload for some other instrument would be published under this
	# key and silently become the active definition, so it must not load.
	foreign = tmp_path / "foreign.instrument.json"
	foreign.write_text(
		json.dumps({"en": {"instrument_key": "some_other_tool", "instrument_version": "1.0"}}),
		encoding="utf-8",
	)
	monkeypatch.setattr("app.products.playspace.instrument._ACTIVE_INSTRUMENT_PATH", foreign)
	get_active_instrument_content.cache_clear()

	try:
		with pytest.raises(ValueError, match=INSTRUMENT_KEY):
			get_active_instrument_content()
	finally:
		get_active_instrument_content.cache_clear()


@pytest.mark.parametrize(
	("content", "expected"),
	[
		({"en": {"instrument_key": "k"}, "fr": {"instrument_key": "k"}}, {"en", "fr"}),
		({"instrument_key": "k", "sections": []}, {"en"}),
		({"en": {"instrument_key": "k"}}, {"en"}),
		("not a mapping", set()),
	],
)
def test_locale_keys_reads_both_wrapped_and_bare_payloads(
	publish_script: ModuleType,
	content: Any,
	expected: set[str],
) -> None:
	assert publish_script._locale_keys(content) == expected


def test_locale_keys_drives_the_drop_guard(publish_script: ModuleType) -> None:
	"""Publication replaces content outright, so a narrower file must be refused.

	Going the other way - a file that adds a locale the live row lacks - is a
	normal translation rollout and must stay allowed.
	"""

	live_row = {"en": {"instrument_key": "k"}, "fr": {"instrument_key": "k"}}
	english_only_file = {"en": {"instrument_key": "k"}}

	assert publish_script._locale_keys(live_row) - publish_script._locale_keys(english_only_file) == {"fr"}
	assert publish_script._locale_keys(english_only_file) - publish_script._locale_keys(live_row) == set()


def test_version_stamps_are_ignored_when_comparing_content(publish_script: ModuleType) -> None:
	left = {"en": {"instrument_key": "k", "instrument_version": "5.33", "sections": []}}
	right = {"en": {"instrument_key": "k", "instrument_version": "5.32", "sections": []}}

	# Publication rewrites the embedded version, so identical content under two
	# stamps has to compare equal or re-running would publish forever.
	assert publish_script._without_version_stamps(left) == publish_script._without_version_stamps(right)
	assert left != right


def test_multiselect_sociability_counter_matches_the_canonical_file(publish_script: ModuleType) -> None:
	content = get_active_instrument_content()

	assert publish_script._count_multiselect_sociability(content) == 33
